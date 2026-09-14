from pathlib import Path

from youtube_ready.models import PublishingMetadata, ThumbnailVariantPackage, TitleSuggestion, Transcript, TranscriptSegment
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
    mocker.patch("youtube_ready.pipeline.extract_candidate_frames", return_value=[])
    mocker.patch(
        "youtube_ready.pipeline.generate_thumbnail_variants",
        return_value=ThumbnailVariantPackage(source="fallback", fallback_reason="No candidate thumbnail frames"),
    )

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
        "thumbnail_ideas.txt",
        "thumbnail_ideas.json",
        "thumbnail_variants.json",
    }
    assert expected == {path.name for path in result.output_dir.iterdir()}
    assert source.read_bytes() == b"video"
    assert "A complete test transcript." in (result.output_dir / "transcript.txt").read_text()
    assert result.thumbnails is not None
    assert result.thumbnails.source == "fallback"
    assert result.thumbnail_variants is not None
    assert result.thumbnail_variants.source == "fallback"

