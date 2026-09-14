from __future__ import annotations

import math
import re

from youtube_ready.models import Chapter, Transcript

MIN_CHAPTER_SECONDS = 10.0
MIN_CHAPTER_COUNT = 3
_SPACE_RE = re.compile(r"\s+")

_LEADING_FILLER_RE = re.compile(
    r"^(?:so|and|but|okay|ok|well|um|uh|because|then|right|now|alright|actually|basically|"
    r"literally|essentially|obviously|like|yeah|here's|here is|this is|that's|that is)[,.!\s]+",
    re.IGNORECASE,
)
_TRAILING_STOPWORDS = {
    "a", "an", "the", "and", "but", "or", "so", "to", "of", "with", "in", "on", "at", "for",
    "as", "is", "are", "was", "that", "this", "it", "if", "by", "from", "when", "while",
    "they", "their", "your", "his", "her", "we", "you", "i", "he", "she",
}
_LOWERCASE_TITLE_WORDS = {
    "a", "an", "the", "and", "but", "or", "nor", "for", "so", "yet",
    "of", "in", "on", "at", "to", "by", "with", "as", "vs",
}


def _title_case(text: str) -> str:
    words = text.split()
    result = []
    for index, word in enumerate(words):
        lower = word.lower()
        if 0 < index < len(words) - 1 and lower in _LOWERCASE_TITLE_WORDS:
            result.append(lower)
        elif word.isupper() and len(word) > 1:
            result.append(word)
        else:
            result.append(lower[:1].upper() + lower[1:] if lower else word)
    return " ".join(result)


def format_chapter_time(seconds: float) -> str:
    total = max(0, int(seconds))
    hours, remainder = divmod(total, 3600)
    minutes, secs = divmod(remainder, 60)
    if hours:
        return f"{hours}:{minutes:02d}:{secs:02d}"
    return f"{minutes:02d}:{secs:02d}"


def _clean_title(title: str, fallback: str) -> str:
    value = _SPACE_RE.sub(" ", title).strip(" -–—:,.\n\t")
    return (value or fallback)[:100]


def chapters_are_valid(chapters: list[Chapter], duration: float) -> bool:
    if len(chapters) < MIN_CHAPTER_COUNT or duration < MIN_CHAPTER_COUNT * MIN_CHAPTER_SECONDS:
        return False
    if abs(chapters[0].start) > 0.001:
        return False
    starts = [chapter.start for chapter in chapters]
    if starts != sorted(starts) or len(set(starts)) != len(starts):
        return False
    boundaries = starts + [duration]
    return all(
        boundaries[index + 1] - boundaries[index] >= MIN_CHAPTER_SECONDS
        for index in range(len(starts))
    )


def normalize_chapters(proposed: list[Chapter], transcript: Transcript) -> list[Chapter]:
    duration = transcript.duration
    if duration < MIN_CHAPTER_COUNT * MIN_CHAPTER_SECONDS:
        return []
    ordered = sorted(proposed, key=lambda chapter: chapter.start)
    result: list[Chapter] = []
    first_title = ordered[0].title if ordered and ordered[0].start < MIN_CHAPTER_SECONDS else "Introduction"
    result.append(Chapter(start=0.0, title=_clean_title(first_title, "Introduction")))
    for chapter in ordered:
        start = max(0.0, float(int(chapter.start)))
        if start < MIN_CHAPTER_SECONDS or start - result[-1].start < MIN_CHAPTER_SECONDS:
            continue
        if duration - start < MIN_CHAPTER_SECONDS:
            continue
        result.append(Chapter(start=start, title=_clean_title(chapter.title, "Next topic")))
    if chapters_are_valid(result, duration):
        return result
    return fallback_chapters(transcript)


def _title_near(transcript: Transcript, start: float, index: int) -> str:
    candidates = [segment for segment in transcript.segments if segment.end >= start]
    if not candidates:
        return f"Part {index + 1}"
    text = _SPACE_RE.sub(" ", candidates[0].text).strip()
    # Repeatedly drop leading filler phrases ("So okay...", "And that's...") so the
    # title doesn't start mid-thought.
    stripped = text
    for _ in range(4):
        updated = _LEADING_FILLER_RE.sub("", stripped, count=1)
        if updated == stripped:
            break
        stripped = updated
    words = (stripped or text).split()[:6]
    # Trim a trailing dangling conjunction/preposition left by the word cutoff.
    while words and words[-1].lower().strip(".,!?") in _TRAILING_STOPWORDS:
        words.pop()
    title = _title_case(" ".join(words).strip(" -–—:,.?!"))
    if index == 0 and len(title) < 4:
        return "Introduction"
    return _clean_title(title, f"Part {index + 1}")


def fallback_chapters(transcript: Transcript) -> list[Chapter]:
    duration = transcript.duration
    if duration < MIN_CHAPTER_COUNT * MIN_CHAPTER_SECONDS:
        return []
    maximum = int(duration // MIN_CHAPTER_SECONDS)
    count = min(10, maximum, max(MIN_CHAPTER_COUNT, round(duration / 180)))
    interval = duration / count
    chapters = [
        Chapter(start=float(int(index * interval)), title=_title_near(transcript, index * interval, index))
        for index in range(count)
    ]
    chapters[0] = Chapter(start=0.0, title=chapters[0].title or "Introduction")
    if not chapters_are_valid(chapters, duration):
        count = min(maximum, MIN_CHAPTER_COUNT)
        interval = duration / count
        chapters = [
            Chapter(start=float(math.floor(index * interval)), title=_title_near(transcript, index * interval, index))
            for index in range(count)
        ]
    return chapters if chapters_are_valid(chapters, duration) else []


def render_chapters(chapters: list[Chapter]) -> str:
    return "\n".join(f"{format_chapter_time(chapter.start)} {chapter.title}" for chapter in chapters) + (
        "\n" if chapters else ""
    )

