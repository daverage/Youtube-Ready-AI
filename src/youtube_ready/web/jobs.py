from __future__ import annotations

import dataclasses
import json
import queue
import shutil
import tempfile
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path

from youtube_ready.branding import load_branding_profile
from youtube_ready.models import (
    CropBox,
    PublishingMetadata,
    TextLayout,
    ThumbnailPackage,
    ThumbnailVariant,
    ThumbnailVariantPackage,
    Transcript,
)
from youtube_ready.pipeline import PipelineResult, run_pipeline
from youtube_ready.render import compute_crop_box, render_variant, resolve_frame_path
from youtube_ready.thumbnails import (
    generate_thumbnail_variants,
    migrate_legacy_package,
    render_thumbnail_ideas,
    validate_variant,
    variants_to_legacy_package,
)

UPLOAD_DIR = Path(tempfile.gettempdir()) / "youtube-ready-uploads"

# faster-whisper and Ollama are both heavy on CPU/GPU; run one video at a time
# so a second upload queues instead of starving the first.
_EXECUTOR = ThreadPoolExecutor(max_workers=1, thread_name_prefix="youtube-ready-job")


@dataclass
class Job:
    id: str
    status: str = "queued"
    error: str | None = None
    result: PipelineResult | None = None
    video_path: Path | None = None
    _messages: list[str] = field(default_factory=list)
    _subscribers: list["queue.Queue[str]"] = field(default_factory=list)
    _lock: threading.Lock = field(default_factory=threading.Lock)

    def emit(self, event: dict) -> None:
        message = json.dumps(event)
        with self._lock:
            self._messages.append(message)
            for subscriber in self._subscribers:
                subscriber.put(message)

    def subscribe(self) -> "queue.Queue[str]":
        subscriber: "queue.Queue[str]" = queue.Queue()
        with self._lock:
            for message in self._messages:
                subscriber.put(message)
            self._subscribers.append(subscriber)
        return subscriber

    def unsubscribe(self, subscriber: "queue.Queue[str]") -> None:
        with self._lock:
            if subscriber in self._subscribers:
                self._subscribers.remove(subscriber)

    def as_dict(self) -> dict:
        files = []
        output_dir = None
        thumbnails = None
        thumbnail_variants = None
        if self.result is not None:
            output_dir = str(self.result.output_dir)
            files = sorted(
                p.name
                for p in self.result.output_dir.iterdir()
                if p.is_file() and p != self.video_path
            )
            if self.result.thumbnails is not None:
                thumbnails = {
                    "source": self.result.thumbnails.source,
                    "fallback_reason": self.result.thumbnails.fallback_reason,
                    "ideas": [
                        {
                            "concept": idea.concept,
                            "overlay_text": idea.overlay_text,
                            "visual_description": idea.visual_description,
                            "image_prompt": idea.image_prompt,
                            "frame_filename": Path(idea.frame_path).name if idea.frame_path else None,
                        }
                        for idea in self.result.thumbnails.ideas
                    ],
                }
            if self.result.thumbnail_variants is not None:
                thumbnail_variants = _variant_package_dict(self.result.thumbnail_variants)
        return {
            "id": self.id,
            "status": self.status,
            "error": self.error,
            "output_dir": output_dir,
            "files": files,
            "thumbnails": thumbnails,
            "thumbnail_variants": thumbnail_variants,
        }


class JobStore:
    def __init__(self) -> None:
        self._jobs: dict[str, Job] = {}
        self._lock = threading.Lock()

    def create(self) -> Job:
        job = Job(id=uuid.uuid4().hex[:12])
        with self._lock:
            self._jobs[job.id] = job
        return job

    def get(self, job_id: str) -> Job | None:
        with self._lock:
            return self._jobs.get(job_id)

    def put(self, job: Job) -> None:
        with self._lock:
            self._jobs[job.id] = job

    def drop(self, job_id: str) -> None:
        with self._lock:
            self._jobs.pop(job_id, None)


JOBS = JobStore()


# --- Session management (past runs on disk) --------------------------------
#
# A "session" is just a previous run's output directory — its name (e.g.
# my-video-20260914-131826) doubles as the job id once loaded, so the existing
# /api/jobs/{id}/... endpoints work unmodified for a reloaded session.

_DONE_MARKER = "youtube_metadata.json"


def _is_session_dir(path: Path) -> bool:
    return path.is_dir() and (path / _DONE_MARKER).is_file()


def _resolve_session_dir(root: str | Path, name: str) -> Path:
    """Resolve `name` to a session directory that is a direct child of `root`,
    rejecting anything that would escape it (e.g. `../..`, an absolute path)."""
    root_resolved = Path(root).expanduser().resolve()
    candidate = (root_resolved / Path(name).name).resolve()
    if candidate.parent != root_resolved or not _is_session_dir(candidate):
        raise FileNotFoundError(f"unknown session {name!r}")
    return candidate


def list_sessions(root: str | Path) -> list[dict]:
    root_path = Path(root).expanduser()
    if not root_path.is_dir():
        return []
    sessions = []
    for entry in root_path.iterdir():
        if not _is_session_dir(entry):
            continue
        title = entry.name
        try:
            metadata = json.loads((entry / _DONE_MARKER).read_text(encoding="utf-8"))
            titles = metadata.get("titles") or []
            if titles and titles[0].get("title"):
                title = titles[0]["title"]
        except (OSError, ValueError):
            pass
        sessions.append(
            {
                "name": entry.name,
                "title": title,
                "created": entry.stat().st_mtime,
                "has_video": any(entry.glob("source.*")),
            }
        )
    sessions.sort(key=lambda item: item["created"], reverse=True)
    return sessions


def load_session(root: str | Path, name: str) -> Job:
    """Reconstruct a Job from a previous run's output directory so it can be
    browsed again through the normal job endpoints."""
    session_dir = _resolve_session_dir(root, name)
    existing = JOBS.get(session_dir.name)
    if existing is not None and existing.result is not None:
        return existing

    try:
        transcript = Transcript.model_validate_json((session_dir / "transcript.json").read_text(encoding="utf-8"))
        metadata = PublishingMetadata.model_validate_json((session_dir / _DONE_MARKER).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise FileNotFoundError(f"session {name!r} is missing or has corrupt output files") from exc

    thumbnails = None
    thumbnails_path = session_dir / "thumbnail_ideas.json"
    if thumbnails_path.is_file():
        try:
            thumbnails = ThumbnailPackage.model_validate_json(thumbnails_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            thumbnails = None

    thumbnail_variants = None
    variants_path = session_dir / "thumbnail_variants.json"
    if variants_path.is_file():
        try:
            thumbnail_variants = ThumbnailVariantPackage.model_validate_json(variants_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            thumbnail_variants = None
    elif thumbnails is not None and thumbnails.ideas:
        # Session predates the versioned variant schema: migrate on the fly so the
        # Thumbnail Studio tab still shows up, and persist it so this only happens once.
        thumbnail_variants = migrate_legacy_package(thumbnails, metadata.titles)
        try:
            variants_path.write_text(thumbnail_variants.model_dump_json(indent=2), encoding="utf-8")
        except OSError:
            pass

    video_path = next(session_dir.glob("source.*"), None)

    job = Job(id=session_dir.name, status="done")
    job.result = PipelineResult(
        output_dir=session_dir,
        transcript=transcript,
        metadata=metadata,
        thumbnails=thumbnails,
        thumbnail_variants=thumbnail_variants,
    )
    job.video_path = video_path
    JOBS.put(job)
    return job


def delete_session(root: str | Path, name: str) -> None:
    session_dir = _resolve_session_dir(root, name)
    shutil.rmtree(session_dir)
    JOBS.drop(session_dir.name)


# --- Thumbnail Studio: editing, regenerating and rendering variants --------


def _variant_package_dict(package: ThumbnailVariantPackage) -> dict:
    return {
        "schema_version": package.schema_version,
        "source": package.source,
        "model": package.model,
        "backend": package.backend,
        "fallback_reason": package.fallback_reason,
        "frames": [
            {
                "timestamp": frame.timestamp,
                "filename": Path(frame.path).name if frame.path else None,
                "preview_filename": f"previews/{Path(frame.preview_path).name}" if frame.preview_path else None,
                "is_duplicate": frame.is_duplicate,
                "has_face": frame.has_face,
                "source": frame.source,
            }
            for frame in package.frames
        ],
        "variants": [_variant_dict(v) for v in package.variants],
    }


def _variant_dict(variant: ThumbnailVariant) -> dict:
    return {
        "variant_id": variant.variant_id,
        "strategy": variant.strategy,
        "title": variant.title,
        "overlay_text": variant.overlay_text,
        "visual_rationale": variant.visual_rationale,
        "audience_promise": variant.audience_promise,
        "frame_timestamp": variant.frame_timestamp,
        "frame_filename": Path(variant.frame_path).name if variant.frame_path else None,
        "crop": variant.crop.model_dump(),
        "focal_x": variant.focal_x,
        "focal_y": variant.focal_y,
        "text": variant.text.model_dump(),
        "rendered_filename": Path(variant.rendered_path).name if variant.rendered_path else None,
        "visual_verified": variant.visual_verified,
        "source": variant.source,
        "model": variant.model,
        "backend": variant.backend,
        "fallback_reason": variant.fallback_reason,
        "warnings": variant.warnings,
    }


def _require_variants(job: Job) -> ThumbnailVariantPackage:
    if job.result is None or job.result.thumbnail_variants is None:
        raise ValueError("this job has no thumbnail variants (thumbnails may have been disabled for this run)")
    return job.result.thumbnail_variants


def _find_variant(package: ThumbnailVariantPackage, variant_id: str) -> ThumbnailVariant:
    for variant in package.variants:
        if variant.variant_id == variant_id:
            return variant
    raise KeyError(f"unknown thumbnail variant {variant_id!r}")


def _persist_variant_package(job: Job, package: ThumbnailVariantPackage) -> None:
    assert job.result is not None
    output_dir = job.result.output_dir
    (output_dir / "thumbnail_variants.json").write_text(package.model_dump_json(indent=2), encoding="utf-8")
    legacy = variants_to_legacy_package(package)
    (output_dir / "thumbnail_ideas.json").write_text(legacy.model_dump_json(indent=2), encoding="utf-8")
    (output_dir / "thumbnail_ideas.txt").write_text(render_thumbnail_ideas(legacy), encoding="utf-8")
    job.result = dataclasses.replace(job.result, thumbnails=legacy, thumbnail_variants=package)


def _rerender(job: Job, variant: ThumbnailVariant) -> None:
    assert job.result is not None
    branding = load_branding_profile()
    output_path = job.result.output_dir / "thumbnails" / f"variant-{variant.strategy}.jpg"
    variant.rendered_path = render_variant(variant, output_path, branding=branding)
    variant.warnings = validate_variant(
        variant,
        job.result.metadata.packaging_brief,
        transcript=job.result.transcript,
        prohibited_phrases=branding.prohibited_phrases,
    )


def update_thumbnail_variant(job: Job, variant_id: str, updates: dict) -> ThumbnailVariant:
    """Apply creator edits (title, overlay, frame, crop/focal point, text layout),
    re-render deterministically, and persist. Any subset of fields may be supplied."""
    package = _require_variants(job)
    variant = _find_variant(package, variant_id)
    frames_by_name = {Path(f.path).name: f for f in package.frames if f.path}

    if "title" in updates:
        variant.title = str(updates["title"])
    if "overlay_text" in updates:
        variant.overlay_text = str(updates["overlay_text"])
    if "visual_rationale" in updates:
        variant.visual_rationale = str(updates["visual_rationale"])
    if "audience_promise" in updates:
        variant.audience_promise = str(updates["audience_promise"])
    if "frame_filename" in updates:
        chosen = frames_by_name.get(updates["frame_filename"])
        if chosen is None:
            raise KeyError(f"unknown candidate frame {updates['frame_filename']!r}")
        variant.frame_path = chosen.path
        variant.frame_timestamp = chosen.timestamp
        variant.crop = CropBox()
    if "text" in updates and isinstance(updates["text"], dict):
        variant.text = TextLayout.model_validate({**variant.text.model_dump(), **updates["text"]})

    focal_x = updates.get("focal_x", variant.focal_x)
    focal_y = updates.get("focal_y", variant.focal_y)
    zoom = updates.get("zoom")
    if "focal_x" in updates or "focal_y" in updates or zoom is not None:
        variant.focal_x, variant.focal_y = float(focal_x), float(focal_y)
        output_path = job.result.output_dir / "thumbnails" / f"variant-{variant.strategy}.jpg"
        resolved_frame_path = resolve_frame_path(variant.frame_path, output_path)
        if zoom is not None and resolved_frame_path and resolved_frame_path.is_file():
            from PIL import Image

            with Image.open(resolved_frame_path) as image:
                variant.crop = compute_crop_box(image.size, variant.focal_x, variant.focal_y, float(zoom))
        else:
            variant.crop = CropBox()

    _rerender(job, variant)
    _persist_variant_package(job, package)
    return variant


def regenerate_thumbnail_variant(job: Job, variant_id: str) -> ThumbnailVariant:
    """Re-run generation for every strategy using the same frames/titles/brief, but
    only replace the requested variant's textual content (frame, overlay, rationale,
    promise) so a creator's edits to the other two packages are left untouched."""
    assert job.result is not None
    package = _require_variants(job)
    target = _find_variant(package, variant_id)
    metadata = job.result.metadata
    branding = load_branding_profile()

    fresh = generate_thumbnail_variants(
        job.result.transcript,
        package.frames,
        # Always retry with whichever model was configured for this run, even if that
        # earlier attempt fell back to heuristic (e.g. Ollama wasn't running yet) —
        # otherwise "Regenerate" can never use AI once a run has fallen back once.
        model=metadata.model,
        backend=metadata.backend or "ollama",
        titles=metadata.titles,
        brief=metadata.packaging_brief,
        prohibited_phrases=branding.prohibited_phrases,
    )
    replacement = next((v for v in fresh.variants if v.strategy == target.strategy), None)
    if replacement is None:
        raise ValueError(f"could not regenerate a '{target.strategy}' variant")

    target.overlay_text = replacement.overlay_text
    target.visual_rationale = replacement.visual_rationale
    target.audience_promise = replacement.audience_promise
    target.frame_path = replacement.frame_path
    target.frame_timestamp = replacement.frame_timestamp
    target.crop = CropBox()
    target.source = replacement.source
    target.model = replacement.model
    target.backend = replacement.backend
    target.fallback_reason = replacement.fallback_reason

    _rerender(job, target)
    _persist_variant_package(job, package)
    return target


def upload_thumbnail_frame(job: Job, variant_id: str, upload) -> ThumbnailVariant:
    """Let a creator use a dedicated thumbnail photograph instead of an extracted frame."""
    assert job.result is not None
    package = _require_variants(job)
    variant = _find_variant(package, variant_id)

    uploads_dir = job.result.output_dir / "thumbnails" / "uploads"
    uploads_dir.mkdir(parents=True, exist_ok=True)
    suffix = Path(upload.filename or "upload.jpg").suffix or ".jpg"
    dest = uploads_dir / f"{variant_id}{suffix}"
    with dest.open("wb") as handle:
        shutil.copyfileobj(upload.file, handle)

    variant.frame_path = str(dest)
    variant.frame_timestamp = None
    variant.crop = CropBox()
    variant.focal_x, variant.focal_y = 0.5, 0.5

    _rerender(job, variant)
    _persist_variant_package(job, package)
    return variant


def _run_job(job: Job, video_path: Path, options: dict) -> None:
    job.status = "running"

    def progress(message: str) -> None:
        job.emit({"type": "progress", "message": message})

    try:
        result = run_pipeline(video_path, on_progress=progress, **options)
    except Exception as exc:  # noqa: BLE001 - surface any failure to the client
        job.status = "error"
        job.error = str(exc)
        job.emit({"type": "error", "message": str(exc)})
        video_path.unlink(missing_ok=True)
    else:
        # Keep the source video alongside the outputs so the web UI can offer a
        # live preview; it is not deleted on success (only on failure, above).
        preview_path = result.output_dir / f"source{video_path.suffix}"
        video_path.replace(preview_path)
        job.video_path = preview_path
        job.result = result
        job.status = "done"
        info = job.as_dict()
        job.emit(
            {
                "type": "done",
                "output_dir": str(result.output_dir),
                "files": info["files"],
                "thumbnails": info["thumbnails"],
                "thumbnail_variants": info["thumbnail_variants"],
            }
        )


def submit_job(
    upload,
    *,
    output_root: str,
    quality: str,
    language: str | None,
    translate: bool,
    metadata_model: str | None,
    metadata_backend: str,
    context: str | None,
    thumbnails: bool = True,
) -> Job:
    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    job = JOBS.create()
    suffix = Path(upload.filename or "video").suffix or ".mp4"
    video_path = UPLOAD_DIR / f"{job.id}{suffix}"
    with video_path.open("wb") as handle:
        shutil.copyfileobj(upload.file, handle)

    options = dict(
        output_root=output_root,
        quality=quality,
        language=language,
        translate=translate,
        metadata_model=metadata_model,
        metadata_backend=metadata_backend,
        context=context,
        thumbnails=thumbnails,
    )
    _EXECUTOR.submit(_run_job, job, video_path, options)
    return job
