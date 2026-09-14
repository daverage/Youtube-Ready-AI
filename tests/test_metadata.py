from youtube_ready.chapters import chapters_are_valid
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

