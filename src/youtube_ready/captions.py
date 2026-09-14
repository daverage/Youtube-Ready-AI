from __future__ import annotations

import math
import re
from dataclasses import dataclass

from youtube_ready.models import Transcript

_TAG_RE = re.compile(r"<[^>]*>")


@dataclass(frozen=True)
class Cue:
    start: float
    end: float
    text: str


def _plain_text(text: str) -> str:
    return " ".join(_TAG_RE.sub("", text).replace("-->", "→").split())


def _split_words(words: list[str], parts: int) -> list[str]:
    parts = max(1, min(parts, len(words)))
    result: list[str] = []
    cursor = 0
    for index in range(parts):
        remaining_words = len(words) - cursor
        remaining_parts = parts - index
        take = math.ceil(remaining_words / remaining_parts)
        result.append(" ".join(words[cursor : cursor + take]))
        cursor += take
    return result


def build_cues(transcript: Transcript, *, max_chars: int = 84, max_seconds: float = 7.0) -> list[Cue]:
    cues: list[Cue] = []
    previous_end = 0.0
    for segment in transcript.segments:
        text = _plain_text(segment.text)
        words = text.split()
        if not words:
            continue
        duration = segment.end - segment.start
        part_count = max(1, math.ceil(len(text) / max_chars), math.ceil(duration / max_seconds))
        chunks = _split_words(words, part_count)
        weights = [max(1, len(chunk)) for chunk in chunks]
        cursor = max(segment.start, previous_end)
        usable_end = max(cursor + 0.01, segment.end)
        for index, (chunk, weight) in enumerate(zip(chunks, weights, strict=True)):
            if index == len(chunks) - 1:
                end = usable_end
            else:
                end = cursor + ((usable_end - cursor) * weight / sum(weights[index:]))
            cues.append(Cue(start=cursor, end=max(cursor + 0.01, end), text=chunk))
            cursor = cues[-1].end
        previous_end = cues[-1].end
    return cues


def _timestamp(seconds: float, separator: str) -> str:
    total_ms = max(0, round(seconds * 1000))
    hours, remainder = divmod(total_ms, 3_600_000)
    minutes, remainder = divmod(remainder, 60_000)
    secs, millis = divmod(remainder, 1000)
    return f"{hours:02d}:{minutes:02d}:{secs:02d}{separator}{millis:03d}"


def render_srt(transcript: Transcript) -> str:
    blocks = []
    for index, cue in enumerate(build_cues(transcript), start=1):
        blocks.append(
            f"{index}\n{_timestamp(cue.start, ',')} --> {_timestamp(cue.end, ',')}\n{cue.text}"
        )
    return "\n\n".join(blocks) + ("\n" if blocks else "")


def render_vtt(transcript: Transcript) -> str:
    lines = ["WEBVTT", ""]
    for cue in build_cues(transcript):
        lines.extend([f"{_timestamp(cue.start, '.')} --> {_timestamp(cue.end, '.')}", cue.text, ""])
    return "\n".join(lines)


def render_plain_transcript(transcript: Transcript) -> str:
    return "\n".join(_plain_text(segment.text) for segment in transcript.segments if segment.text.strip()) + "\n"
