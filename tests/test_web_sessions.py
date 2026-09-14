import json
from pathlib import Path

import pytest

from youtube_ready.web.jobs import JOBS, delete_session, list_sessions, load_session


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
