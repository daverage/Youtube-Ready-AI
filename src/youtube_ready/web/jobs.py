from __future__ import annotations

import dataclasses
import json
import queue
import shutil
import tempfile
import threading
import time
import uuid
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path

from youtube_ready.ai_runtime import ensure_ai_runtime
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
from youtube_ready.pipeline import PipelineResult, _render_package, run_pipeline
from youtube_ready.render import compute_crop_box, render_variant, resolve_frame_path
from youtube_ready.thumbnails import (
    classify_thumbnail_asset,
    extract_candidate_frames,
    find_better_take,
    generate_thumbnail_variants,
    migrate_legacy_package,
    recheck_rendered_thumbnail,
    refresh_thumbnail_assistant_fields,
    render_thumbnail_ideas,
    select_top_frames,
    suggest_overlay_ideas,
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
    ai_runtime_status: dict | None = None
    _messages: list[str] = field(default_factory=list)
    _subscribers: list["queue.Queue[str]"] = field(default_factory=list)
    _lock: threading.Lock = field(default_factory=threading.Lock)
    _thumbnail_continue: threading.Event = field(default_factory=threading.Event)
    _thumbnail_plan_mode: str | None = None
    # What a long-running thumbnail-studio request (generate/regenerate/recheck/
    # render/...) is doing right now. These run as plain blocking POSTs after the
    # main pipeline has finished, so they can't use the SSE stream (which already
    # ended with "done"); the frontend polls `activity_dict()` instead.
    _activity: str | None = None
    _activity_started: float | None = None
    _activity_step_started: float | None = None

    @contextmanager
    def activity(self, message: str) -> Iterator[None]:
        """Mark a studio operation as in progress for the duration of the block."""
        now = time.monotonic()
        self._activity, self._activity_started, self._activity_step_started = message, now, now
        try:
            yield
        finally:
            self._activity = self._activity_started = self._activity_step_started = None

    def set_activity_step(self, message: str) -> None:
        """Update the current step of an in-progress `activity` (no-op outside one)."""
        if self._activity_started is None:
            return
        self._activity = message
        self._activity_step_started = time.monotonic()

    def activity_dict(self) -> dict:
        now = time.monotonic()
        return {
            "active": self._activity_started is not None,
            "message": self._activity,
            "elapsed": round(now - self._activity_started, 1) if self._activity_started else 0.0,
            "step_elapsed": round(now - self._activity_step_started, 1) if self._activity_step_started else 0.0,
        }

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
                            "frame_filename": _thumbnail_relative_path(idea.frame_path, self.result.output_dir),
                        }
                        for idea in self.result.thumbnails.ideas
                    ],
                }
            if self.result.thumbnail_variants is not None:
                thumbnail_variants = _variant_package_dict(self.result.thumbnail_variants, self.result.output_dir)
        return {
            "id": self.id,
            "status": self.status,
            "error": self.error,
            "output_dir": output_dir,
            "files": files,
            "thumbnails": thumbnails,
            "thumbnail_variants": thumbnail_variants,
            "ai_runtime": self.ai_runtime_status,
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
    ai_runtime_status = None
    if metadata.backend and metadata.model:
        ai_runtime_status = ensure_ai_runtime(metadata.backend, metadata.model, start=True).as_dict()

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

    if thumbnail_variants is not None:
        refresh_thumbnail_assistant_fields(thumbnail_variants, transcript, metadata.packaging_brief)
        thumbnails = variants_to_legacy_package(thumbnail_variants)
        try:
            variants_path.write_text(thumbnail_variants.model_dump_json(indent=2), encoding="utf-8")
            thumbnails_path.write_text(thumbnails.model_dump_json(indent=2), encoding="utf-8")
            (session_dir / "thumbnail_ideas.txt").write_text(render_thumbnail_ideas(thumbnails), encoding="utf-8")
        except OSError:
            pass

    video_path = next(session_dir.glob("source.*"), None)

    job = Job(id=session_dir.name, status="done")
    # Plans generated but never rendered (e.g. the server restarted, or the page
    # was reloaded before clicking "Render thumbnails") must reopen in the same
    # waiting state, otherwise /continue rejects them and the UI has no way to
    # render them at all.
    if thumbnail_variants is not None and any(not v.rendered_path for v in thumbnail_variants.variants):
        job.status = "waiting_for_thumbnail_assets"
        job._thumbnail_plan_mode = "manual"
    job.ai_runtime_status = ai_runtime_status
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


def _thumbnail_relative_path(path: str | None, output_dir: Path) -> str | None:
    if not path:
        return None
    thumbnails_dir = (output_dir / "thumbnails").resolve()
    try:
        resolved = Path(path).resolve()
        if resolved.is_relative_to(thumbnails_dir):
            return str(resolved.relative_to(thumbnails_dir))
    except (OSError, ValueError):
        pass
    return Path(path).name


def _variant_package_dict(package: ThumbnailVariantPackage, output_dir: Path) -> dict:
    return {
        "schema_version": package.schema_version,
        "source": package.source,
        "model": package.model,
        "backend": package.backend,
        "fallback_reason": package.fallback_reason,
        "style_intent": package.style_intent,
        "uploaded_assets": [asset.model_dump() for asset in package.uploaded_assets],
        "frames": [
            {
                "timestamp": frame.timestamp,
                "filename": _thumbnail_relative_path(frame.path, output_dir),
                "preview_filename": _thumbnail_relative_path(frame.preview_path, output_dir),
                "is_duplicate": frame.is_duplicate,
                "has_face": frame.has_face,
                "source": frame.source,
            }
            for frame in package.frames
        ],
        "variants": [_variant_dict(v, output_dir) for v in package.variants],
    }


def _variant_dict(variant: ThumbnailVariant, output_dir: Path) -> dict:
    return {
        "variant_id": variant.variant_id,
        "strategy": variant.strategy,
        "style_intent": variant.style_intent,
        "title": variant.title,
        "overlay_text": variant.overlay_text,
        "overlay_options": variant.overlay_options,
        "visual_rationale": variant.visual_rationale,
        "audience_promise": variant.audience_promise,
        "frame_timestamp": variant.frame_timestamp,
        "frame_filename": _thumbnail_relative_path(variant.frame_path, output_dir),
        "crop": variant.crop.model_dump(),
        "focal_x": variant.focal_x,
        "focal_y": variant.focal_y,
        "text": variant.text.model_dump(),
        "rendered_filename": _thumbnail_relative_path(variant.rendered_path, output_dir),
        "visual_verified": variant.visual_verified,
        "source": variant.source,
        "model": variant.model,
        "backend": variant.backend,
        "fallback_reason": variant.fallback_reason,
        "warnings": variant.warnings,
        "assessment": variant.assessment.model_dump() if variant.assessment else None,
        "composition": variant.composition.model_dump() if variant.composition else None,
        "advice": variant.advice.model_dump() if variant.advice else None,
        "recheck": variant.recheck.model_dump() if variant.recheck else None,
        "source_assets": variant.source_assets,
        "edit_prompt": variant.edit_prompt,
        "fallback_plan": variant.fallback_plan,
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
    chapters_text = (output_dir / "chapters.txt").read_text(encoding="utf-8") if (output_dir / "chapters.txt").is_file() else ""
    thumbnails_text = render_thumbnail_ideas(legacy)
    (output_dir / "youtube_package.md").write_text(
        _render_package(job.result.metadata, chapters_text, thumbnails_text),
        encoding="utf-8",
    )


def _source_video_for_job(job: Job) -> Path:
    assert job.result is not None
    if job.video_path and job.video_path.is_file():
        return job.video_path
    for candidate in job.result.output_dir.glob("source.*"):
        if candidate.is_file():
            return candidate
    raise FileNotFoundError("source video is not available for thumbnail extraction")


def generate_thumbnail_plan_for_job(job: Job, *, style_intent: str = "balanced") -> ThumbnailVariantPackage:
    """Extract frames and create thumbnail plans only when explicitly requested."""
    if job.result is None:
        raise ValueError("this job has no completed publishing pack")
    if job.status not in {"done", "waiting_for_thumbnail_assets"}:
        raise ValueError("thumbnail generation is only available after transcription and metadata finish")

    with job.activity("Preparing thumbnail generation"):
        return _generate_thumbnail_plan(job, style_intent=style_intent)


def _generate_thumbnail_plan(job: Job, *, style_intent: str) -> ThumbnailVariantPackage:
    assert job.result is not None
    metadata = job.result.metadata
    if metadata.backend and metadata.model:
        job.set_activity_step(f"Starting the local AI runtime ({metadata.backend}: {metadata.model})")
        status = ensure_ai_runtime(metadata.backend, metadata.model, start=True)
        job.ai_runtime_status = status.as_dict()

    source_video = _source_video_for_job(job)
    output_dir = job.result.output_dir
    job.set_activity_step("Extracting candidate frames from the video with ffmpeg")
    candidate_frames = extract_candidate_frames(
        source_video,
        output_dir / "thumbnails",
        chapter_timestamps=[chapter.start for chapter in metadata.chapters],
    )
    job.set_activity_step(f"Scoring {len(candidate_frames)} candidate frames for sharpness and faces")
    top_frames = select_top_frames(candidate_frames)
    branding = load_branding_profile()
    uploaded_assets = job.result.thumbnail_variants.uploaded_assets if job.result.thumbnail_variants else []
    package = generate_thumbnail_variants(
        job.result.transcript,
        top_frames,
        model=metadata.model,
        backend=metadata.backend or "ollama",
        titles=metadata.titles,
        brief=metadata.packaging_brief,
        prohibited_phrases=branding.prohibited_phrases,
        style_intent=style_intent,
        uploaded_assets=uploaded_assets,
        on_progress=job.set_activity_step,
    )
    # Deliberately not rendered yet: while `waiting_for_thumbnail_assets`, edits
    # and uploads are refreshed without rendering (see `_refresh_and_persist_
    # without_render`) so the client's "Render thumbnails" button — the only
    # trigger for `_render_thumbnail_package`/`continue_thumbnail_render` — stays
    # visible (it keys off `rendered_filename` being absent) until the user is
    # done adjusting things and explicitly asks for the rendered result.
    _persist_variant_package(job, package)
    job.status = "waiting_for_thumbnail_assets"
    job._thumbnail_plan_mode = "manual"
    return package


def _render_variants_in_place(job: Job, package: ThumbnailVariantPackage) -> None:
    assert job.result is not None
    branding = load_branding_profile()
    total = len(package.variants)
    for index, variant in enumerate(package.variants, start=1):
        job.set_activity_step(f"Rendering the {variant.strategy} thumbnail ({index}/{total})")
        output_path = job.result.output_dir / "thumbnails" / f"variant-{variant.strategy}.jpg"
        variant.rendered_path = render_variant(variant, output_path, branding=branding)
        variant.warnings = validate_variant(
            variant,
            job.result.metadata.packaging_brief,
            transcript=job.result.transcript,
            prohibited_phrases=branding.prohibited_phrases,
        )
        variant.recheck = None  # stale once the composition it was checked against changes
    _persist_variant_package(job, package)


def _render_thumbnail_package(job: Job) -> ThumbnailVariantPackage:
    assert job.result is not None and job.result.thumbnail_variants is not None
    package = job.result.thumbnail_variants
    refresh_thumbnail_assistant_fields(package, job.result.transcript, job.result.metadata.packaging_brief)
    _render_variants_in_place(job, package)
    return package


def _refresh_and_persist_without_render(job: Job, package: ThumbnailVariantPackage) -> None:
    assert job.result is not None
    refresh_thumbnail_assistant_fields(package, job.result.transcript, job.result.metadata.packaging_brief)
    _persist_variant_package(job, package)


def _rerender(job: Job, variant: ThumbnailVariant) -> None:
    assert job.result is not None
    branding = load_branding_profile()
    if job.result.thumbnail_variants:
        refresh_thumbnail_assistant_fields(
            job.result.thumbnail_variants,
            job.result.transcript,
            job.result.metadata.packaging_brief,
        )
    output_path = job.result.output_dir / "thumbnails" / f"variant-{variant.strategy}.jpg"
    variant.rendered_path = render_variant(variant, output_path, branding=branding)
    variant.warnings = validate_variant(
        variant,
        job.result.metadata.packaging_brief,
        transcript=job.result.transcript,
        prohibited_phrases=branding.prohibited_phrases,
    )
    variant.recheck = None  # stale once the composition it was checked against changes


def _persist_after_variant_change(job: Job, package: ThumbnailVariantPackage, variant: ThumbnailVariant) -> None:
    """Shared tail end of every edit/upload entry point (update_thumbnail_variant,
    upload_thumbnail_frame, upload_thumbnail_asset): while a manual plan is still
    `waiting_for_thumbnail_assets`, changes are refreshed but deliberately not
    rendered (batched — see `generate_thumbnail_plan_for_job`'s comment); once the
    plan has been rendered at least once (status moves past that), every further
    edit re-renders immediately. Kept as one function specifically so the two
    branches can't drift out of sync between call sites the way they did before."""
    assert job.result is not None
    if job.status == "waiting_for_thumbnail_assets":
        _refresh_and_persist_without_render(job, package)
    else:
        _rerender(job, variant)
        _persist_variant_package(job, package)


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
    if "style_intent" in updates:
        style_intent = str(updates["style_intent"])
        if style_intent in {"clear", "balanced", "bold"}:
            variant.style_intent = style_intent
            package.style_intent = style_intent
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

    _persist_after_variant_change(job, package, variant)
    return variant


def regenerate_thumbnail_variant(job: Job, variant_id: str) -> ThumbnailVariant:
    """Re-run generation for every strategy using the same frames/titles/brief, but
    only replace the requested variant's textual content (frame, overlay, rationale,
    promise) so a creator's edits to the other two packages are left untouched."""
    assert job.result is not None
    package = _require_variants(job)
    target = _find_variant(package, variant_id)
    branding = load_branding_profile()

    with job.activity(f"Regenerating the {target.strategy} thumbnail"):
        _regenerate_into(job, package, target, branding)
    return target


def _regenerate_into(
    job: Job, package: ThumbnailVariantPackage, target: ThumbnailVariant, branding
) -> None:
    assert job.result is not None
    metadata = job.result.metadata
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
        style_intent=target.style_intent,
        uploaded_assets=package.uploaded_assets,
        on_progress=job.set_activity_step,
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
    target.assessment = replacement.assessment
    target.composition = replacement.composition
    target.advice = replacement.advice
    target.source_assets = replacement.source_assets
    target.edit_prompt = replacement.edit_prompt
    target.fallback_plan = replacement.fallback_plan

    job.set_activity_step(f"Rendering the {target.strategy} thumbnail")
    _rerender(job, target)
    _persist_variant_package(job, package)


def recheck_thumbnail_variant(job: Job, variant_id: str) -> ThumbnailVariant:
    """Vision-assess the actual rendered thumbnail (not the raw source frame/assets)
    so a creator can check whether the finished composition is actually strong
    after adjusting crop, text, style, or supporting assets and rendering."""
    assert job.result is not None
    package = _require_variants(job)
    variant = _find_variant(package, variant_id)
    metadata = job.result.metadata
    with job.activity(f"Asking {metadata.model} to look at the rendered {variant.strategy} thumbnail"):
        variant.recheck = recheck_rendered_thumbnail(
            metadata.model, metadata.backend or "ollama", variant, metadata.packaging_brief
        )
    _persist_variant_package(job, package)
    return variant


def find_better_take_for_variant(job: Job, variant_id: str) -> ThumbnailVariant:
    """Look a couple of seconds either side of the current frame for one with
    a stronger on-camera take (facing the camera, smiling, sharper/bigger
    face) and swap to it if one is clearly better. Raises ValueError with a
    specific reason when it can't help (no face in the current frame, ffmpeg/
    OpenCV missing, or nothing nearby scores meaningfully better)."""
    assert job.result is not None
    package = _require_variants(job)
    variant = _find_variant(package, variant_id)
    if not variant.frame_path or variant.frame_timestamp is None:
        raise ValueError("this package has no extracted video frame to search near (it uses an uploaded photo instead)")

    source_video = _source_video_for_job(job)
    with job.activity("Scanning nearby frames for a stronger on-camera take"):
        better = find_better_take(
            source_video, variant.frame_path, variant.frame_timestamp, job.result.output_dir / "thumbnails"
        )
    package.frames.append(better)
    variant.frame_path = better.path
    variant.frame_timestamp = better.timestamp
    variant.crop = CropBox()
    variant.focal_x, variant.focal_y = 0.5, 0.5
    _persist_after_variant_change(job, package, variant)
    return variant


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

    if variant.frame_timestamp is not None:
        variant.replaced_frame_path = variant.frame_path
        variant.replaced_frame_timestamp = variant.frame_timestamp
    variant.frame_path = str(dest)
    variant.frame_timestamp = None
    variant.crop = CropBox()
    variant.focal_x, variant.focal_y = 0.5, 0.5
    with job.activity("Classifying the uploaded image"):
        asset = classify_thumbnail_asset(
            dest,
            upload.filename,
            model=job.result.metadata.model,
            backend=job.result.metadata.backend or "ollama",
        )
    package.uploaded_assets = [existing for existing in package.uploaded_assets if existing.path != str(dest)]
    package.uploaded_assets.append(asset)
    variant.source_assets = {
        "frame": variant.frame_path,
        "uploaded_assets": [item.model_dump() for item in package.uploaded_assets],
    }

    _persist_after_variant_change(job, package, variant)
    return variant


def upload_thumbnail_asset(job: Job, variant_id: str, upload) -> ThumbnailVariant:
    """Add a supporting image for the assistant to classify and optionally composite."""
    assert job.result is not None
    package = _require_variants(job)
    variant = _find_variant(package, variant_id)

    assets_dir = job.result.output_dir / "thumbnails" / "assets"
    assets_dir.mkdir(parents=True, exist_ok=True)
    suffix = Path(upload.filename or "asset.jpg").suffix or ".jpg"
    dest = assets_dir / f"{variant_id}-{uuid.uuid4().hex[:8]}{suffix}"
    with dest.open("wb") as handle:
        shutil.copyfileobj(upload.file, handle)

    with job.activity("Classifying the uploaded image"):
        asset = classify_thumbnail_asset(
            dest,
            upload.filename,
            model=job.result.metadata.model,
            backend=job.result.metadata.backend or "ollama",
        )
    package.uploaded_assets.append(asset)
    _persist_after_variant_change(job, package, variant)
    return variant


def suggest_overlay_ideas_for_variant(job: Job, variant_id: str) -> ThumbnailVariant:
    """Replace a variant's offered overlay alternatives with a fresh set. Only the
    suggestions change — the overlay itself is untouched until the creator picks one."""
    assert job.result is not None
    package = _require_variants(job)
    variant = _find_variant(package, variant_id)
    metadata = job.result.metadata
    label = f"Asking {metadata.model} for new thumbnail text ideas" if metadata.model else "Finding new thumbnail text ideas"
    with job.activity(label):
        variant.overlay_options = suggest_overlay_ideas(
            job.result.transcript,
            variant,
            brief=metadata.packaging_brief,
            model=metadata.model,
            backend=metadata.backend or "ollama",
        )
    _persist_variant_package(job, package)
    return variant


def _restore_extracted_frame(variant: ThumbnailVariant, package: ThumbnailVariantPackage) -> None:
    """Put a variant back on a video frame after its uploaded photo is removed:
    the frame that photo replaced if known, else the package's first candidate."""
    if variant.replaced_frame_path:
        variant.frame_path = variant.replaced_frame_path
        variant.frame_timestamp = variant.replaced_frame_timestamp
    else:
        frame = next((f for f in package.frames if f.timestamp is not None), None)
        variant.frame_path = frame.path if frame else None
        variant.frame_timestamp = frame.timestamp if frame else None
    variant.replaced_frame_path = None
    variant.replaced_frame_timestamp = None
    variant.crop = CropBox()
    variant.focal_x, variant.focal_y = 0.5, 0.5


def remove_thumbnail_asset(job: Job, variant_id: str, filename: str) -> ThumbnailVariant:
    """Remove an uploaded image — a variant's own photo (via "Upload image") or a
    supporting/product image — and delete its file. Uploaded assets are shared by
    every variant in the package, so any variant using the image as its photo
    goes back to a video frame, and every already-rendered variant is re-rendered
    so none of them keep showing the removed image."""
    assert job.result is not None
    package = _require_variants(job)
    variant = _find_variant(package, variant_id)
    asset = next((a for a in package.uploaded_assets if Path(a.path).name == filename), None)
    # An uploaded photo is normally also in `uploaded_assets`, but older sessions
    # may only reference it as a variant's frame.
    photo_users = [v for v in package.variants if v.frame_timestamp is None and v.frame_path and Path(v.frame_path).name == filename]
    if asset is None and not photo_users:
        raise KeyError(f"unknown uploaded image {filename!r}")

    removed_path = Path(asset.path) if asset else Path(photo_users[0].frame_path)
    package.uploaded_assets = [a for a in package.uploaded_assets if Path(a.path).name != filename]
    for user in photo_users:
        _restore_extracted_frame(user, package)

    thumbnails_dir = (job.result.output_dir / "thumbnails").resolve()
    candidate = removed_path if removed_path.is_file() else thumbnails_dir / removed_path.parent.name / filename
    if candidate.resolve().is_relative_to(thumbnails_dir):
        candidate.unlink(missing_ok=True)

    if job.status == "waiting_for_thumbnail_assets":
        _refresh_and_persist_without_render(job, package)
    else:
        for rendered in [v for v in package.variants if v.rendered_path]:
            _rerender(job, rendered)
        _persist_variant_package(job, package)
    return variant


def _run_job(job: Job, video_path: Path, options: dict) -> None:
    job.status = "running"
    thumbnail_review = bool(options.pop("thumbnail_review", False))

    def progress(message: str) -> None:
        job.emit({"type": "progress", "message": message})

    backend = options.get("metadata_backend")
    model = options.get("metadata_model")
    if backend and model:
        status = ensure_ai_runtime(str(backend), str(model), start=True)
        job.ai_runtime_status = status.as_dict()
        job.emit({"type": "ai_status", "runtime": status.as_dict()})

    def thumbnail_plan_ready(partial: PipelineResult) -> None:
        job.result = partial
        job.status = "waiting_for_thumbnail_assets"
        job._thumbnail_plan_mode = "pipeline"
        info = job.as_dict()
        job.emit(
            {
                "type": "thumbnail_plan",
                "output_dir": str(partial.output_dir),
                "files": info["files"],
                "thumbnails": info["thumbnails"],
                "thumbnail_variants": info["thumbnail_variants"],
                "ai_runtime": info["ai_runtime"],
            }
        )
        job._thumbnail_continue.wait()
        job.status = "running"

    try:
        result = run_pipeline(
            video_path,
            on_progress=progress,
            on_thumbnail_plan=thumbnail_plan_ready if thumbnail_review and options.get("thumbnails") else None,
            **options,
        )
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
                "ai_runtime": info["ai_runtime"],
            }
        )


def continue_thumbnail_render(job: Job) -> None:
    if job.status != "waiting_for_thumbnail_assets":
        raise ValueError("this job is not waiting for thumbnail assets")
    if job._thumbnail_plan_mode == "manual":
        with job.activity("Rendering thumbnails"):
            _render_thumbnail_package(job)
        job.status = "done"
        job._thumbnail_plan_mode = None
        return
    job._thumbnail_continue.set()


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
    thumbnails: bool = False,
    thumbnail_review: bool = False,
    thumbnail_style_intent: str = "balanced",
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
        thumbnail_review=thumbnail_review,
        thumbnail_style_intent=thumbnail_style_intent,
    )
    _EXECUTOR.submit(_run_job, job, video_path, options)
    return job
