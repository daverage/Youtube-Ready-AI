import dataclasses
import json
from pathlib import Path

import pytest

from youtube_ready.web import jobs
from youtube_ready.web.jobs import JOBS, delete_session, list_sessions, load_session
from youtube_ready.models import PublishingMetadata, ThumbnailVariant, ThumbnailVariantPackage, Transcript
from youtube_ready.pipeline import PipelineResult


def test_index_html_versions_static_assets():
    from fastapi.testclient import TestClient

    from youtube_ready.web.server import app

    client = TestClient(app)
    response = client.get("/")
    assert response.status_code == 200
    # Static assets must carry a cache-busting version so a restart after a code
    # change can't leave the browser silently serving a stale cached app.js.
    assert "/static/app.js?v=" in response.text
    assert "/static/styles.css?v=" in response.text


def test_start_ai_endpoint_returns_runtime_status(monkeypatch):
    from fastapi.testclient import TestClient

    from youtube_ready.web import server

    class FakeStatus:
        def as_dict(self):
            return {"backend": "ollama", "running": True, "message": "ready"}

    monkeypatch.setattr(server, "ensure_ai_runtime", lambda backend, model=None, start=True: FakeStatus())
    client = TestClient(server.app)

    response = client.post("/api/ai/start", json={"backend": "ollama", "model": "llama3"})

    assert response.status_code == 200
    assert response.json()["message"] == "ready"


def test_generate_thumbnail_variants_endpoint_uses_manual_trigger(tmp_path: Path, monkeypatch):
    from fastapi.testclient import TestClient

    from youtube_ready.web import server

    job = JOBS.create()
    job.status = "done"
    job.result = PipelineResult(
        output_dir=tmp_path,
        transcript=Transcript(duration=0, segments=[]),
        metadata=PublishingMetadata(source="fallback"),
    )

    def fake_generate(target_job, *, style_intent="balanced"):
        target_job.result = dataclasses.replace(
            target_job.result,
            thumbnail_variants=ThumbnailVariantPackage(
                variants=[ThumbnailVariant(variant_id="v1", strategy="search", style_intent=style_intent)]
            ),
        )

    monkeypatch.setattr(server, "generate_thumbnail_plan_for_job", fake_generate)
    client = TestClient(server.app)

    response = client.post(
        f"/api/jobs/{job.id}/thumbnail-variants/generate",
        json={"style_intent": "bold"},
    )

    assert response.status_code == 200
    assert response.json()["thumbnail_variants"]["variants"][0]["style_intent"] == "bold"


def _make_session(root: Path, name: str, title: str = "My Video") -> Path:
    session_dir = root / name
    session_dir.mkdir(parents=True)
    (session_dir / "transcript.json").write_text(
        json.dumps({"language": "en", "duration": 10, "segments": [{"start": 0, "end": 5, "text": "Hello", "words": []}]}),
        encoding="utf-8",
    )
    (session_dir / "youtube_metadata.json").write_text(
        json.dumps({"titles": [{"title": title, "reason": ""}], "description": "d", "hashtags": [], "chapters": []}),
        encoding="utf-8",
    )
    return session_dir


def test_list_sessions_finds_completed_runs(tmp_path: Path):
    _make_session(tmp_path, "video-a-20260101-000000", title="Video A")
    incomplete = tmp_path / "incomplete-run"
    incomplete.mkdir()  # no youtube_metadata.json — not a completed session

    sessions = list_sessions(tmp_path)
    assert len(sessions) == 1
    assert sessions[0]["name"] == "video-a-20260101-000000"
    assert sessions[0]["title"] == "Video A"


def test_load_session_reconstructs_job(tmp_path: Path):
    _make_session(tmp_path, "video-b-20260101-000000", title="Video B")

    job = load_session(tmp_path, "video-b-20260101-000000")
    assert job.status == "done"
    assert job.result is not None
    assert job.result.metadata.titles[0].title == "Video B"
    assert JOBS.get("video-b-20260101-000000") is job


def test_load_session_refreshes_thumbnail_assistant_fields(tmp_path: Path):
    session_dir = _make_session(tmp_path, "video-thumb-20260101-000000", title="Video Thumb")
    thumbnails_dir = session_dir / "thumbnails"
    thumbnails_dir.mkdir()
    frame_path = thumbnails_dir / "frame.jpg"
    from PIL import Image

    Image.new("RGB", (1280, 720), (80, 80, 80)).save(frame_path)
    (session_dir / "thumbnail_variants.json").write_text(
        json.dumps(
            {
                "schema_version": 2,
                "frames": [{"timestamp": 0, "path": str(frame_path)}],
                "variants": [
                    {
                        "variant_id": "v1",
                        "strategy": "search",
                        "title": "Video Thumb",
                        "frame_timestamp": 0,
                        "frame_path": str(frame_path),
                    }
                ],
                "source": "heuristic",
            }
        ),
        encoding="utf-8",
    )

    job = load_session(tmp_path, "video-thumb-20260101-000000")

    variant = job.result.thumbnail_variants.variants[0]
    assert variant.assessment is not None
    assert variant.composition is not None
    assert variant.edit_prompt
    persisted = json.loads((session_dir / "thumbnail_variants.json").read_text(encoding="utf-8"))
    assert persisted["variants"][0]["assessment"]


def test_load_session_with_unrendered_plans_can_still_be_rendered(tmp_path: Path):
    session_dir = _make_session(tmp_path, "video-unrendered-20260101-000000")
    thumbnails_dir = session_dir / "thumbnails"
    thumbnails_dir.mkdir()
    frame_path = thumbnails_dir / "frame.jpg"
    from PIL import Image

    Image.new("RGB", (1280, 720), (80, 80, 80)).save(frame_path)
    (session_dir / "thumbnail_variants.json").write_text(
        json.dumps(
            {
                "schema_version": 2,
                "frames": [{"timestamp": 0, "path": str(frame_path)}],
                "variants": [
                    {
                        "variant_id": "v1",
                        "strategy": "search",
                        "title": "Video",
                        "frame_timestamp": 0,
                        "frame_path": str(frame_path),
                    }
                ],
                "source": "llm",
            }
        ),
        encoding="utf-8",
    )

    job = load_session(tmp_path, "video-unrendered-20260101-000000")
    assert job.status == "waiting_for_thumbnail_assets"

    jobs.continue_thumbnail_render(job)
    assert job.status == "done"
    assert Path(job.result.thumbnail_variants.variants[0].rendered_path).is_file()


def test_load_session_starts_saved_ai_runtime(tmp_path: Path, monkeypatch):
    session_dir = _make_session(tmp_path, "video-ai-20260101-000000", title="Video AI")
    metadata = json.loads((session_dir / "youtube_metadata.json").read_text(encoding="utf-8"))
    metadata["model"] = "llama3"
    metadata["backend"] = "ollama"
    (session_dir / "youtube_metadata.json").write_text(json.dumps(metadata), encoding="utf-8")
    started = []

    class FakeStatus:
        def as_dict(self):
            return {}

    def fake_ensure(backend, model, start=True):
        started.append((backend, model, start))
        return FakeStatus()

    monkeypatch.setattr(jobs, "ensure_ai_runtime", fake_ensure)

    load_session(tmp_path, "video-ai-20260101-000000")

    assert started == [("ollama", "llama3", True)]


def test_load_session_exposes_saved_ai_runtime_status(tmp_path: Path, monkeypatch):
    session_dir = _make_session(tmp_path, "video-ai-status-20260101-000000", title="Video AI")
    metadata = json.loads((session_dir / "youtube_metadata.json").read_text(encoding="utf-8"))
    metadata["model"] = "llama3"
    metadata["backend"] = "ollama"
    (session_dir / "youtube_metadata.json").write_text(json.dumps(metadata), encoding="utf-8")

    class FakeStatus:
        def as_dict(self):
            return {"backend": "ollama", "model": "llama3", "running": True, "message": "Ollama started."}

    monkeypatch.setattr(jobs, "ensure_ai_runtime", lambda backend, model, start=True: FakeStatus())

    job = load_session(tmp_path, "video-ai-status-20260101-000000")

    assert job.as_dict()["ai_runtime"]["message"] == "Ollama started."


def test_load_session_rejects_path_traversal(tmp_path: Path):
    _make_session(tmp_path, "video-c-20260101-000000")
    with pytest.raises(FileNotFoundError):
        load_session(tmp_path, "../escaped")


def test_load_session_rejects_unknown_name(tmp_path: Path):
    with pytest.raises(FileNotFoundError):
        load_session(tmp_path, "does-not-exist")


def test_delete_session_removes_directory_and_cached_job(tmp_path: Path):
    _make_session(tmp_path, "video-d-20260101-000000")
    load_session(tmp_path, "video-d-20260101-000000")
    assert JOBS.get("video-d-20260101-000000") is not None

    delete_session(tmp_path, "video-d-20260101-000000")

    assert not (tmp_path / "video-d-20260101-000000").exists()
    assert JOBS.get("video-d-20260101-000000") is None
    assert list_sessions(tmp_path) == []
