from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Callable

from youtube_ready.branding import load_branding_profile
from youtube_ready.captions import render_plain_transcript, render_srt, render_vtt
from youtube_ready.chapters import render_chapters
from youtube_ready.dictionary import apply_corrections
from youtube_ready.metadata import generate_metadata
from youtube_ready.models import PublishingMetadata, ThumbnailPackage, ThumbnailVariantPackage, Transcript
from youtube_ready.render import render_variant
from youtube_ready.thumbnails import (
    extract_candidate_frames,
    generate_thumbnail_variants,
    render_thumbnail_ideas,
    select_top_frames,
    variants_to_legacy_package,
)
from youtube_ready.transcribe import transcribe_video

_SAFE_NAME = re.compile(r"[^A-Za-z0-9._-]+")


@dataclass(frozen=True)
class PipelineResult:
    output_dir: Path
    transcript: Transcript
    metadata: PublishingMetadata
    thumbnails: ThumbnailPackage | None = None
    thumbnail_variants: ThumbnailVariantPackage | None = None


def _session_dir(video_path: Path, output_root: str | Path) -> Path:
    root = Path(output_root).expanduser().resolve()
    if root == video_path or (root.exists() and not root.is_dir()):
        raise ValueError("output root must be a directory and must not be the source video")
    root.mkdir(parents=True, exist_ok=True)
    stem = _SAFE_NAME.sub("-", video_path.stem).strip("-._") or "video"
    base = root / f"{stem}-{datetime.now().strftime('%Y%m%d-%H%M%S')}"
    candidate = base
    suffix = 2
    while candidate.exists():
        candidate = Path(f"{base}-{suffix:02d}")
        suffix += 1
    candidate.mkdir()
    return candidate


def _render_titles(metadata: PublishingMetadata) -> str:
    return "\n\n".join(
        f"{index}. {item.title}\n   Why: {item.reason}" for index, item in enumerate(metadata.titles, start=1)
    ) + "\n"


def _render_package(metadata: PublishingMetadata, chapters_text: str, thumbnails_text: str | None = None) -> str:
    titles = "\n".join(f"{index}. {item.title}" for index, item in enumerate(metadata.titles, start=1))
    chapter_block = chapters_text.strip() or metadata.chapters_note or "No valid chapter list generated."
    hashtags = " ".join(metadata.hashtags) or "No hashtags generated."
    package = (
        "# YouTube publishing package\n\n## Suggested titles\n\n"
        f"{titles}\n\n## Description\n\n{metadata.description}\n\n"
        f"## Hashtags\n\n{hashtags}\n\n"
        f"## Chapters\n\n{chapter_block}\n"
    )
    if thumbnails_text:
        package += f"\n## Thumbnail concepts\n\n{thumbnails_text.strip()}\n"
    return package


def write_transcript_outputs(output_dir: Path, transcript: Transcript) -> None:
    """(Re)write transcript/caption files derived from a Transcript."""
    (output_dir / "transcript.json").write_text(transcript.model_dump_json(indent=2), encoding="utf-8")
    (output_dir / "transcript.txt").write_text(render_plain_transcript(transcript), encoding="utf-8")
    (output_dir / "captions.srt").write_text(render_srt(transcript), encoding="utf-8")
    (output_dir / "captions.vtt").write_text(render_vtt(transcript), encoding="utf-8")


def run_pipeline(
    input_path: str | Path,
    *,
    output_root: str | Path = "youtube-ready-output",
    quality: str = "balanced",
    whisper_model: str | None = None,
    language: str | None = None,
    translate: bool = False,
    metadata_model: str | None = "qwen2.5:7b-instruct",
    metadata_backend: str = "ollama",
    context: str | None = None,
    thumbnails: bool = True,
    on_progress: Callable[[str], None] | None = None,
) -> PipelineResult:
    progress = on_progress or (lambda _message: None)
    video_path = Path(input_path).expanduser().resolve()
    if not video_path.is_file():
        raise FileNotFoundError(f"video not found: {video_path}")
    output_dir = _session_dir(video_path, output_root)

    transcript = transcribe_video(
        video_path,
        quality=quality,
        model_name=whisper_model,
        language=language,
        translate=translate,
        on_progress=progress,
    )
    transcript = apply_corrections(transcript)
    progress("Writing complete transcript and YouTube caption files")
    write_transcript_outputs(output_dir, transcript)

    progress("Analysing the video and generating titles, description, hashtags, and chapters")
    metadata = generate_metadata(
        transcript,
        model=metadata_model,
        context=context,
        backend=metadata_backend,
    )
    chapters_text = render_chapters(metadata.chapters)
    description = metadata.description.strip()
    if metadata.hashtags:
        description = f"{description}\n\n{' '.join(metadata.hashtags)}"
    if chapters_text:
        description = f"{description}\n\nChapters\n{chapters_text.strip()}"
    (output_dir / "chapters.txt").write_text(chapters_text, encoding="utf-8")
    (output_dir / "description.txt").write_text(description + "\n", encoding="utf-8")
    (output_dir / "titles.txt").write_text(_render_titles(metadata), encoding="utf-8")
    (output_dir / "hashtags.txt").write_text((" ".join(metadata.hashtags) or "") + "\n", encoding="utf-8")
    (output_dir / "youtube_metadata.json").write_text(metadata.model_dump_json(indent=2), encoding="utf-8")

    thumbnail_package = None
    variant_package = None
    thumbnails_text = None
    if thumbnails:
        progress("Selecting thumbnail frames across the full runtime")
        chapter_timestamps = [chapter.start for chapter in metadata.chapters]
        candidate_frames = extract_candidate_frames(
            video_path, output_dir / "thumbnails", chapter_timestamps=chapter_timestamps
        )
        top_frames = select_top_frames(candidate_frames)

        branding = load_branding_profile()

        progress("Pairing titles with coherent thumbnail concepts")
        variant_package = generate_thumbnail_variants(
            transcript,
            top_frames,
            model=metadata_model,
            context=context,
            backend=metadata_backend,
            titles=metadata.titles,
            brief=metadata.packaging_brief,
            prohibited_phrases=branding.prohibited_phrases,
        )

        progress("Rendering title-thumbnail packages")
        for variant in variant_package.variants:
            rendered = render_variant(
                variant, output_dir / "thumbnails" / f"variant-{variant.strategy}.jpg", branding=branding
            )
            variant.rendered_path = rendered

        (output_dir / "thumbnail_variants.json").write_text(variant_package.model_dump_json(indent=2), encoding="utf-8")

        thumbnail_package = variants_to_legacy_package(variant_package)
        thumbnails_text = render_thumbnail_ideas(thumbnail_package)
        (output_dir / "thumbnail_ideas.txt").write_text(thumbnails_text, encoding="utf-8")
        (output_dir / "thumbnail_ideas.json").write_text(thumbnail_package.model_dump_json(indent=2), encoding="utf-8")

    (output_dir / "youtube_package.md").write_text(
        _render_package(metadata, chapters_text, thumbnails_text), encoding="utf-8"
    )

    progress("YouTube publishing package complete")
    return PipelineResult(
        output_dir=output_dir,
        transcript=transcript,
        metadata=metadata,
        thumbnails=thumbnail_package,
        thumbnail_variants=variant_package,
    )
