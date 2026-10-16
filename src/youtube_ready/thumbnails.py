from __future__ import annotations

import math
import re
import shutil
import subprocess
import uuid
from collections.abc import Callable
from pathlib import Path

from pydantic import ValidationError

from youtube_ready.metadata import _backend_call, _extract_json_object
from youtube_ready.models import (
    AssetRecommendation,
    PackagingBrief,
    ThumbnailFrame,
    ThumbnailIdea,
    ThumbnailPackage,
    ThumbnailAdvice,
    ThumbnailAssessment,
    ThumbnailCompositionPlan,
    ThumbnailDiagnosticScores,
    ThumbnailVariant,
    ThumbnailVariantPackage,
    TitleSuggestion,
    Transcript,
    UploadedThumbnailAsset,
)

MAX_CANDIDATE_FRAMES = 28
TOP_FRAME_COUNT = 10
MIN_FRAME_GAP_SECONDS = 3.0
TRANSCRIPT_CONTEXT_CHARS = 14_000
MASTER_WIDTH = 1280
PREVIEW_WIDTH = 320
STRATEGIES = ("search", "browse", "outcome")

# Small/quantized local models frequently don't echo the exact schema string
# ("search"/"browse"/"outcome") even when the prompt spells it out — they
# paraphrase using the UI-facing label instead (e.g. "Clarity/Search",
# "curiosity_browse", "Outcome / Stakes"). Recognize those before giving up.
_STRATEGY_ALIASES: dict[str, str] = {
    "search": "search",
    "clarity": "search",
    "browse": "browse",
    "curiosity": "browse",
    "outcome": "outcome",
    "stakes": "outcome",
}

_PTS_TIME_RE = re.compile(r"pts_time:([\d.]+)")


def _match_strategy(raw: str) -> str | None:
    normalized = re.sub(r"[^a-z]", "", raw.strip().lower())
    for alias, strategy in _STRATEGY_ALIASES.items():
        if alias in normalized:
            return strategy
    return None


def _extract_raw_variants(raw: object) -> list[dict]:
    """Pull the list of per-strategy variant dicts out of whatever shape the
    model actually returned. Some local models put it under a different key
    ("thumbnails", "results", ...) or skip the wrapper object entirely and
    return a bare JSON array, despite the prompt/schema asking for
    {"variants": [...]}."""
    if isinstance(raw, list):
        return [item for item in raw if isinstance(item, dict)]
    if not isinstance(raw, dict):
        return []
    if isinstance(raw.get("variants"), list):
        return [item for item in raw["variants"] if isinstance(item, dict)]
    # Fall back to the first value that looks like a list of variant dicts.
    for value in raw.values():
        if isinstance(value, list) and value and all(isinstance(item, dict) for item in value):
            return value
    return []


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
_EYE_CASCADE = None
_SMILE_CASCADE = None
_BLUR_SHARPNESS_MIN = 15.0
BEST_TAKE_WINDOW_SECONDS = 2.5
BEST_TAKE_SAMPLE_FPS = 4
# A candidate must beat the current frame by this margin (not just noise) to
# be worth swapping to — avoids flip-flopping between near-identical scores.
_BEST_TAKE_IMPROVEMENT_MARGIN = 1.15


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


def _load_face_cascades(cv2) -> None:
    global _FACE_CASCADE, _EYE_CASCADE, _SMILE_CASCADE
    if _FACE_CASCADE is None:
        try:
            _FACE_CASCADE = cv2.CascadeClassifier(cv2.data.haarcascades + "haarcascade_frontalface_default.xml")
        except Exception:  # noqa: BLE001
            _FACE_CASCADE = False
    if _EYE_CASCADE is None:
        try:
            _EYE_CASCADE = cv2.CascadeClassifier(cv2.data.haarcascades + "haarcascade_eye.xml")
        except Exception:  # noqa: BLE001
            _EYE_CASCADE = False
    if _SMILE_CASCADE is None:
        try:
            _SMILE_CASCADE = cv2.CascadeClassifier(cv2.data.haarcascades + "haarcascade_smile.xml")
        except Exception:  # noqa: BLE001
            _SMILE_CASCADE = False


def _face_take_score(cv2, gray) -> float | None:
    """Score how strong a "take" this frame is for an on-camera shot: a bigger,
    sharper face with both eyes detected (facing the camera, not a profile or
    looking away) and a detected smile (open, engaged expression) scores
    higher. Returns None when no face is found — this frame isn't applicable
    to a best-take comparison at all (e.g. a product/screen shot)."""
    if not _FACE_CASCADE:
        return None
    faces = _FACE_CASCADE.detectMultiScale(gray, scaleFactor=1.1, minNeighbors=5, minSize=(40, 40))
    if len(faces) == 0:
        return None
    x, y, w, h = max(faces, key=lambda box: box[2] * box[3])
    face_gray = gray[y : y + h, x : x + w]

    eyes_detected = 0
    if _EYE_CASCADE:
        eyes = _EYE_CASCADE.detectMultiScale(
            face_gray, scaleFactor=1.1, minNeighbors=6, minSize=(max(int(w * 0.12), 8), max(int(h * 0.12), 8))
        )
        eyes_detected = min(len(eyes), 2)

    has_smile = False
    if _SMILE_CASCADE:
        smiles = _SMILE_CASCADE.detectMultiScale(
            face_gray, scaleFactor=1.7, minNeighbors=18, minSize=(max(int(w * 0.25), 8), max(int(h * 0.15), 8))
        )
        has_smile = len(smiles) > 0

    face_area_ratio = (w * h) / max(gray.shape[0] * gray.shape[1], 1)
    face_sharpness = float(cv2.Laplacian(face_gray, cv2.CV_64F).var())

    return (
        eyes_detected * 40.0  # both eyes visible ~ facing the camera, not a profile/looking away
        + (30.0 if has_smile else 0.0)  # a detected smile as a rough "engaged expression" proxy
        + min(face_area_ratio, 0.35) * 200.0  # a bigger, more prominent face
        + math.log1p(max(face_sharpness, 0.0)) * 15.0  # in-focus face specifically, not just the whole frame
    )


def _extract_burst_frames(
    video_path: Path, center: float, window: float, fps: float, output_dir: Path, prefix: str
) -> list[ThumbnailFrame]:
    """Sample a short run of frames around `center` (seconds) at `fps`, using
    input-side seeking so this stays fast even on a long video."""
    start = max(center - window, 0.0)
    duration = window * 2
    output_dir.mkdir(parents=True, exist_ok=True)
    pattern = output_dir / f"{prefix}_%03d.jpg"
    command = [
        "ffmpeg", "-y", "-ss", f"{start:.3f}", "-i", str(video_path), "-t", f"{duration:.3f}",
        "-vf", f"fps={fps},scale={MASTER_WIDTH}:-2", "-qscale:v", "2", str(pattern),
    ]
    subprocess.run(command, capture_output=True, text=True, timeout=120)
    paths = sorted(output_dir.glob(f"{prefix}_*.jpg"))
    # Input-side seeking (`-ss` before `-i`) doesn't reliably preserve the
    # source video's original PTS base across containers/ffmpeg builds — for
    # some inputs `showinfo`'s pts_time comes back relative to the seek point
    # (i.e. starting near 0) rather than absolute. Since frames are sampled at
    # a fixed `fps`, compute the absolute timestamp directly instead of
    # trusting the parsed pts_time.
    frames = []
    for index, path in enumerate(paths):
        frames.append(ThumbnailFrame(timestamp=start + index / fps, path=str(path), source="burst"))
    return frames


def find_better_take(
    video_path: str | Path,
    current_frame_path: str,
    current_timestamp: float,
    output_dir: Path,
    *,
    window: float = BEST_TAKE_WINDOW_SECONDS,
    fps: float = BEST_TAKE_SAMPLE_FPS,
) -> ThumbnailFrame:
    """Look a couple of seconds either side of the current frame for one with
    a stronger on-camera "take": both eyes visible (facing the camera), a
    detected smile, a bigger/sharper face. Local and deterministic — no
    internet, no generative model, just re-scoring nearby real frames from the
    source video with the same face heuristics `select_top_frames` already
    uses. Raises ValueError with a specific reason whenever it can't help
    (no ffmpeg, no OpenCV, no face in the current frame, or nothing nearby
    scores meaningfully better) rather than silently doing nothing."""
    if not _ffmpeg_available():
        raise ValueError("ffmpeg is not available on PATH")
    try:
        import cv2
    except Exception as exc:  # noqa: BLE001
        raise ValueError("OpenCV (install the 'thumbnails' extra) is required to find a better take") from exc

    _load_face_cascades(cv2)
    if not _FACE_CASCADE:
        raise ValueError("face detection is unavailable in this OpenCV install")

    current_image = cv2.imread(current_frame_path) if current_frame_path else None
    if current_image is None:
        raise ValueError("could not read the current frame to compare against")
    current_score = _face_take_score(cv2, cv2.cvtColor(current_image, cv2.COLOR_BGR2GRAY))
    if current_score is None:
        raise ValueError("no face was detected in the current frame — best-take search only applies to on-camera shots")

    burst_dir = Path(output_dir) / "burst"
    candidates = _extract_burst_frames(
        Path(video_path), current_timestamp, window, fps, burst_dir, prefix=f"take{uuid.uuid4().hex[:6]}"
    )
    best: ThumbnailFrame | None = None
    best_score = current_score * _BEST_TAKE_IMPROVEMENT_MARGIN
    for candidate in candidates:
        image = cv2.imread(candidate.path)
        if image is None:
            continue
        score = _face_take_score(cv2, cv2.cvtColor(image, cv2.COLOR_BGR2GRAY))
        if score is not None and score > best_score:
            best_score = score
            best = candidate

    if best is None:
        raise ValueError("this is already the strongest take in the nearby frames — no clearly better one found")

    _attach_dimensions_and_previews([best], Path(output_dir))
    return best


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
STYLE_INTENTS = ("clear", "balanced", "bold")
ASSET_ROLES = (
    "person",
    "product",
    "before",
    "after",
    "result",
    "screenshot",
    "document",
    "chart",
    "logo",
    "environment",
    "alternative_frame",
    "other",
)
# Bare "gemma" (not just "gemma3") so future versions (gemma4, ...) are
# recognized without another edit here — matches metadata.py's _MLX_VLM_MODEL_RE.
VISION_MODEL_RE = re.compile(r"llava|bakllava|vision|moondream|minicpm|qwen.*vl|gemma", re.IGNORECASE)


def _new_variant_id() -> str:
    return uuid.uuid4().hex[:10]


def _heuristic_variant(
    strategy: str,
    index: int,
    titles: list[TitleSuggestion],
    frames: list[ThumbnailFrame],
    brief: PackagingBrief | None,
    style_intent: str,
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
        style_intent=style_intent,
        title=title,
        title_suggestion_index=index if index < len(titles) else None,
        frame_timestamp=frame.timestamp if frame else None,
        frame_path=frame.path if frame else None,
        overlay_text=overlays[strategy],
        visual_rationale=rationales[strategy],
        audience_promise=(brief.viewer_payoff if brief else "") or "",
        source="heuristic",
    )


def _clamp_score(value: float) -> float:
    return max(0.0, min(1.0, round(value, 2)))


def _score_band(value: float, *, inverted: bool = False) -> str:
    score = 1.0 - value if inverted else value
    if score >= 0.72:
        return "good"
    if score >= 0.42:
        return "medium"
    return "poor"


def _classification_prompt() -> str:
    return f"""Classify this uploaded supporting image for a YouTube thumbnail assistant.

Return exactly one valid JSON object and nothing else.

SCHEMA
{{"role":"person|product|before|after|result|screenshot|document|chart|logo|environment|alternative_frame|other","reason":"one short reason"}}

Use "other" if the role is unclear. Do not invent a product, person, result, or before/after state that is not visible in the image."""


def _parse_classification_response(text: str) -> tuple[str, str]:
    import json

    data = json.loads(_extract_json_object(text))
    role = str(data.get("role", "other")).strip().lower()
    reason = str(data.get("reason", "")).strip()
    if role not in ASSET_ROLES:
        role = "other"
    return role, reason


def _classify_thumbnail_asset_with_ollama(path: Path, model: str) -> tuple[str, str] | None:
    import ollama

    response = ollama.chat(
        model=model,
        messages=[{"role": "user", "content": _classification_prompt(), "images": [str(path)]}],
        format="json",
    )
    return _parse_classification_response(response["message"]["content"])


def _classify_thumbnail_asset_with_mlx(path: Path, model: str) -> tuple[str, str] | None:
    from youtube_ready.metadata import _generate_mlx_vlm, _load_mlx

    kind, bundle = _load_mlx(model)
    if kind != "vlm":
        return None
    text = _generate_mlx_vlm(bundle, _classification_prompt(), images=[str(path)])
    return _parse_classification_response(text)


_CLASSIFIER_NAMES: dict[str, str] = {
    "ollama": "_classify_thumbnail_asset_with_ollama",
    "mlx": "_classify_thumbnail_asset_with_mlx",
}


def classify_thumbnail_asset(
    path: str | Path,
    original_filename: str | None = None,
    *,
    model: str | None = None,
    backend: str = "ollama",
) -> UploadedThumbnailAsset:
    """Best-effort local role classification for supporting thumbnail images."""
    path = Path(path)
    source_name = (original_filename or path.name).lower()
    role = "other"
    reason = ""
    classifier_name = _CLASSIFIER_NAMES.get(backend)
    if model and classifier_name is not None and VISION_MODEL_RE.search(model):
        try:
            classified = globals()[classifier_name](path, model)
        except Exception:
            classified = None
        if classified is not None:
            role, reason = classified

    keyword_roles = [
        ("person", ("person", "presenter", "face", "headshot", "portrait", "me", "host")),
        ("product", ("product", "cutout", "item", "device", "gear")),
        ("before", ("before", "old", "start")),
        ("after", ("after", "result", "finished", "final")),
        ("result", ("result", "proof", "outcome")),
        ("screenshot", ("screenshot", "screen", "ui", "app")),
        ("document", ("document", "doc", "paper", "pdf")),
        ("chart", ("chart", "graph", "plot", "analytics")),
        ("logo", ("logo", "brand", "mark")),
        ("environment", ("room", "environment", "setup", "studio", "desk")),
    ]
    if role == "other":
        for candidate, keywords in keyword_roles:
            if any(keyword in source_name for keyword in keywords):
                role = candidate
                reason = f"Classified from filename as {role.replace('_', ' ')}."
                break

    width = height = 0
    try:
        from PIL import Image

        with Image.open(path) as image:
            width, height = image.size
        if role == "other" and width >= 900 and height >= 500:
            role = "alternative_frame"
            reason = "Classified from image dimensions as an alternative frame."
    except Exception:
        pass

    return UploadedThumbnailAsset(
        path=str(path),
        role=role,
        original_filename=original_filename or path.name,
        reason=reason or f"Classified as {role.replace('_', ' ')}.",
        width=width,
        height=height,
    )


def _asset_roles(assets: list[UploadedThumbnailAsset]) -> set[str]:
    return {asset.role for asset in assets if asset.role in ASSET_ROLES}


def _has_relevant_asset(roles: set[str], recommendation_type: str) -> bool:
    recommendation_type = recommendation_type.lower()
    if "product" in recommendation_type:
        return bool(roles & {"product", "result"})
    if "person" in recommendation_type or "presenter" in recommendation_type:
        return "person" in roles
    if "result" in recommendation_type or "after" in recommendation_type:
        return bool(roles & {"result", "after"})
    if "screenshot" in recommendation_type:
        return "screenshot" in roles
    return False


def _assess_thumbnail_inputs(
    variant: ThumbnailVariant,
    frames: list[ThumbnailFrame],
    brief: PackagingBrief | None,
    uploaded_assets: list[UploadedThumbnailAsset],
) -> ThumbnailAssessment:
    frame = _nearest_frame(frames, variant.frame_timestamp)
    if frame is None and variant.frame_path:
        frame = next((item for item in frames if item.path == variant.frame_path), None)
    source_width = frame.width if frame else 0
    source_height = frame.height if frame else 0
    has_source_image = frame is not None or bool(variant.frame_path)
    if frame is None and variant.frame_path:
        try:
            from PIL import Image

            with Image.open(variant.frame_path) as image:
                source_width, source_height = image.size
        except Exception:
            pass

    sharpness = _clamp_score((frame.sharpness or 0.0) / 70.0) if frame else 0.0
    resolution = _clamp_score(min((source_width or 0) / 1280.0, (source_height or 0) / 720.0))
    frame_quality = max(sharpness, resolution)
    negative_space = _clamp_score(frame.negative_space_score if frame else 0.0)
    human = 0.78 if frame and frame.has_face else 0.0
    roles = _asset_roles(uploaded_assets)
    product = 0.78 if roles & {"product", "result"} else (0.45 if brief and brief.primary_subjects else 0.25)
    clutter = _clamp_score(1.0 - ((negative_space + frame_quality) / 2.0)) if frame else 0.8
    text_readability = _clamp_score((negative_space * 0.65) + (frame_quality * 0.35))
    hierarchy = _clamp_score(max(human, product, frame_quality * 0.8) - (clutter * 0.2))
    curiosity = 0.7 if variant.strategy in {"browse", "outcome"} else 0.55
    potential = _clamp_score((frame_quality + hierarchy + text_readability + curiosity) / 4.0)

    strengths: list[str] = []
    weaknesses: list[str] = []
    missing: list[str] = []
    recommendations: list[AssetRecommendation] = []

    if has_source_image:
        strengths.append("A source frame is available, so generation can continue without extra uploads.")
    else:
        weaknesses.append("No candidate frame is available, so export will rely on a placeholder.")
    if frame_quality >= 0.55:
        strengths.append("The selected frame has enough quality for a truthful video-only thumbnail.")
    else:
        weaknesses.append("The selected frame may soften if cropped aggressively.")
    if human >= 0.7:
        strengths.append("A visible person can give the thumbnail a human anchor.")
    elif "person" not in roles:
        missing.append("presenter cutout or clear person photo")
    if product < 0.6 and not _has_relevant_asset(roles, "product_cutout") and brief and (brief.primary_subjects or brief.core_topic):
        weaknesses.append("The main subject may not be prominent enough as a visual anchor.")
        missing.append("clean product or subject image")
        recommendations.append(
            AssetRecommendation(
                type="product_cutout",
                priority="high",
                reason="Would allow the main subject to become larger and clearer without inventing details.",
            )
        )
    if clutter >= 0.55:
        weaknesses.append("The background may compete with text or the main subject.")
    if text_readability < 0.45:
        weaknesses.append("There is limited clean space for readable overlay text.")

    recommendations = [rec for rec in recommendations if not _has_relevant_asset(roles, rec.type)]

    if not uploaded_assets and not recommendations:
        highest = "No extra assets available. We'll optimise the strongest video frame."
    elif recommendations:
        highest = recommendations[0].reason
    else:
        highest = "Use the current assets; no additional upload is clearly needed."

    return ThumbnailAssessment(
        frame_quality=_score_band(frame_quality),
        subject_clarity=_score_band(hierarchy),
        product_visibility=_score_band(product),
        person_visibility=_score_band(human),
        background_clutter=_score_band(clutter, inverted=True),
        negative_space=_score_band(negative_space),
        text_readability_potential=_score_band(text_readability),
        cutout_potential="good" if roles & {"person", "product", "result"} else "medium",
        strengths=strengths,
        weaknesses=weaknesses,
        missing_assets=missing,
        recommended_uploads=recommendations,
        highest_value_change=highest,
        scores=ThumbnailDiagnosticScores(
            subject_clarity=hierarchy,
            visual_hierarchy=hierarchy,
            product_prominence=product,
            human_prominence=human,
            background_clutter=clutter,
            text_readability=text_readability,
            negative_space=negative_space,
            mobile_legibility=text_readability,
            authenticity=1.0,
            curiosity_or_value=curiosity,
            thumbnail_potential=potential,
        ),
    )


def _composition_plan(
    variant: ThumbnailVariant,
    assessment: ThumbnailAssessment,
    brief: PackagingBrief | None,
    uploaded_assets: list[UploadedThumbnailAsset],
) -> ThumbnailCompositionPlan:
    roles = _asset_roles(uploaded_assets)
    subject = (brief.core_topic if brief else "") or (brief.primary_subjects[0] if brief and brief.primary_subjects else "") or variant.title
    product_scale = "large" if "product" in roles or variant.style_intent == "bold" else "medium"
    person_scale = "medium" if "person" in roles or assessment.person_visibility == "good" else "natural"
    layout = "product_or_subject_led"
    if "person" in roles and roles & {"product", "result", "screenshot"}:
        layout = "presenter_left_subject_right"
    elif assessment.person_visibility == "good":
        layout = "person_led_with_text_space"
    elif roles & {"screenshot", "chart", "result"}:
        layout = "evidence_led"

    return ThumbnailCompositionPlan(
        strategy=variant.strategy,
        primary_subject=subject,
        secondary_subject="presenter" if assessment.person_visibility == "good" or "person" in roles else "",
        visual_anchor=subject,
        secondary_anchor="presenter" if assessment.person_visibility == "good" or "person" in roles else "",
        layout=layout,
        crop_instruction={
            "clear": "Use a restrained 16:9 crop that preserves context.",
            "balanced": "Crop moderately around the strongest subject while preserving authenticity.",
            "bold": "Crop tightly and enlarge the main subject when source quality allows.",
        }.get(variant.style_intent, "Crop moderately around the strongest subject while preserving authenticity."),
        background_treatment="blur_and_darken" if assessment.background_clutter in {"poor", "medium"} else "subtle_contrast",
        product_scale=product_scale,
        person_scale=person_scale,
        overlay_text=variant.overlay_text,
        text_position=variant.text.position,
        text_treatment={
            "clear": "restrained_high_contrast",
            "balanced": "large_bold_high_contrast",
            "bold": "oversized_bold_stroked",
        }.get(variant.style_intent, "large_bold_high_contrast"),
        remove_or_suppress=["background clutter"] if assessment.background_clutter in {"poor", "medium"} else [],
        supporting_asset_roles=_planned_supporting_asset_roles(uploaded_assets, layout),
    )


def _planned_supporting_asset_roles(uploaded_assets: list[UploadedThumbnailAsset], layout: str) -> list[str]:
    roles = _asset_roles(uploaded_assets)
    if not roles:
        return []
    layout = layout.lower()
    if "product" in roles:
        return ["product"]
    if "result" in roles:
        return ["result"]
    if "screenshot" in roles:
        return ["screenshot"]
    if "chart" in roles:
        return ["chart"]
    if "after" in roles:
        return ["after"]
    if "before" in roles:
        return ["before"]
    if "person" in roles and any(word in layout for word in ("presenter", "person", "human")):
        return ["person"]
    if "logo" in roles:
        return ["logo"]
    return []


def _edit_prompt(variant: ThumbnailVariant, plan: ThumbnailCompositionPlan, brief: PackagingBrief | None) -> str:
    guardrails = [
        "Editorial transformation allowed; factual transformation prohibited.",
        "Do not invent different products, branding, results, reactions, before/after states, or unsupported claims.",
    ]
    if brief and brief.must_not_imply:
        guardrails.append("Must not imply: " + "; ".join(brief.must_not_imply))
    return (
        f"Create a truthful 16:9 YouTube thumbnail for the title: {variant.title!r}. "
        f"Style intent: {variant.style_intent}. Layout: {plan.layout}. "
        f"Visual anchor: {plan.visual_anchor or 'selected video frame'}. Secondary anchor: {plan.secondary_anchor or 'none'}. "
        f"Crop: {plan.crop_instruction}. Background: {plan.background_treatment}. "
        f"Overlay text: {variant.overlay_text!r} at {plan.text_position} using {plan.text_treatment}. "
        f"Suppress: {', '.join(plan.remove_or_suppress) or 'none'}. "
        + " ".join(guardrails)
    )


def _vision_asset_paths(variant: ThumbnailVariant, limit: int = 4) -> list[str]:
    paths: list[str] = []
    if variant.frame_path and Path(variant.frame_path).is_file():
        paths.append(variant.frame_path)
    assets = variant.source_assets.get("uploaded_assets", []) if isinstance(variant.source_assets, dict) else []
    for asset in assets:
        if not isinstance(asset, dict):
            continue
        path = str(asset.get("path") or "")
        if path and Path(path).is_file() and path not in paths:
            paths.append(path)
        if len(paths) >= limit:
            break
    return paths


def _vision_assessment_prompt(variant: ThumbnailVariant, brief: PackagingBrief | None) -> str:
    return f"""Visually assess these source images for a truthful YouTube thumbnail.

The first image is the selected source frame when present. Later images are optional supporting assets. Use only what is visible in the images and supported by the packaging brief/title.

Return exactly one valid JSON object and nothing else.

SCHEMA
{{"assessment":{{"frame_quality":"good|medium|poor","subject_clarity":"good|medium|poor","product_visibility":"good|medium|poor","person_visibility":"good|medium|poor","background_clutter":"good|medium|poor","negative_space":"good|medium|poor","text_readability_potential":"good|medium|poor","cutout_potential":"good|medium|poor","strengths":["..."],"weaknesses":["..."],"missing_assets":["..."],"recommended_uploads":[{{"type":"product_cutout","priority":"high|medium|low","reason":"..."}}],"highest_value_change":"...","scores":{{"subject_clarity":0.0,"visual_hierarchy":0.0,"product_prominence":0.0,"human_prominence":0.0,"background_clutter":0.0,"text_readability":0.0,"negative_space":0.0,"mobile_legibility":0.0,"authenticity":1.0,"curiosity_or_value":0.0,"thumbnail_potential":0.0}}}},"composition":{{"primary_subject":"...","secondary_subject":"...","visual_anchor":"...","secondary_anchor":"...","layout":"...","crop_instruction":"...","background_treatment":"blur_and_darken|subtle_contrast","product_scale":"natural|medium|large","person_scale":"natural|medium|large","overlay_text":"...","text_position":"top|bottom|center","text_treatment":"...","remove_or_suppress":["..."],"supporting_asset_roles":["product"]}},"advice":{{"overall":"...","strengths":["..."],"weaknesses":["..."],"recommended_uploads":[{{"type":"product_cutout","priority":"high|medium|low","reason":"..."}}],"highest_value_change":"..."}}}}

RULES
- Editorial transformation allowed; factual transformation prohibited.
- Do not invent products, branding, outcomes, emotional reactions, or before/after states.
- Recommend at most one optional upload, only when it would materially improve this thumbnail.
- Use supporting_asset_roles to name uploaded asset roles that should appear in the final composition; leave it empty when uploads do not improve the thumbnail.

TITLE
{variant.title}

OVERLAY
{variant.overlay_text}

STYLE
{variant.style_intent}

PACKAGING BRIEF
{(brief or PackagingBrief()).model_dump_json(indent=2)}"""


def _parse_vision_assessment_response(
    text: str,
) -> tuple[ThumbnailAssessment | None, ThumbnailCompositionPlan | None, ThumbnailAdvice | None]:
    import json

    data = json.loads(_extract_json_object(text))
    assessment = ThumbnailAssessment.model_validate(data["assessment"]) if isinstance(data.get("assessment"), dict) else None
    composition = (
        ThumbnailCompositionPlan.model_validate(data["composition"]) if isinstance(data.get("composition"), dict) else None
    )
    advice = ThumbnailAdvice.model_validate(data["advice"]) if isinstance(data.get("advice"), dict) else None
    return assessment, composition, advice


def _ollama_vision_thumbnail_assessment(
    model: str,
    variant: ThumbnailVariant,
    brief: PackagingBrief | None,
    *,
    images: list[str] | None = None,
) -> tuple[ThumbnailAssessment | None, ThumbnailCompositionPlan | None, ThumbnailAdvice | None]:
    import ollama

    images = images if images is not None else _vision_asset_paths(variant)
    if not images:
        return None, None, None
    prompt = _vision_assessment_prompt(variant, brief)
    response = ollama.chat(
        model=model,
        messages=[{"role": "user", "content": prompt, "images": images}],
        format="json",
    )
    return _parse_vision_assessment_response(response["message"]["content"])


def _mlx_vision_thumbnail_assessment(
    model: str,
    variant: ThumbnailVariant,
    brief: PackagingBrief | None,
    *,
    images: list[str] | None = None,
) -> tuple[ThumbnailAssessment | None, ThumbnailCompositionPlan | None, ThumbnailAdvice | None]:
    from youtube_ready.metadata import _generate_mlx_vlm, _load_mlx

    images = images if images is not None else _vision_asset_paths(variant)
    if not images:
        return None, None, None
    kind, bundle = _load_mlx(model)
    if kind != "vlm":
        return None, None, None
    prompt = _vision_assessment_prompt(variant, brief)
    text = _generate_mlx_vlm(bundle, prompt, images=images)
    return _parse_vision_assessment_response(text)


_VISION_ASSESSOR_NAMES: dict[str, str] = {
    "ollama": "_ollama_vision_thumbnail_assessment",
    "mlx": "_mlx_vision_thumbnail_assessment",
}


def _enrich_variants_with_vision(
    package: ThumbnailVariantPackage,
    model: str | None,
    backend: str,
    brief: PackagingBrief | None,
    on_progress: Callable[[str], None] | None = None,
) -> None:
    assessor_name = _VISION_ASSESSOR_NAMES.get(backend)
    if not model or assessor_name is None or not VISION_MODEL_RE.search(model):
        return
    # Looked up by name (rather than captured at import time) so tests can
    # monkeypatch `thumbnails._ollama_vision_thumbnail_assessment` etc.
    assess = globals()[assessor_name]
    total = len(package.variants)
    for index, variant in enumerate(package.variants, start=1):
        if on_progress:
            on_progress(f"Checking the {variant.strategy} thumbnail's images with {model} ({index}/{total})")
        try:
            assessment, composition, advice = assess(model, variant, brief)
        except Exception:
            continue
        if assessment is not None:
            variant.assessment = assessment
        if composition is not None:
            variant.composition = composition
        if advice is not None:
            variant.advice = advice
        if assessment is not None or composition is not None or advice is not None:
            variant.visual_verified = True


def recheck_rendered_thumbnail(
    model: str | None,
    backend: str,
    variant: ThumbnailVariant,
    brief: PackagingBrief | None,
) -> ThumbnailAssessment:
    """Vision-assess the actual rendered composite (crop, background treatment,
    overlay text and any pasted supporting asset all baked in) rather than the
    raw source materials `_assess_thumbnail_inputs`/vision-enrichment look at
    before rendering. This is what a "recheck" after adjusting the composition
    should measure: is the *finished* thumbnail actually good, not just the
    ingredients that went into it."""
    if not variant.rendered_path or not Path(variant.rendered_path).is_file():
        raise ValueError("render this thumbnail before rechecking it")
    if not model or not VISION_MODEL_RE.search(model):
        raise ValueError("rechecking needs a vision-capable local model (e.g. llava, qwen-vl, gemma3)")
    assessor_name = _VISION_ASSESSOR_NAMES.get(backend)
    if assessor_name is None:
        raise ValueError(f"unsupported backend for rechecking: {backend!r}")
    assess = globals()[assessor_name]
    try:
        assessment, _composition, _advice = assess(model, variant, brief, images=[variant.rendered_path])
    except Exception as exc:
        # Unlike `_enrich_variants_with_vision` (which silently skips a failed
        # variant during planning since heuristic data is already in place),
        # a recheck is a call the user explicitly asked for — swallowing the
        # error here would surface as an opaque HTTP 500 in the web UI instead
        # of a message explaining what went wrong.
        raise ValueError(f"vision recheck failed: {exc}") from exc
    if assessment is None:
        raise ValueError("the vision model did not return a usable assessment")
    return assessment


def _attach_assistant_fields(
    package: ThumbnailVariantPackage,
    transcript: Transcript,
    brief: PackagingBrief | None,
    *,
    preserve_existing: bool = False,
) -> ThumbnailVariantPackage:
    for variant in package.variants:
        assessment = (
            variant.assessment
            if preserve_existing and variant.assessment is not None
            else _assess_thumbnail_inputs(variant, package.frames, brief, package.uploaded_assets)
        )
        plan = (
            variant.composition
            if preserve_existing and variant.composition is not None
            else _composition_plan(variant, assessment, brief, package.uploaded_assets)
        )
        plan.strategy = variant.strategy
        plan.overlay_text = variant.overlay_text
        plan.text_position = variant.text.position
        if package.uploaded_assets and not plan.supporting_asset_roles:
            plan.supporting_asset_roles = _planned_supporting_asset_roles(package.uploaded_assets, plan.layout)
        variant.assessment = assessment
        variant.composition = plan
        if not preserve_existing:
            variant.visual_verified = False
        if not preserve_existing or variant.advice is None:
            variant.advice = ThumbnailAdvice(
                overall=variant.visual_rationale or "Use the current source material to build the clearest truthful thumbnail.",
                strengths=assessment.strengths,
                weaknesses=assessment.weaknesses,
                recommended_uploads=assessment.recommended_uploads,
                highest_value_change=assessment.highest_value_change,
            )
        variant.source_assets = {
            "frame": variant.frame_path,
            "uploaded_assets": [asset.model_dump() for asset in package.uploaded_assets],
        }
        variant.edit_prompt = _edit_prompt(variant, plan, brief)
        variant.fallback_plan = (
            "Optimise the strongest video frame with a truthful crop and minimal text."
            if not package.uploaded_assets
            else "Use uploaded assets only when they strengthen the truthful composition."
        )
    return package


def refresh_thumbnail_assistant_fields(
    package: ThumbnailVariantPackage,
    transcript: Transcript,
    brief: PackagingBrief | None = None,
) -> ThumbnailVariantPackage:
    """Recompute assessment, plan, advice and edit prompts after creator edits."""
    return _attach_assistant_fields(package, transcript, brief)


def heuristic_thumbnail_variants(
    frames: list[ThumbnailFrame],
    transcript: Transcript,
    titles: list[TitleSuggestion],
    *,
    brief: PackagingBrief | None = None,
    reason: str | None = None,
    style_intent: str = "balanced",
    uploaded_assets: list[UploadedThumbnailAsset] | None = None,
) -> ThumbnailVariantPackage:
    style_intent = style_intent if style_intent in STYLE_INTENTS else "balanced"
    variants = [_heuristic_variant(strategy, i, titles, frames, brief, style_intent) for i, strategy in enumerate(STRATEGIES)]
    package = ThumbnailVariantPackage(
        frames=frames,
        variants=variants,
        uploaded_assets=uploaded_assets or [],
        style_intent=style_intent,
        source="fallback" if reason else "heuristic",
        fallback_reason=reason,
    )
    return _attach_assistant_fields(package, transcript, brief)


def _variant_prompt(
    transcript: Transcript,
    frames: list[ThumbnailFrame],
    context: str | None,
    titles: list[TitleSuggestion],
    brief: PackagingBrief | None,
    style_intent: str,
    uploaded_assets: list[UploadedThumbnailAsset],
) -> str:
    title_block = "\n".join(
        f'{i}. [{STRATEGIES[i] if i < len(STRATEGIES) else "extra"}] "{t.title}" — {t.reason}' for i, t in enumerate(titles)
    ) or "(no title candidates supplied)"
    brief_json = (brief or PackagingBrief()).model_dump_json(indent=2)
    assets_json = [asset.model_dump() for asset in uploaded_assets]
    return f"""You are pairing each of the following YouTube titles with ONE coherent thumbnail concept, so that title and thumbnail together form a single truthful package.

Use ONLY information supported by the packaging brief and transcript context below. You cannot see the candidate frames, so never claim a facial expression, object or visual detail the nearby transcript doesn't support. Choose, for each title, the candidate timestamp whose nearby content best matches that title's angle.

Return exactly one valid JSON object and nothing else. Keep it small and exact — do not add extra top-level keys, and do not nest anything beyond what's shown here.

SCHEMA
{{"variants":[{{"strategy":"search","overlay_text":"...","visual_rationale":"...","audience_promise":"...","frame_timestamp":0.0}},{{"strategy":"browse","overlay_text":"...","visual_rationale":"...","audience_promise":"...","frame_timestamp":0.0}},{{"strategy":"outcome","overlay_text":"...","visual_rationale":"...","audience_promise":"...","frame_timestamp":0.0}}]}}

RULES
- Return exactly 3 objects in the "variants" array, one per strategy — search, browse, outcome, in that order — each with the "strategy" field set to exactly that word.
- The overlay must ADD information, evidence or tension beyond the paired title. Never restate or closely paraphrase the title.
- overlay_text: 0-{OVERLAY_MAX_WORDS} words, empty allowed when the frame alone is strong enough.
- visual_rationale: one sentence grounding the frame choice in supported evidence (why this frame fits this title/strategy).
- audience_promise: one short phrase describing the truthful payoff a viewer gets, drawn from the packaging brief's viewer_payoff/result_or_conclusion — never invented.
- Never imply anything listed under must_not_imply in the packaging brief.
- Avoid generic clickbait phrasing ("You Won't Believe", "Shocking", "Secret", "Game Changer").
- Style intent is {style_intent!r}: clear means restrained and informative, balanced means moderate hierarchy, bold means stronger crop/contrast/text. The same factual truth rules apply to all styles.
- Factual transformations are prohibited: do not invent products, alter branding, falsify results, fabricate reactions, or create unsupported before/after states.

TITLES (one variant per row, in strategy order)
{title_block}

CREATOR CONTEXT
{context or '(none supplied)'}

PACKAGING BRIEF
{brief_json}

UPLOADED SUPPORTING ASSETS
{assets_json}

CANDIDATE FRAME CONTEXT
{_frame_contexts(transcript, frames)}

WHOLE-VIDEO TRANSCRIPT OVERVIEW
{_transcript_overview(transcript)}"""


OVERLAY_IDEA_COUNT = 5

_STRATEGY_IDEA_BRIEFS = {
    "search": "explain clearly what the video is about, for viewers who searched for it",
    "browse": "create honest curiosity, for viewers scrolling their home feed",
    "outcome": "lead with the result, verdict or payoff the video delivers",
}

_STRATEGY_FALLBACK_IDEAS = {
    "search": ["Full Review", "Explained", "Tested"],
    "browse": ["Worth It?", "What Happens Next", "Not What I Expected"],
    "outcome": ["The Verdict", "The Result", "Final Answer"],
}


def _trim_overlay_idea(text: str) -> str:
    """Like `_safe_overlay`, but keeps a trailing ? or ! — a question is a
    legitimate thumbnail angle, and stripping it changes the meaning."""
    text = re.sub(r"\s+", " ", text).strip(" .,:;-\"'")
    ending = text[-1] if text and text[-1] in "?!" else ""
    words = text.rstrip("?!").split()[:OVERLAY_MAX_WORDS]
    result = " ".join(words)
    if len(result) + len(ending) > OVERLAY_MAX_CHARS:
        result = result[: OVERLAY_MAX_CHARS - len(ending)].rsplit(" ", 1)[0]
    return f"{result}{ending}" if result else ""


def _clean_overlay_ideas(raw: list[object], title: str, exclude: list[str], limit: int) -> list[str]:
    """Trim to overlay limits, drop blanks, duplicates, anything already shown,
    and anything that just repeats the paired title."""
    seen = {text.strip().lower().rstrip("?!") for text in exclude if text and text.strip()}
    title_lower = title.strip().lower()
    ideas: list[str] = []
    for item in raw:
        text = _trim_overlay_idea(str(item or ""))
        key = text.lower().rstrip("?!")
        if not text or key in seen or (title_lower and key in title_lower):
            continue
        seen.add(key)
        ideas.append(text)
        if len(ideas) >= limit:
            break
    return ideas


def heuristic_overlay_ideas(
    variant: ThumbnailVariant,
    brief: PackagingBrief | None,
    exclude: list[str] | None = None,
    limit: int = OVERLAY_IDEA_COUNT,
) -> list[str]:
    """Offline fallback: short phrases lifted from the packaging brief (which is
    itself transcript-derived), then a few safe strategy-specific stock phrases."""
    raw: list[object] = []
    if brief:
        if variant.strategy == "outcome":
            raw += [brief.result_or_conclusion, brief.viewer_payoff, brief.primary_claim]
        elif variant.strategy == "browse":
            raw += [*brief.surprising_points, *brief.contrasts, brief.stakes]
        else:
            raw += [*brief.search_phrases, brief.core_topic, *brief.primary_subjects]
        raw += [*brief.proof_points, *brief.contrasts, *brief.surprising_points, brief.primary_claim]
    raw += _STRATEGY_FALLBACK_IDEAS.get(variant.strategy, [])
    return _clean_overlay_ideas(raw, variant.title, [variant.overlay_text, *(exclude or [])], limit)


def _overlay_ideas_prompt(
    transcript: Transcript, variant: ThumbnailVariant, brief: PackagingBrief | None, avoid: list[str], count: int
) -> str:
    brief_json = (brief or PackagingBrief()).model_dump_json(indent=2)
    avoid_block = "\n".join(f"- {text}" for text in avoid if text) or "(none)"
    return f"""Suggest {count} alternative pieces of overlay text for one YouTube thumbnail.

The thumbnail's job: {_STRATEGY_IDEA_BRIEFS.get(variant.strategy, "make the video's value obvious")}.
It is shown next to this title: "{variant.title}"

Return exactly one JSON object and nothing else: {{"ideas": ["...", "..."]}}

RULES
- Each idea is 1-{OVERLAY_MAX_WORDS} words and at most {OVERLAY_MAX_CHARS} characters, readable at phone size.
- Each idea must ADD information, evidence or tension beyond the title. Never restate the title.
- Make the ideas genuinely different from each other (e.g. a number, a contrast, a verdict, a question).
- Use only facts supported by the packaging brief and transcript. Never imply anything in must_not_imply.
- No generic clickbait ("You Won't Believe", "Shocking", "Secret", "Game Changer").
- Do not repeat any of these already-shown texts:
{avoid_block}

PACKAGING BRIEF
{brief_json}

TRANSCRIPT OVERVIEW
{_transcript_overview(transcript)}"""


def suggest_overlay_ideas(
    transcript: Transcript,
    variant: ThumbnailVariant,
    *,
    brief: PackagingBrief | None,
    model: str | None,
    backend: str = "ollama",
    call: Callable[[str, str], dict] | None = None,
    count: int = OVERLAY_IDEA_COUNT,
) -> list[str]:
    """Fresh alternative overlay texts for one variant, avoiding the current text
    and the ideas already shown. Uses the local model when configured and falls
    back to `heuristic_overlay_ideas` on any failure — never raises."""
    avoid = [variant.overlay_text, *variant.overlay_options]
    if model:
        try:
            call = call or _backend_call(backend)
            raw = call(model, _overlay_ideas_prompt(transcript, variant, brief, avoid, count))
            items = raw.get("ideas") if isinstance(raw, dict) else raw
            if isinstance(items, list):
                ideas = _clean_overlay_ideas(items, variant.title, avoid, count)
                if ideas:
                    return ideas
        except Exception:
            pass
    ideas = heuristic_overlay_ideas(variant, brief, exclude=avoid, limit=count)
    # Every stock phrase already shown: start over rather than return nothing.
    return ideas or heuristic_overlay_ideas(variant, brief, limit=count)


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
    implies_before_after = bool(re.search(r"\bbefore\s*(?:/|and|&|\+|vs\.?|versus)\s*after\b", haystack))
    if implies_before_after:
        asset_roles = {
            str(asset.get("role", ""))
            for asset in variant.source_assets.get("uploaded_assets", [])
            if isinstance(variant.source_assets, dict) and isinstance(asset, dict)
        }
        brief_supports_comparison = bool(brief and (brief.contrasts or brief.result_or_conclusion))
        assets_support_comparison = bool(asset_roles & {"before", "after", "result"})
        if not brief_supports_comparison and not assets_support_comparison:
            warnings.append("Before/after framing needs source evidence or supporting result assets")
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
    style_intent: str = "balanced",
    uploaded_assets: list[UploadedThumbnailAsset] | None = None,
    on_progress: Callable[[str], None] | None = None,
) -> ThumbnailVariantPackage:
    """Generate exactly one variant per title strategy (search/browse/outcome).

    `on_progress` (optional) receives a short human-readable message as each
    slow step starts, so callers like the web UI can show what's happening."""
    call = call or _backend_call(backend)
    report = on_progress or (lambda _message: None)
    style_intent = style_intent if style_intent in STYLE_INTENTS else "balanced"
    uploaded_assets = uploaded_assets or []
    if not frames:
        package = heuristic_thumbnail_variants(
            frames,
            transcript,
            titles,
            brief=brief,
            reason="No candidate thumbnail frames were extracted",
            style_intent=style_intent,
            uploaded_assets=uploaded_assets,
        )
    elif not model:
        package = heuristic_thumbnail_variants(
            frames, transcript, titles, brief=brief, style_intent=style_intent, uploaded_assets=uploaded_assets
        )
    else:
        try:
            report(f"Asking {model} to plan 3 thumbnail concepts (this is usually the slowest step)")
            raw = call(model, _variant_prompt(transcript, frames, context, titles, brief, style_intent, uploaded_assets))
            raw_variants = _extract_raw_variants(raw)
            by_strategy: dict[str, dict] = {}
            unmatched: list[dict] = []
            for item in raw_variants:
                if not isinstance(item, dict):
                    continue
                matched = _match_strategy(str(item.get("strategy", "")))
                if matched and matched not in by_strategy:
                    by_strategy[matched] = item
                else:
                    unmatched.append(item)
            # Last resort: the prompt asks for "one variant per row, in strategy
            # order", so if the number of still-missing strategies exactly
            # matches the number of leftover unmatched items, assume the model
            # just dropped/garbled the `strategy` field rather than the whole
            # variant, and pair them up positionally. Ambiguous counts (e.g. 2
            # missing but 3 leftover) are left alone rather than guessed.
            missing = [strategy for strategy in STRATEGIES if strategy not in by_strategy]
            if missing and len(missing) == len(unmatched):
                by_strategy.update(zip(missing, unmatched))
            variants = []
            for index, strategy in enumerate(STRATEGIES):
                item = by_strategy.get(strategy)
                if item is None:
                    raise ValueError(
                        f"model did not return a '{strategy}' variant "
                        f"(got {len(raw_variants)} variant(s), raw strategy labels: "
                        f"{[str(v.get('strategy')) for v in raw_variants if isinstance(v, dict)]!r}; "
                        f"top-level response keys: {sorted(raw.keys()) if isinstance(raw, dict) else type(raw).__name__!r})"
                    )
                overlay_text = _safe_overlay(
                    str(item.get("overlay_text", "")), max_words=OVERLAY_MAX_WORDS, max_chars=OVERLAY_MAX_CHARS
                )
                frame = _nearest_frame(frames, item.get("frame_timestamp"))
                title = titles[index].title if index < len(titles) else ""
                variants.append(
                    ThumbnailVariant(
                        variant_id=_new_variant_id(),
                        strategy=strategy,
                        style_intent=style_intent,
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
                        assessment=ThumbnailAssessment.model_validate(item["assessment"])
                        if isinstance(item.get("assessment"), dict)
                        else None,
                        composition=ThumbnailCompositionPlan.model_validate(item["composition"])
                        if isinstance(item.get("composition"), dict)
                        else None,
                        advice=ThumbnailAdvice.model_validate(item["advice"]) if isinstance(item.get("advice"), dict) else None,
                    )
                )
            package = ThumbnailVariantPackage(
                frames=frames,
                variants=variants,
                uploaded_assets=uploaded_assets,
                style_intent=style_intent,
                source="llm",
                model=model,
                backend=backend,
            )
        except (Exception, ValidationError) as exc:
            package = heuristic_thumbnail_variants(
                frames,
                transcript,
                titles,
                brief=brief,
                reason=f"Local thumbnail model unavailable or invalid: {exc}",
                style_intent=style_intent,
                uploaded_assets=uploaded_assets,
            )
            package.model = model
            package.backend = backend

    package = _attach_assistant_fields(package, transcript, brief, preserve_existing=package.source == "llm")
    _enrich_variants_with_vision(package, model, backend, brief, on_progress=on_progress)
    package = _attach_assistant_fields(package, transcript, brief, preserve_existing=True)
    report("Checking thumbnail plans against the title and transcript")
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
            image_prompt=variant.edit_prompt or variant.audience_promise,
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
