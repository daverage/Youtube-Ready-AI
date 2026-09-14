from __future__ import annotations

from pathlib import Path
from typing import Callable, NamedTuple

from youtube_ready.models import Transcript, TranscriptSegment, Word

try:
    from faster_whisper import WhisperModel
except ModuleNotFoundError:
    WhisperModel = None  # type: ignore[assignment]


class QualityPreset(NamedTuple):
    model: str
    beam_size: int


QUALITY_PRESETS = {
    "fast": QualityPreset("tiny", 1),
    "balanced": QualityPreset("small", 5),
    "accurate": QualityPreset("medium", 5),
}


def transcribe_video(
    video_path: str | Path,
    *,
    quality: str = "balanced",
    model_name: str | None = None,
    language: str | None = None,
    translate: bool = False,
    on_progress: Callable[[str], None] | None = None,
) -> Transcript:
    if quality not in QUALITY_PRESETS:
        raise ValueError(f"unknown quality {quality!r}; choose from: {', '.join(QUALITY_PRESETS)}")
    if WhisperModel is None:
        raise RuntimeError("faster-whisper is not installed; run `pip install -e .`")

    progress = on_progress or (lambda _message: None)
    preset = QUALITY_PRESETS[quality]
    selected_model = model_name or preset.model
    progress(f"Loading local Whisper model: {selected_model}")
    model = WhisperModel(selected_model, device="auto", compute_type="int8")
    progress("Transcribing the complete video locally")
    try:
        segment_stream, info = model.transcribe(
            str(video_path),
            beam_size=preset.beam_size,
            word_timestamps=True,
            vad_filter=True,
            language=language,
            task="translate" if translate else "transcribe",
        )
    except Exception as exc:  # noqa: BLE001 - surface a clear, actionable message instead of a raw decoder errno
        raise RuntimeError(
            f"Could not read '{video_path}' as audio/video ({exc}). "
            "Check that the file is a valid, non-empty media file and that ffmpeg is installed."
        ) from exc

    segments: list[TranscriptSegment] = []
    for item in segment_stream:
        text = item.text.strip()
        if not text:
            continue
        words = [
            Word(start=float(word.start), end=float(word.end), text=word.word)
            for word in (item.words or [])
            if word.start is not None and word.end is not None and word.end > word.start
        ]
        segments.append(
            TranscriptSegment(start=float(item.start), end=float(item.end), text=text, words=words)
        )

    duration = max(
        float(getattr(info, "duration", 0.0) or 0.0),
        max((segment.end for segment in segments), default=0.0),
    )
    return Transcript(language=getattr(info, "language", language), duration=duration, segments=segments)
