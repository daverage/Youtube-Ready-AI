from __future__ import annotations

import math
import re
import shutil
import subprocess
import uuid
from collections.abc import Callable
from pathlib import Path

from pydantic import ValidationError

from youtube_ready.metadata import _backend_call
from youtube_ready.models import (
    PackagingBrief,
    ThumbnailFrame,
    ThumbnailIdea,
    ThumbnailPackage,
    ThumbnailVariant,
    ThumbnailVariantPackage,
    TitleSuggestion,
    Transcript,
)

MAX_CANDIDATE_FRAMES = 28
TOP_FRAME_COUNT = 10
MIN_FRAME_GAP_SECONDS = 3.0
TRANSCRIPT_CONTEXT_CHARS = 14_000
MASTER_WIDTH = 1280
PREVIEW_WIDTH = 320
STRATEGIES = ("search", "browse", "outcome")

_PTS_TIME_RE = re.compile(r"pts_time:([\d.]+)")


def _ffmpeg_available() -> bool:
    return shutil.which("ffmpeg") is not None


def extract_candidate_frames(
    video_path: Path,
    output_dir: Path,
    *,
    scene_threshold: float = 0.35,
    max_frames: int = MAX_CANDIDATE_FRAMES,
    chapter_timestamps: list[float] | None = None,
) -> list[ThumbnailFrame]:
    """Pull a diverse candidate set spanning the whole runtime: scene changes,
    evenly-spaced samples, chapter boundaries and the closing moments.

    Falls back to spaced samples alone if too few scene changes are found (e.g.
    a mostly-static video), and returns [] gracefully if ffmpeg isn't on PATH.
    """
    if not _ffmpeg_available():
        return []
    output_dir.mkdir(parents=True, exist_ok=True)

    scene_budget = max(max_frames // 2, 3)
    frames = _extract_scene_change_frames(video_path, output_dir, scene_threshold, scene_budget)
    for frame in frames:
        frame.source = "scene"

    remaining = max(max_frames - len(frames), 4)
    even_budget = max(remaining - (3 if chapter_timestamps else 0), 4)
    even_frames = _extract_evenly_spaced_frames(video_path, output_dir, even_budget)
    for frame in even_frames:
        frame.source = "even"

    if len(frames) < min(3, max_frames):
        frames = even_frames
    else:
        frames = _merge_frame_lists(frames, even_frames)

    if chapter_timestamps:
        chapter_frames = _extract_at_timestamps(video_path, output_dir, chapter_timestamps[:6], "chapter")
        for frame in chapter_frames:
            frame.source = "chapter"
        frames = _merge_frame_lists(frames, chapter_frames)

    duration = _probe_duration(video_path)
    if duration > 0:
        conclusion_ts = [max(duration - max(duration * 0.05, 3.0), 0.0)]
        conclusion_frames = _extract_at_timestamps(video_path, output_dir, conclusion_ts, "conclusion")
        for frame in conclusion_frames:
            frame.source = "conclusion"
        frames = _merge_frame_lists(frames, conclusion_frames)

    frames = sorted(frames, key=lambda f: f.timestamp)[:max_frames]
    _attach_dimensions_and_previews(frames, output_dir)
    return _flag_duplicates(frames)


def _merge_frame_lists(base: list[ThumbnailFrame], extra: list[ThumbnailFrame], min_gap: float = 1.5) -> list[ThumbnailFrame]:
    merged = list(base)
    for frame in extra:
        if any(abs(frame.timestamp - existing.timestamp) < min_gap for existing in merged):
            continue
        merged.append(frame)
    return merged


def _run_ffmpeg_select(video_path: Path, output_dir: Path, vf: str, max_frames: int, prefix: str) -> list[ThumbnailFrame]:
    pattern = output_dir / f"{prefix}_%03d.jpg"
    command = [
        "ffmpeg", "-y", "-i", str(video_path), "-vf", f"{vf},showinfo", "-fps_mode", "vfr",
        "-frames:v", str(max_frames), "-qscale:v", "2", str(pattern),
    ]
    result = subprocess.run(command, capture_output=True, text=True, timeout=600)
    timestamps = [float(match) for match in _PTS_TIME_RE.findall(result.stderr)]
    paths = sorted(output_dir.glob(f"{prefix}_*.jpg"))
    frames = []
    for index, path in enumerate(paths):
        timestamp = timestamps[index] if index < len(timestamps) else 0.0
        frames.append(ThumbnailFrame(timestamp=timestamp, path=str(path)))
    return frames


def _extract_scene_change_frames(
    video_path: Path, output_dir: Path, scene_threshold: float, max_frames: int
) -> list[ThumbnailFrame]:
    vf = f"select='gt(scene,{scene_threshold})',scale={MASTER_WIDTH}:-2"
    return _run_ffmpeg_select(video_path, output_dir, vf, max_frames, "scene")


def _extract_evenly_spaced_frames(video_path: Path, output_dir: Path, max_frames: int) -> list[ThumbnailFrame]:
    duration = _probe_duration(video_path)
    if duration <= 0:
        vf = f"select='not(mod(n,150))',scale={MASTER_WIDTH}:-2"
        return _run_ffmpeg_select(video_path, output_dir, vf, max_frames, "even")
    interval = duration / (max_frames + 1)
    timestamps = [interval * i for i in range(1, max_frames + 1)]
    return _extract_at_timestamps(video_path, output_dir, timestamps, "even", include_first=True)


def _extract_at_timestamps(
    video_path: Path, output_dir: Path, timestamps: list[float], prefix: str, *, include_first: bool = False
) -> list[ThumbnailFrame]:
    timestamps = sorted(t for t in timestamps if t >= 0)
    if not timestamps:
        return []
    clauses = []
    if include_first:
        clauses.append("eq(n,0)")
    for t in timestamps:
        clauses.append(f"gte(t,{t:.2f})*lt(prev_selected_t,{t:.2f})")
    expr = "+".join(clauses)
    vf = f"select='{expr}',scale={MASTER_WIDTH}:-2"
    return _run_ffmpeg_select(video_path, output_dir, vf, len(clauses), prefix)


def _probe_duration(video_path: Path) -> float:
    if not shutil.which("ffprobe"):
        return 0.0
    command = [
        "ffprobe", "-v", "error", "-show_entries", "format=duration",
        "-of", "default=noprint_wrappers=1:nokey=1", str(video_path),
    ]
    try:
        result = subprocess.run(command, capture_output=True, text=True, timeout=60)
        return float(result.stdout.strip())
    except (ValueError, subprocess.SubprocessError):
        return 0.0


def _attach_dimensions_and_previews(frames: list[ThumbnailFrame], output_dir: Path) -> None:
    """Record master dimensions and write a small preview beside each master frame."""
    try:
        from PIL import Image
    except Exception:  # noqa: BLE001
        return
    preview_dir = output_dir / "previews"
    for frame in frames:
        try:
            with Image.open(frame.path) as image:
                frame.width, frame.height = image.size
                preview_dir.mkdir(parents=True, exist_ok=True)
                ratio = PREVIEW_WIDTH / max(image.width, 1)
                preview_size = (PREVIEW_WIDTH, max(int(image.height * ratio), 1))
                preview_path = preview_dir / Path(frame.path).name
                image.convert("RGB").resize(preview_size).save(preview_path, "JPEG", quality=80)
                frame.preview_path = str(preview_path)
        except Exception:  # noqa: BLE001
            continue


def _average_hash(path: str) -> int | None:
    try:
        from PIL import Image
    except Exception:  # noqa: BLE001
        return None
    try:
        with Image.open(path) as image:
            small = image.convert("L").resize((8, 8))
            pixels = list(small.tobytes())
        avg = sum(pixels) / len(pixels)
        bits = 0
        for pixel in pixels:
            bits = (bits << 1) | (1 if pixel >= avg else 0)
        return bits
    except Exception:  # noqa: BLE001
        return None


def _hamming(a: int, b: int) -> int:
    return bin(a ^ b).count("1")


def _flag_duplicates(frames: list[ThumbnailFrame], threshold: int = 6) -> list[ThumbnailFrame]:
    """Mark near-identical frames (e.g. static shots sampled repeatedly) rather than
    dropping them outright, so a caller can still fall back to them if needed."""
    hashes: list[tuple[ThumbnailFrame, int | None]] = [(frame, _average_hash(frame.path)) for frame in frames]
    kept_hashes: list[int] = []
    for frame, digest in hashes:
        if digest is None:
            continue
        if any(_hamming(digest, existing) <= threshold for existing in kept_hashes):
            frame.is_duplicate = True
        else:
            kept_hashes.append(digest)
    return frames


_FACE_CASCADE = None
_BLUR_SHARPNESS_MIN = 15.0


def _score_frames(frames: list[ThumbnailFrame]) -> list[ThumbnailFrame]:
    """Best-effort technical scoring; semantic relevance is handled later from transcript context."""
    try:
        import cv2
    except Exception:  # noqa: BLE001
        return frames

    global _FACE_CASCADE
    if _FACE_CASCADE is None:
        try:
            _FACE_CASCADE = cv2.CascadeClassifier(cv2.data.haarcascades + "haarcascade_frontalface_default.xml")
        except Exception:  # noqa: BLE001
            _FACE_CASCADE = False

    scored = []
    for frame in frames:
        try:
            image = cv2.imread(frame.path)
            if image is None:
                scored.append(frame)
                continue
            gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
            sharpness = float(cv2.Laplacian(gray, cv2.CV_64F).var())
            has_face = False
            if _FACE_CASCADE:
                faces = _FACE_CASCADE.detectMultiScale(gray, scaleFactor=1.1, minNeighbors=5, minSize=(40, 40))
                has_face = len(faces) > 0
            negative_space_score = _negative_space_score(gray)
            scored.append(
                frame.model_copy(
                    update={"sharpness": sharpness, "has_face": has_face, "negative_space_score": negative_space_score}
                )
            )
        except Exception:  # noqa: BLE001
            scored.append(frame)
    return scored


def _negative_space_score(gray) -> float:
    """How much low-detail (edge-sparse) space exists for overlay text: higher is better."""
    import cv2

    try:
        edges = cv2.Canny(gray, 80, 160)
        h, w = edges.shape
        regions = [
            edges[0 : h // 3, :],  # top band
            edges[h - h // 3 : h, :],  # bottom band
        ]
        densities = [float((region > 0).mean()) for region in regions]
        flattest = min(densities) if densities else 1.0
        return max(0.0, 1.0 - flattest * 8.0)
    except Exception:  # noqa: BLE001
        return 0.0


def select_top_frames(
    frames: list[ThumbnailFrame], *, count: int = TOP_FRAME_COUNT, min_gap_seconds: float = MIN_FRAME_GAP_SECONDS
) -> list[ThumbnailFrame]:
    """Select technically usable, temporally varied candidates for the shortlist.

    A face is a useful signal, not an automatic winner: product, screen, food, craft,
    vehicle and demonstration videos often need the object/result more than a face.
    """
    scored = _score_frames(frames)
    usable = [f for f in scored if not f.is_duplicate]
    non_blurry = [f for f in usable if f.sharpness == 0.0 or f.sharpness >= _BLUR_SHARPNESS_MIN]
    if non_blurry:
        usable = non_blurry
    if not usable:
        usable = scored

    def rank(frame: ThumbnailFrame) -> float:
        sharpness_score = math.log1p(max(frame.sharpness, 0.0)) * 100.0
        face_bonus = 220.0 if frame.has_face else 0.0
        space_bonus = frame.negative_space_score * 60.0
        return sharpness_score + face_bonus + space_bonus

    chosen: list[ThumbnailFrame] = []
    for frame in sorted(usable, key=rank, reverse=True):
        if any(abs(frame.timestamp - picked.timestamp) < min_gap_seconds for picked in chosen):
            continue
        chosen.append(frame)
        if len(chosen) == count:
            break
    return sorted(chosen, key=lambda frame: frame.timestamp)


def _title_seed(transcript: Transcript) -> str:
    text = " ".join(segment.text for segment in transcript.segments[:6]).strip()
    text = re.sub(r"^(?:okay|right|so|well|hello|hi|hey|welcome)[,.!\s]+", "", text, flags=re.IGNORECASE)
    sentence = re.split(r"[.!?]", text)[0].strip()
    words = sentence.split()[:9]
    return " ".join(words).strip(" ,.-") or "This Video"


def _safe_overlay(text: str, max_words: int = 4, max_chars: int = 28) -> str:
    text = re.sub(r"\s+", " ", text).strip(" .,:;!?-")
    words = text.split()[:max_words]
    result = " ".join(words)
    if len(result) > max_chars:
        result = result[:max_chars].rsplit(" ", 1)[0]
    return result


def heuristic_thumbnail_ideas(frames: list[ThumbnailFrame], transcript: Transcript, reason: str | None = None) -> ThumbnailPackage:
    """Conservative fallback: clear subject first, no fabricated emotional claim."""
    seed = _safe_overlay(_title_seed(transcript))
    templates = [
        ("Subject First", seed),
        ("Clean Visual", ""),
        ("Key Takeaway", "What Matters"),
    ]
    ideas = []
    for index, (concept, overlay) in enumerate(templates):
        frame = frames[index] if index < len(frames) else (frames[0] if frames else None)
        prompt_subject = seed or "the video's main subject"
        ideas.append(
            ThumbnailIdea(
                concept=concept,
                overlay_text=overlay,
                visual_description="Use the clearest relevant subject or result frame, with simple composition and minimal clutter.",
                frame_timestamp=frame.timestamp if frame else None,
                frame_path=frame.path if frame else None,
                image_prompt=f"16:9 YouTube thumbnail composition, {prompt_subject}, one clear focal subject, clean background, readable silhouette, natural contrast, room for optional text",
            )
        )
    return ThumbnailPackage(frames=frames, ideas=ideas, source="fallback" if reason else "heuristic", fallback_reason=reason)


def _transcript_overview(transcript: Transcript) -> str:
    """Whole-video context without dumping an arbitrarily long transcript into a small local model."""
    segments = transcript.segments
    if not segments:
        return "(empty transcript)"

    lines = [f"[{s.start:.1f}s] {s.text.strip()}" for s in segments]
    joined = "\n".join(lines)
    if len(joined) <= TRANSCRIPT_CONTEXT_CHARS:
        return joined

    # Sample across the whole runtime so opening, middle and conclusion can all influence packaging.
    target_count = min(30, len(segments))
    indexes = sorted({round(i * (len(segments) - 1) / max(target_count - 1, 1)) for i in range(target_count)})
    sampled = "\n".join(f"[{segments[i].start:.1f}s] {segments[i].text.strip()}" for i in indexes)
    return sampled[:TRANSCRIPT_CONTEXT_CHARS]


def _context_near_timestamp(transcript: Transcript, timestamp: float, window: float = 12.0) -> str:
    relevant = [
        segment.text.strip()
        for segment in transcript.segments
        if segment.end >= timestamp - window and segment.start <= timestamp + window
    ]
    if not relevant and transcript.segments:
        nearest = min(transcript.segments, key=lambda segment: abs(segment.start - timestamp))
        relevant = [nearest.text.strip()]
    return " ".join(relevant)[:700]


def _frame_contexts(transcript: Transcript, frames: list[ThumbnailFrame]) -> str:
    if not frames:
        return "(no candidate frames available)"
    return "\n".join(
        f"- {frame.timestamp:.1f}s | nearby transcript: {_context_near_timestamp(transcript, frame.timestamp)}"
        for frame in frames
    )


def _prompt(
    transcript: Transcript,
    frames: list[ThumbnailFrame],
    context: str | None,
    titles: list[str] | None = None,
) -> str:
    title_block = "\n".join(f"- {title}" for title in (titles or [])) or "(no title candidates supplied)"
    return f"""You are designing three generic, evidence-led YouTube thumbnail concepts.

Use ONLY supported information from the transcript/context below. You cannot actually see the candidate frames, so never claim a facial expression, object, result or visual detail unless the nearby transcript/context supports it. The timestamps are candidate stills; choose the timestamp whose nearby content best matches the concept.

Return exactly one valid JSON object and nothing else.

SCHEMA
{{"ideas":[{{"concept":"...","overlay_text":"...","visual_description":"...","frame_timestamp":0.0,"image_prompt":"..."}}]}}

STRATEGY
Create exactly three meaningfully different concepts that can test different viewer motivations:
1. CLARITY/SEARCH: make the subject/result immediately understandable.
2. CURIOSITY/BROWSE: create one honest visual question, contrast or unresolved tension grounded in the video.
3. OUTCOME/STAKES: show or imply the supported consequence, result, comparison, transformation or why-it-matters angle. If none exists, use the strongest benefit-led visual.

TITLE + THUMBNAIL RULES
- Treat title and thumbnail as one package. The thumbnail should ADD information, tension or visual evidence rather than simply repeat the title.
- Do not copy a title into the overlay.
- Use concrete subjects/results over generic reaction language.
- Prefer one dominant visual idea, not a collage of many unrelated objects.
- Prefer strong subject separation, low clutter and a composition readable at small size.
- Faces/expressions may help when relevant, but never force a face into a topic where the object, screen, result or comparison is the real visual hook.

OVERLAY RULES
- 0-4 words is preferred; 5 is an absolute maximum.
- Empty overlay_text is allowed when the image works better without text.
- Text must be specific to the supported concept. Avoid generic clickbait such as "You Won't Believe This", "OMG", "Shocking", "Secret" or "Game Changer".
- Do not state a verdict the video does not support.
- Do not merely repeat words already obvious from the supplied title unless the identifier is essential.

VISUAL DESCRIPTION
- One sentence specifying the focal subject, comparison/result if supported, framing and where optional text could sit.
- Do not invent a pose, emotion or physical detail that is not supported.

IMAGE PROMPT
- A short 16:9 composition/shot brief for an optional image generator or designer.
- Describe subject, composition, lighting/contrast and mood.
- Do NOT include rendered text or quote the overlay text.
- Keep it faithful to the actual subject; no unrelated dramatic imagery.

CANDIDATE TITLE HYPOTHESES
{title_block}

CREATOR CONTEXT
{context or '(none supplied)'}

CANDIDATE FRAME CONTEXT
{_frame_contexts(transcript, frames)}

WHOLE-VIDEO TRANSCRIPT OVERVIEW
{_transcript_overview(transcript)}"""


def _nearest_frame(frames: list[ThumbnailFrame], timestamp: float | None) -> ThumbnailFrame | None:
    if not frames:
        return None
    if timestamp is None:
        return frames[0]
    return min(frames, key=lambda frame: abs(frame.timestamp - timestamp))


def generate_thumbnail_ideas(
    transcript: Transcript,
    frames: list[ThumbnailFrame],
    *,
    model: str | None,
    context: str | None = None,
    backend: str = "ollama",
    call: Callable[[str, str], dict] | None = None,
    titles: list[str] | None = None,
) -> ThumbnailPackage:
    """Generate concepts. `titles` is optional, preserving compatibility with existing callers."""
    call = call or _backend_call(backend)
    if not frames:
        return heuristic_thumbnail_ideas(frames, transcript, reason="No candidate thumbnail frames were extracted")
    if not model:
        return heuristic_thumbnail_ideas(frames, transcript, reason=None)

    try:
        raw = call(model, _prompt(transcript, frames, context, titles=titles))
        ideas_raw = raw.get("ideas", []) if isinstance(raw, dict) else []
        ideas = []
        for item in ideas_raw[:3]:
            concept = str(item.get("concept", "")).strip()
            if not concept:
                continue
            overlay_text = _safe_overlay(str(item.get("overlay_text", "")), max_words=5, max_chars=32)
            frame = _nearest_frame(frames, item.get("frame_timestamp"))
            ideas.append(
                ThumbnailIdea(
                    concept=concept,
                    overlay_text=overlay_text,
                    visual_description=str(item.get("visual_description", "")).strip(),
                    frame_timestamp=frame.timestamp if frame else None,
                    frame_path=frame.path if frame else None,
                    image_prompt=str(item.get("image_prompt", "")).strip(),
                )
            )
        if len(ideas) < 2:
            raise ValueError("thumbnail model returned too little usable content")
        return ThumbnailPackage(frames=frames, ideas=ideas, source="llm", model=model, backend=backend)
    except (Exception, ValidationError) as exc:
        package = heuristic_thumbnail_ideas(frames, transcript, reason=f"Local thumbnail model unavailable or invalid: {exc}")
        package.model = model
        package.backend = backend
        return package


def render_thumbnail_ideas(package: ThumbnailPackage) -> str:
    if not package.ideas:
        return "No thumbnail ideas were generated.\n"
    blocks = []
    for index, idea in enumerate(package.ideas, start=1):
        if idea.frame_path and idea.frame_timestamp is not None:
            frame_note = f"Suggested frame: {idea.frame_timestamp:.1f}s ({idea.frame_path})"
        elif idea.frame_path:
            frame_note = f"Suggested frame: (uploaded) ({idea.frame_path})"
        else:
            frame_note = "Suggested frame: (none)"
        overlay = idea.overlay_text or "(no overlay text)"
        blocks.append(
            f"{index}. {idea.concept}\n"
            f"   Overlay text: {overlay}\n"
            f"   Visual: {idea.visual_description}\n"
            f"   {frame_note}\n"
            f"   Image prompt: {idea.image_prompt}"
        )
    return "\n\n".join(blocks) + "\n"


# --- Title-thumbnail variants -----------------------------------------------
#
# One ThumbnailVariant per title strategy (search/browse/outcome), sharing the
# same PackagingBrief evidence that produced the titles so overlay text and
# visual rationale stay coherent with what the title already promises.

STRATEGY_LABELS = {
    "search": "Clarity/Search",
    "browse": "Curiosity/Browse",
    "outcome": "Outcome/Stakes",
}
OVERLAY_MAX_WORDS = 5
OVERLAY_MAX_CHARS = 32
OVERLAY_DUP_WARNING = "Overlay text duplicates the title"


def _new_variant_id() -> str:
    return uuid.uuid4().hex[:10]


def _heuristic_variant(
    strategy: str, index: int, titles: list[TitleSuggestion], frames: list[ThumbnailFrame], brief: PackagingBrief | None
) -> ThumbnailVariant:
    title = titles[index].title if index < len(titles) else ""
    frame = frames[index] if index < len(frames) else (frames[0] if frames else None)
    subject = (brief.core_topic if brief else "") or _safe_overlay(title, max_words=6, max_chars=60) or "This Video"
    overlays = {
        "search": "",
        "browse": "What Happens Next",
        "outcome": "The Result",
    }
    rationales = {
        "search": f"Clear, literal shot of {subject} so a searching viewer instantly recognises the subject.",
        "browse": "A visually intriguing moment that creates honest curiosity without spoiling the payoff.",
        "outcome": "A shot of the result or conclusion, supporting the outcome-led title.",
    }
    return ThumbnailVariant(
        variant_id=_new_variant_id(),
        strategy=strategy,
        title=title,
        title_suggestion_index=index if index < len(titles) else None,
        frame_timestamp=frame.timestamp if frame else None,
        frame_path=frame.path if frame else None,
        overlay_text=overlays[strategy],
        visual_rationale=rationales[strategy],
        audience_promise=(brief.viewer_payoff if brief else "") or "",
        source="heuristic",
    )


def heuristic_thumbnail_variants(
    frames: list[ThumbnailFrame],
    transcript: Transcript,
    titles: list[TitleSuggestion],
    *,
    brief: PackagingBrief | None = None,
    reason: str | None = None,
) -> ThumbnailVariantPackage:
    variants = [_heuristic_variant(strategy, i, titles, frames, brief) for i, strategy in enumerate(STRATEGIES)]
    return ThumbnailVariantPackage(
        frames=frames, variants=variants, source="fallback" if reason else "heuristic", fallback_reason=reason
    )


def _variant_prompt(
    transcript: Transcript,
    frames: list[ThumbnailFrame],
    context: str | None,
    titles: list[TitleSuggestion],
    brief: PackagingBrief | None,
) -> str:
    title_block = "\n".join(
        f'{i}. [{STRATEGIES[i] if i < len(STRATEGIES) else "extra"}] "{t.title}" — {t.reason}' for i, t in enumerate(titles)
    ) or "(no title candidates supplied)"
    brief_json = (brief or PackagingBrief()).model_dump_json(indent=2)
    return f"""You are pairing each of the following YouTube titles with ONE coherent thumbnail concept, so that title and thumbnail together form a single truthful package.

Use ONLY information supported by the packaging brief and transcript context below. You cannot see the candidate frames, so never claim a facial expression, object or visual detail the nearby transcript doesn't support. Choose, for each title, the candidate timestamp whose nearby content best matches that title's angle.

Return exactly one valid JSON object and nothing else.

SCHEMA
{{"variants":[{{"strategy":"search","overlay_text":"...","visual_rationale":"...","audience_promise":"...","frame_timestamp":0.0}},{{"strategy":"browse","overlay_text":"...","visual_rationale":"...","audience_promise":"...","frame_timestamp":0.0}},{{"strategy":"outcome","overlay_text":"...","visual_rationale":"...","audience_promise":"...","frame_timestamp":0.0}}]}}

RULES
- Return exactly one variant per strategy: search, browse, outcome — matching the title already written for that strategy.
- The overlay must ADD information, evidence or tension beyond the paired title. Never restate or closely paraphrase the title.
- overlay_text: 0-{OVERLAY_MAX_WORDS} words, empty allowed when the frame alone is strong enough.
- visual_rationale: one sentence grounding the frame choice in supported evidence (why this frame fits this title/strategy).
- audience_promise: one short phrase describing the truthful payoff a viewer gets, drawn from the packaging brief's viewer_payoff/result_or_conclusion — never invented.
- Never imply anything listed under must_not_imply in the packaging brief.
- Avoid generic clickbait phrasing ("You Won't Believe", "Shocking", "Secret", "Game Changer").

TITLES (one variant per row, in strategy order)
{title_block}

CREATOR CONTEXT
{context or '(none supplied)'}

PACKAGING BRIEF
{brief_json}

CANDIDATE FRAME CONTEXT
{_frame_contexts(transcript, frames)}

WHOLE-VIDEO TRANSCRIPT OVERVIEW
{_transcript_overview(transcript)}"""


def _opening_delivers_promise(transcript: Transcript, brief: PackagingBrief | None, window: float = 60.0) -> bool:
    """Loose keyword-overlap check: does the opening 30-60s actually touch on the
    promised topic/payoff, or does the video bury it? Never a strict requirement —
    only produces a warning, since short/atypical intros are common and valid."""
    if not brief or not (brief.core_topic or brief.viewer_payoff):
        return True
    opener = " ".join(s.text for s in transcript.segments if s.start <= window).lower()
    if not opener:
        return True
    keywords = re.findall(r"[a-z0-9]{4,}", f"{brief.core_topic} {brief.viewer_payoff}".lower())
    if not keywords:
        return True
    return any(keyword in opener for keyword in keywords)


def validate_variant(
    variant: ThumbnailVariant,
    brief: PackagingBrief | None,
    *,
    transcript: Transcript | None = None,
    prohibited_phrases: list[str] | None = None,
) -> list[str]:
    """Non-fatal warnings: visible to the creator but never block manual export."""
    warnings: list[str] = []
    title = variant.title.strip()
    overlay = variant.overlay_text.strip()

    if len(title) > 100:
        warnings.append("Title exceeds YouTube's 100 character limit")
    if len(overlay) > OVERLAY_MAX_CHARS:
        warnings.append("Overlay text is long and may not fit legibly at small size")
    if overlay and title and overlay.lower() in title.lower():
        warnings.append(OVERLAY_DUP_WARNING)
    if not variant.frame_path:
        warnings.append("No frame selected; export will use a text-only placeholder")

    haystack = f"{title} {overlay}".lower()
    if brief:
        for claim in brief.must_not_imply:
            claim_clean = claim.strip().lower()
            if claim_clean and claim_clean in haystack:
                warnings.append(f"Conflicts with must_not_imply: {claim.strip()}")
    for phrase in prohibited_phrases or []:
        phrase_clean = phrase.strip().lower()
        if phrase_clean and phrase_clean in haystack:
            warnings.append(f"Uses a channel-prohibited phrase: {phrase.strip()}")
    if transcript is not None and not _opening_delivers_promise(transcript, brief):
        warnings.append("Opening 30-60s may not yet deliver the promised topic/payoff")
    return warnings


def migrate_legacy_package(package: ThumbnailPackage, titles: list[TitleSuggestion]) -> ThumbnailVariantPackage:
    """Adapt an old thumbnail_ideas.json (ThumbnailIdea list, unpaired with titles)
    into the versioned variant schema so previous sessions remain loadable."""
    variants = []
    for index, idea in enumerate(package.ideas[:3]):
        strategy = STRATEGIES[index] if index < len(STRATEGIES) else "search"
        title = titles[index].title if index < len(titles) else ""
        variants.append(
            ThumbnailVariant(
                variant_id=_new_variant_id(),
                strategy=strategy,
                title=title,
                title_suggestion_index=index if index < len(titles) else None,
                frame_timestamp=idea.frame_timestamp,
                frame_path=idea.frame_path,
                overlay_text=idea.overlay_text,
                visual_rationale=idea.visual_description,
                source=package.source,
                model=package.model,
                backend=package.backend,
                fallback_reason=package.fallback_reason,
            )
        )
    return ThumbnailVariantPackage(
        frames=package.frames,
        variants=variants,
        source=package.source,
        model=package.model,
        backend=package.backend,
        fallback_reason=package.fallback_reason,
    )


def generate_thumbnail_variants(
    transcript: Transcript,
    frames: list[ThumbnailFrame],
    *,
    model: str | None,
    context: str | None = None,
    backend: str = "ollama",
    call: Callable[[str, str], dict] | None = None,
    titles: list[TitleSuggestion],
    brief: PackagingBrief | None = None,
    prohibited_phrases: list[str] | None = None,
) -> ThumbnailVariantPackage:
    """Generate exactly one variant per title strategy (search/browse/outcome)."""
    call = call or _backend_call(backend)
    if not frames:
        package = heuristic_thumbnail_variants(
            frames, transcript, titles, brief=brief, reason="No candidate thumbnail frames were extracted"
        )
    elif not model:
        package = heuristic_thumbnail_variants(frames, transcript, titles, brief=brief)
    else:
        try:
            raw = call(model, _variant_prompt(transcript, frames, context, titles, brief))
            raw_variants = raw.get("variants", []) if isinstance(raw, dict) else []
            by_strategy = {str(item.get("strategy", "")).strip().lower(): item for item in raw_variants}
            variants = []
            for index, strategy in enumerate(STRATEGIES):
                item = by_strategy.get(strategy)
                if item is None:
                    raise ValueError(f"model did not return a '{strategy}' variant")
                overlay_text = _safe_overlay(
                    str(item.get("overlay_text", "")), max_words=OVERLAY_MAX_WORDS, max_chars=OVERLAY_MAX_CHARS
                )
                frame = _nearest_frame(frames, item.get("frame_timestamp"))
                title = titles[index].title if index < len(titles) else ""
                variants.append(
                    ThumbnailVariant(
                        variant_id=_new_variant_id(),
                        strategy=strategy,
                        title=title,
                        title_suggestion_index=index if index < len(titles) else None,
                        frame_timestamp=frame.timestamp if frame else None,
                        frame_path=frame.path if frame else None,
                        overlay_text=overlay_text,
                        visual_rationale=str(item.get("visual_rationale", "")).strip(),
                        audience_promise=str(item.get("audience_promise", "")).strip(),
                        source="llm",
                        model=model,
                        backend=backend,
                    )
                )
            package = ThumbnailVariantPackage(frames=frames, variants=variants, source="llm", model=model, backend=backend)
        except (Exception, ValidationError) as exc:
            package = heuristic_thumbnail_variants(
                frames, transcript, titles, brief=brief, reason=f"Local thumbnail model unavailable or invalid: {exc}"
            )
            package.model = model
            package.backend = backend

    for variant in package.variants:
        variant.warnings = validate_variant(
            variant, brief, transcript=transcript, prohibited_phrases=prohibited_phrases
        )
    return package


def variants_to_legacy_package(package: ThumbnailVariantPackage) -> ThumbnailPackage:
    """Project the versioned variant package back down to the old ThumbnailIdea
    schema so `thumbnail_ideas.json`/`.txt` (and older web UI clients) keep working."""
    ideas = [
        ThumbnailIdea(
            concept=f"{STRATEGY_LABELS.get(variant.strategy, variant.strategy.title())}: {variant.title}".strip(": "),
            overlay_text=variant.overlay_text,
            visual_description=variant.visual_rationale,
            frame_timestamp=variant.frame_timestamp,
            frame_path=variant.frame_path,
            image_prompt=variant.audience_promise,
        )
        for variant in package.variants
    ]
    return ThumbnailPackage(
        frames=package.frames,
        ideas=ideas,
        source=package.source,
        model=package.model,
        backend=package.backend,
        fallback_reason=package.fallback_reason,
    )
