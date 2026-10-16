from pathlib import Path

from youtube_ready.models import PublishingMetadata, ThumbnailVariant, ThumbnailVariantPackage, TitleSuggestion, Transcript, TranscriptSegment
from youtube_ready.pipeline import run_pipeline


def test_pipeline_writes_complete_package_without_touching_source(tmp_path: Path, mocker):
    source = tmp_path / "source.mp4"
    source.write_bytes(b"video")
    transcript = Transcript(
        language="en",
        duration=60,
        segments=[TranscriptSegment(start=0, end=60, text="A complete test transcript.")],
    )
    metadata = PublishingMetadata(
        titles=[TitleSuggestion(title="Test title", reason="Accurate")],
        description="Test description.",
        language="en",
    )
    mocker.patch("youtube_ready.pipeline.transcribe_video", return_value=transcript)
    mocker.patch("youtube_ready.pipeline.generate_metadata", return_value=metadata)
    frame_extractor = mocker.patch("youtube_ready.pipeline.extract_candidate_frames", return_value=[])
    thumbnail_generator = mocker.patch("youtube_ready.pipeline.generate_thumbnail_variants")

    result = run_pipeline(source, output_root=tmp_path / "output")

    expected = {
        "transcript.txt",
        "transcript.json",
        "captions.srt",
        "captions.vtt",
        "chapters.txt",
        "description.txt",
        "titles.txt",
        "hashtags.txt",
        "youtube_metadata.json",
        "youtube_package.md",
    }
    assert expected == {path.name for path in result.output_dir.iterdir()}
    assert source.read_bytes() == b"video"
    assert "A complete test transcript." in (result.output_dir / "transcript.txt").read_text()
    assert result.thumbnails is None
    assert result.thumbnail_variants is None
    frame_extractor.assert_not_called()
    thumbnail_generator.assert_not_called()


def test_pipeline_persists_thumbnail_plan_before_rendering(tmp_path: Path, mocker):
    source = tmp_path / "source.mp4"
    source.write_bytes(b"video")
    transcript = Transcript(
        language="en",
        duration=60,
        segments=[TranscriptSegment(start=0, end=60, text="A complete test transcript.")],
    )
    metadata = PublishingMetadata(
        titles=[TitleSuggestion(title="Test title", reason="Accurate")],
        description="Test description.",
        language="en",
    )
    package = ThumbnailVariantPackage(
        variants=[
            ThumbnailVariant(
                variant_id="v1",
                strategy="search",
                title="Test title",
                edit_prompt="Create a truthful thumbnail.",
            )
        ]
    )
    seen_pre_render_json = {}

    def fake_render(_variant, output_path, *, branding=None):
        variants_path = output_path.parent.parent / "thumbnail_variants.json"
        seen_pre_render_json.update({"text": variants_path.read_text(encoding="utf-8")})
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_bytes(b"jpg")
        return str(output_path)

    mocker.patch("youtube_ready.pipeline.transcribe_video", return_value=transcript)
    mocker.patch("youtube_ready.pipeline.generate_metadata", return_value=metadata)
    mocker.patch("youtube_ready.pipeline.extract_candidate_frames", return_value=[])
    mocker.patch("youtube_ready.pipeline.generate_thumbnail_variants", return_value=package)
    mocker.patch("youtube_ready.pipeline.render_variant", side_effect=fake_render)

    run_pipeline(source, output_root=tmp_path / "output", thumbnails=True)

    assert "Create a truthful thumbnail." in seen_pre_render_json["text"]


def test_pipeline_passes_thumbnail_style_intent(tmp_path: Path, mocker):
    source = tmp_path / "source.mp4"
    source.write_bytes(b"video")
    transcript = Transcript(
        language="en",
        duration=60,
        segments=[TranscriptSegment(start=0, end=60, text="A complete test transcript.")],
    )
    metadata = PublishingMetadata(
        titles=[TitleSuggestion(title="Test title", reason="Accurate")],
        description="Test description.",
        language="en",
    )
    generator = mocker.patch(
        "youtube_ready.pipeline.generate_thumbnail_variants",
        return_value=ThumbnailVariantPackage(source="fallback"),
    )
    mocker.patch("youtube_ready.pipeline.transcribe_video", return_value=transcript)
    mocker.patch("youtube_ready.pipeline.generate_metadata", return_value=metadata)
    mocker.patch("youtube_ready.pipeline.extract_candidate_frames", return_value=[])

    run_pipeline(source, output_root=tmp_path / "output", thumbnails=True, thumbnail_style_intent="bold")

    assert generator.call_args.kwargs["style_intent"] == "bold"
