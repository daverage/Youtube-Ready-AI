import io
from pathlib import Path

from youtube_ready.branding import load_branding_profile, load_feedback, record_selection, save_branding_profile
from youtube_ready.models import (
    BrandingProfile,
    PublishingMetadata,
    ThumbnailFrame,
    ThumbnailVariant,
    ThumbnailVariantPackage,
    TitleSuggestion,
    Transcript,
    TranscriptSegment,
)
from youtube_ready.pipeline import PipelineResult
from youtube_ready.web.jobs import Job, regenerate_thumbnail_variant, update_thumbnail_variant, upload_thumbnail_frame


def _frame(tmp_path: Path, name: str, timestamp: float) -> ThumbnailFrame:
    from PIL import Image

    path = tmp_path / name
    Image.new("RGB", (1280, 720), (60, 60, 60)).save(path)
    return ThumbnailFrame(timestamp=timestamp, path=str(path))


def _job(tmp_path: Path) -> Job:
    output_dir = tmp_path / "session"
    output_dir.mkdir()
    frames = [_frame(tmp_path, "f0.jpg", 0.0), _frame(tmp_path, "f1.jpg", 5.0)]
    variant = ThumbnailVariant(
        variant_id="v1",
        strategy="search",
        title="Original Title",
        overlay_text="Original Overlay",
        frame_path=frames[0].path,
        frame_timestamp=0.0,
    )
    package = ThumbnailVariantPackage(frames=frames, variants=[variant], source="heuristic")
    metadata = PublishingMetadata(titles=[TitleSuggestion(title="Original Title", reason="x")], description="d")
    transcript = Transcript(duration=30, segments=[TranscriptSegment(start=0, end=10, text="hello world")])
    job = Job(id="job1", status="done")
    job.result = PipelineResult(
        output_dir=output_dir, transcript=transcript, metadata=metadata, thumbnail_variants=package
    )
    return job


def test_update_thumbnail_variant_edits_and_rerenders(tmp_path: Path):
    job = _job(tmp_path)
    variant = update_thumbnail_variant(job, "v1", {"title": "New Title", "overlay_text": "New Overlay"})
    assert variant.title == "New Title"
    assert variant.overlay_text == "New Overlay"
    assert variant.rendered_path is not None
    assert Path(variant.rendered_path).is_file()
    # persisted to disk
    assert (job.result.output_dir / "thumbnail_variants.json").is_file()
    assert (job.result.output_dir / "thumbnail_ideas.json").is_file()


def test_update_thumbnail_variant_swaps_frame(tmp_path: Path):
    job = _job(tmp_path)
    variant = update_thumbnail_variant(job, "v1", {"frame_filename": "f1.jpg"})
    assert variant.frame_timestamp == 5.0
    assert Path(variant.frame_path).name == "f1.jpg"


def test_update_thumbnail_variant_zoom_sets_explicit_crop(tmp_path: Path):
    job = _job(tmp_path)
    variant = update_thumbnail_variant(job, "v1", {"focal_x": 0.3, "focal_y": 0.4, "zoom": 0.5})
    assert variant.crop.width < 1.0
    assert variant.focal_x == 0.3


def test_regenerate_thumbnail_variant_heuristic(tmp_path: Path):
    job = _job(tmp_path)
    variant = regenerate_thumbnail_variant(job, "v1")
    assert variant.variant_id == "v1"
    assert variant.rendered_path is not None


def test_upload_thumbnail_frame(tmp_path: Path):
    from PIL import Image

    job = _job(tmp_path)
    buf = io.BytesIO()
    Image.new("RGB", (800, 600), (10, 200, 10)).save(buf, "JPEG")
    buf.seek(0)

    class FakeUpload:
        filename = "custom.jpg"
        file = buf

    variant = upload_thumbnail_frame(job, "v1", FakeUpload())
    assert variant.frame_path.endswith("v1.jpg")
    assert Path(variant.frame_path).is_file()
    assert variant.rendered_path is not None


def test_branding_profile_round_trip(tmp_path: Path):
    path = tmp_path / "branding.json"
    assert load_branding_profile(path) == BrandingProfile()
    profile = BrandingProfile(channel_name="Test Channel", prohibited_phrases=["100% guaranteed"])
    save_branding_profile(profile, path)
    assert load_branding_profile(path).channel_name == "Test Channel"


def test_record_selection_appends_feedback(tmp_path: Path):
    path = tmp_path / "feedback.json"
    record_selection(job_id="j1", variant_id="v1", strategy="search", title="T", path=path)
    record_selection(
        job_id="j1", variant_id="v2", strategy="browse", title="T2", youtube_test_result="Won A/B test", path=path
    )
    entries = load_feedback(path)
    assert len(entries) == 2
    assert entries[1].youtube_test_result == "Won A/B test"
