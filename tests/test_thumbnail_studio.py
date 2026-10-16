import dataclasses
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
from youtube_ready.web.jobs import (
    Job,
    continue_thumbnail_render,
    generate_thumbnail_plan_for_job,
    regenerate_thumbnail_variant,
    remove_thumbnail_asset,
    update_thumbnail_variant,
    upload_thumbnail_asset,
    upload_thumbnail_frame,
)


def _frame(tmp_path: Path, name: str, timestamp: float) -> ThumbnailFrame:
    from PIL import Image

    path = tmp_path / name
    Image.new("RGB", (1280, 720), (60, 60, 60)).save(path)
    return ThumbnailFrame(timestamp=timestamp, path=str(path))


def _job(tmp_path: Path) -> Job:
    output_dir = tmp_path / "session"
    output_dir.mkdir()
    (output_dir / "source.mp4").write_bytes(b"video")
    (output_dir / "chapters.txt").write_text("", encoding="utf-8")
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
    job.video_path = output_dir / "source.mp4"
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


def test_update_thumbnail_variant_style_refreshes_assistant_plan(tmp_path: Path):
    job = _job(tmp_path)
    variant = update_thumbnail_variant(job, "v1", {"style_intent": "bold"})
    assert variant.style_intent == "bold"
    assert job.result.thumbnail_variants.style_intent == "bold"
    assert variant.composition is not None
    assert "Crop tightly" in variant.composition.crop_instruction
    assert variant.edit_prompt


def test_update_thumbnail_variant_waiting_review_does_not_render(tmp_path: Path):
    job = _job(tmp_path)
    job.status = "waiting_for_thumbnail_assets"
    variant = update_thumbnail_variant(job, "v1", {"style_intent": "bold"})
    assert variant.rendered_path is None
    assert variant.composition is not None
    assert "Crop tightly" in variant.composition.crop_instruction


def test_continue_thumbnail_render_sets_wait_event(tmp_path: Path):
    job = _job(tmp_path)
    job.status = "waiting_for_thumbnail_assets"
    continue_thumbnail_render(job)
    assert job._thumbnail_continue.is_set()


def test_generate_thumbnail_plan_for_job_extracts_only_on_manual_request(tmp_path: Path, monkeypatch):
    job = _job(tmp_path)
    job.result = dataclasses.replace(job.result, thumbnails=None, thumbnail_variants=None)
    extracted = []
    generated = {}

    monkeypatch.setattr("youtube_ready.web.jobs.extract_candidate_frames", lambda *args, **kwargs: extracted.append(args) or [])

    def fake_generate(*args, **kwargs):
        generated.update(kwargs)
        return ThumbnailVariantPackage(
            variants=[ThumbnailVariant(variant_id="manual", strategy="search", title="Manual", style_intent=kwargs["style_intent"])]
        )

    monkeypatch.setattr("youtube_ready.web.jobs.generate_thumbnail_variants", fake_generate)

    package = generate_thumbnail_plan_for_job(job, style_intent="bold")

    assert extracted
    assert generated["style_intent"] == "bold"
    assert job.status == "waiting_for_thumbnail_assets"
    assert job._thumbnail_plan_mode == "manual"
    assert package.variants[0].style_intent == "bold"
    assert (job.result.output_dir / "thumbnail_variants.json").is_file()


def test_continue_thumbnail_render_renders_manual_plan(tmp_path: Path):
    job = _job(tmp_path)
    job.status = "waiting_for_thumbnail_assets"
    job._thumbnail_plan_mode = "manual"

    continue_thumbnail_render(job)

    assert job.status == "done"
    assert job._thumbnail_plan_mode is None
    assert job.result.thumbnail_variants.variants[0].rendered_path is not None


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
        filename = "product-photo.jpg"
        file = buf

    variant = upload_thumbnail_frame(job, "v1", FakeUpload())
    assert variant.frame_path.endswith("v1.jpg")
    assert Path(variant.frame_path).is_file()
    assert variant.rendered_path is not None
    assert job.result.thumbnail_variants.uploaded_assets[0].role == "product"
    assert variant.source_assets["uploaded_assets"][0]["role"] == "product"
    serialized = job.as_dict()["thumbnail_variants"]["variants"][0]
    assert serialized["frame_filename"] == "uploads/v1.jpg"


def test_upload_thumbnail_asset_keeps_source_frame_and_refreshes_plan(tmp_path: Path):
    from PIL import Image

    job = _job(tmp_path)
    original_frame = job.result.thumbnail_variants.variants[0].frame_path
    buf = io.BytesIO()
    Image.new("RGBA", (500, 400), (220, 20, 20, 255)).save(buf, "PNG")
    buf.seek(0)

    class FakeUpload:
        filename = "product-cutout.png"
        file = buf

    variant = upload_thumbnail_asset(job, "v1", FakeUpload())
    assert variant.frame_path == original_frame
    assert job.result.thumbnail_variants.uploaded_assets[0].role == "product"
    assert variant.source_assets["uploaded_assets"][0]["role"] == "product"
    assert variant.composition is not None
    assert variant.rendered_path is not None


def _fake_upload(name: str, color: tuple[int, int, int]):
    from PIL import Image

    buf = io.BytesIO()
    Image.new("RGB", (800, 600), color).save(buf, "JPEG")
    buf.seek(0)

    class FakeUpload:
        filename = name
        file = buf

    return FakeUpload()


def test_remove_uploaded_photo_restores_original_frame(tmp_path: Path):
    job = _job(tmp_path)
    original = job.result.thumbnail_variants.variants[0]
    original_frame, original_ts = original.frame_path, original.frame_timestamp

    variant = upload_thumbnail_frame(job, "v1", _fake_upload("me.jpg", (10, 200, 10)))
    uploaded_path = Path(variant.frame_path)

    variant = remove_thumbnail_asset(job, "v1", uploaded_path.name)
    assert variant.frame_path == original_frame
    assert variant.frame_timestamp == original_ts
    assert not uploaded_path.exists()
    assert job.result.thumbnail_variants.uploaded_assets == []
    assert variant.source_assets["uploaded_assets"] == []
    assert variant.rendered_path is not None


def test_remove_supporting_asset_deletes_file_and_keeps_frame(tmp_path: Path):
    job = _job(tmp_path)
    frame = job.result.thumbnail_variants.variants[0].frame_path
    upload_thumbnail_asset(job, "v1", _fake_upload("product-photo.jpg", (200, 20, 20)))
    asset_path = Path(job.result.thumbnail_variants.uploaded_assets[0].path)

    variant = remove_thumbnail_asset(job, "v1", asset_path.name)
    assert not asset_path.exists()
    assert job.result.thumbnail_variants.uploaded_assets == []
    assert variant.frame_path == frame


def test_remove_unknown_asset_raises(tmp_path: Path):
    import pytest

    job = _job(tmp_path)
    with pytest.raises(KeyError):
        remove_thumbnail_asset(job, "v1", "nope.jpg")


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
