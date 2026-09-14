from __future__ import annotations

import sys

import click

from youtube_ready import __version__
from youtube_ready.pipeline import run_pipeline
from youtube_ready.transcribe import QUALITY_PRESETS


def _progress(message: str) -> None:
    click.echo(f"PROGRESS: {message}")
    sys.stdout.flush()


@click.command()
@click.version_option(version=__version__)
@click.argument("input_path", type=click.Path(exists=True, dir_okay=False, path_type=str))
@click.option(
    "--output-root",
    type=click.Path(file_okay=False, path_type=str),
    default="youtube-ready-output",
    show_default=True,
)
@click.option("--quality", type=click.Choice(sorted(QUALITY_PRESETS)), default="balanced", show_default=True)
@click.option("--whisper-model", default=None, help="Override the Whisper model name or local model path.")
@click.option("--language", default=None, help="Spoken language code, such as en; auto-detected by default.")
@click.option("--translate", is_flag=True, help="Translate speech to English while transcribing.")
@click.option("--metadata-model", default="qwen2.5:7b-instruct", show_default=True, help="Local model name for chapters/metadata.")
@click.option(
    "--metadata-backend",
    type=click.Choice(["ollama", "mlx"]),
    default="ollama",
    show_default=True,
    help="Local runtime to use for semantic chapters and metadata. 'mlx' runs a Hugging Face model "
    "in-process via Apple's MLX framework (Apple Silicon only).",
)
@click.option("--ai/--no-ai", default=True, help="Use a local model for semantic chapters and metadata.")
@click.option("--context", default=None, help="Optional topic, audience, or channel context for the metadata.")
@click.option("--thumbnails/--no-thumbnails", default=True, help="Extract candidate frames and propose thumbnail ideas.")
def main(
    input_path: str,
    output_root: str,
    quality: str,
    whisper_model: str | None,
    language: str | None,
    translate: bool,
    metadata_model: str,
    metadata_backend: str,
    ai: bool,
    context: str | None,
    thumbnails: bool,
) -> None:
    """Create a complete YouTube publishing package from INPUT_PATH."""
    try:
        result = run_pipeline(
            input_path,
            output_root=output_root,
            quality=quality,
            whisper_model=whisper_model,
            language=language,
            translate=translate,
            metadata_model=metadata_model if ai else None,
            metadata_backend=metadata_backend,
            context=context,
            thumbnails=thumbnails,
            on_progress=_progress,
        )
    except (FileNotFoundError, RuntimeError, ValueError, OSError) as exc:
        raise click.ClickException(str(exc)) from exc

    click.echo(f"Output: {result.output_dir}")
    click.echo(f"Transcript segments: {len(result.transcript.segments)}")
    click.echo(f"Metadata source: {result.metadata.source}")
    if result.metadata.fallback_reason:
        click.echo(f"Note: {result.metadata.fallback_reason}")
    if result.thumbnails:
        click.echo(f"Thumbnail ideas source: {result.thumbnails.source}")
        if result.thumbnails.fallback_reason:
            click.echo(f"Note: {result.thumbnails.fallback_reason}")


if __name__ == "__main__":
    main()

