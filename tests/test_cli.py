from pathlib import Path

from click.testing import CliRunner

from youtube_ready.cli import main
from youtube_ready.models import PublishingMetadata, Transcript
from youtube_ready.pipeline import PipelineResult


def test_cli_passes_thumbnail_style_to_pipeline(tmp_path: Path, monkeypatch):
    source = tmp_path / "video.mp4"
    source.write_bytes(b"video")
    seen = {}

    def fake_run_pipeline(input_path, **kwargs):
        seen.update(kwargs)
        return PipelineResult(
            output_dir=tmp_path / "out",
            transcript=Transcript(duration=0, segments=[]),
            metadata=PublishingMetadata(source="fallback"),
        )

    monkeypatch.setattr("youtube_ready.cli.run_pipeline", fake_run_pipeline)

    result = CliRunner().invoke(main, [str(source), "--thumbnails", "--thumbnail-style", "bold"])

    assert result.exit_code == 0
    assert seen["thumbnails"] is True
    assert seen["thumbnail_style_intent"] == "bold"


def test_cli_does_not_generate_thumbnails_by_default(tmp_path: Path, monkeypatch):
    source = tmp_path / "video.mp4"
    source.write_bytes(b"video")
    seen = {}

    def fake_run_pipeline(input_path, **kwargs):
        seen.update(kwargs)
        return PipelineResult(
            output_dir=tmp_path / "out",
            transcript=Transcript(duration=0, segments=[]),
            metadata=PublishingMetadata(source="fallback"),
        )

    monkeypatch.setattr("youtube_ready.cli.run_pipeline", fake_run_pipeline)

    result = CliRunner().invoke(main, [str(source)])

    assert result.exit_code == 0
    assert seen["thumbnails"] is False
