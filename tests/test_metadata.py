import sys
import types

from youtube_ready.chapters import chapters_are_valid
from youtube_ready import metadata
from youtube_ready.metadata import generate_metadata, heuristic_metadata
from youtube_ready.models import Transcript, TranscriptSegment


def sample_transcript() -> Transcript:
    return Transcript(
        language="en",
        duration=120,
        segments=[
            TranscriptSegment(start=0, end=20, text="How to record better guitar audio."),
            TranscriptSegment(start=40, end=60, text="First set the input gain."),
            TranscriptSegment(start=80, end=110, text="Now compare the final recording."),
        ],
    )


def test_llm_metadata_is_validated_and_normalized():
    def fake_call(_model: str, _prompt: str) -> dict:
        return {
            "titles": [
                {"title": "Better Guitar Audio", "reason": "Clear benefit"},
                {"title": "Set Guitar Gain Correctly", "reason": "Specific"},
                {"title": "Record Cleaner Guitar", "reason": "Accurate"},
            ],
            "description": "Learn how to set gain and record cleaner guitar audio.",
            "hashtags": ["#GuitarRecording", "#guitar recording!", "#GuitarRecording", "Gain"],
            "chapters": [
                {"start": 0, "title": "Overview"},
                {"start": 40, "title": "Set input gain"},
                {"start": 80, "title": "Final comparison"},
            ],
        }

    result = generate_metadata(sample_transcript(), model="local-model", call=fake_call)
    assert result.source == "llm"
    assert len(result.titles) == 3
    assert chapters_are_valid(result.chapters, 120)
    # duplicates (case/punctuation-insensitive) are collapsed and tags are normalized to '#Word' form
    assert result.hashtags == ["#GuitarRecording", "#Gain"]
    # the packaging brief built alongside metadata is persisted for thumbnail generation to reuse
    assert result.packaging_brief is not None


def test_heuristic_metadata_includes_hashtags():
    result = heuristic_metadata(sample_transcript())
    assert result.hashtags
    assert all(tag.startswith("#") for tag in result.hashtags)


def test_metadata_falls_back_if_local_model_fails():
    def fail(_model: str, _prompt: str) -> dict:
        raise ConnectionError("offline")

    result = generate_metadata(sample_transcript(), model="local-model", call=fail)
    assert result.source == "fallback"
    assert result.fallback_reason
    assert chapters_are_valid(result.chapters, 120)


def test_mlx_loader_routes_gemma_model_to_vlm_without_importing_lm(monkeypatch):
    metadata._MLX_MODEL_CACHE.clear()
    used = []
    vlm_module = types.ModuleType("mlx_vlm")
    vlm_module.load = lambda model: used.append(model) or ("model", "processor")

    def fake_find_spec(name):
        if name == "mlx_vlm":
            return object()
        if name == "mlx_lm":
            raise AssertionError("mlx_lm should not be checked for a Gemma VLM model")
        return None

    monkeypatch.setattr(metadata, "find_spec", fake_find_spec)
    monkeypatch.setitem(sys.modules, "mlx_vlm", vlm_module)

    kind, bundle = metadata._load_mlx("mlx-community/gemma-4-e4b-it-4bit")

    assert kind == "vlm"
    assert bundle == ("model", "processor")
    assert used == ["mlx-community/gemma-4-e4b-it-4bit"]


def test_mlx_loader_reports_missing_selected_loader(monkeypatch):
    metadata._MLX_MODEL_CACHE.clear()
    monkeypatch.setattr(metadata, "find_spec", lambda _name: None)

    try:
        metadata._load_mlx("mlx-community/gemma-4-e4b-it-4bit")
    except RuntimeError as exc:
        message = str(exc)
    else:
        raise AssertionError("expected missing mlx-vlm to raise")

    assert "with mlx-vlm" in message
    assert "pip install -e '.[mlx]'" in message


def test_call_ollama_extracts_json_wrapped_in_prose_or_fences(monkeypatch):
    # Some local models wrap the JSON in markdown fences or a little commentary
    # even when asked for format="json" — _call_ollama used to hand the raw
    # text straight to json.loads and blow up on this instead of extracting
    # the actual object, forcing an unnecessary heuristic fallback.
    captured_options = {}

    class FakeOllama:
        @staticmethod
        def chat(*, model, messages, format, options=None):
            captured_options.update(options or {})
            return {"message": {"content": "Sure, here you go:\n```json\n{\"titles\": []}\n```\nHope that helps!"}}

    monkeypatch.setitem(sys.modules, "ollama", FakeOllama)

    result = metadata._call_ollama("local-model", "prompt")

    assert result == {"titles": []}
    # Also guards against truncated-JSON failures by asking for enough tokens.
    assert captured_options.get("num_predict", 0) >= 2048
