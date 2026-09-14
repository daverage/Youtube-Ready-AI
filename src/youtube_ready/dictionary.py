from __future__ import annotations

import json
import re
from pathlib import Path

from youtube_ready.models import Transcript

_WORD_CHARS_RE = re.compile(r"[^\W_]+", re.UNICODE)


def dictionary_path() -> Path:
    return Path.home() / ".youtube_ready" / "custom_dictionary.json"


def load_corrections(path: Path | None = None) -> dict[str, str]:
    """Load the user's custom spelling corrections (wrong -> right), keyed lowercase."""
    target = path or dictionary_path()
    if not target.is_file():
        return {}
    try:
        data = json.loads(target.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}
    if not isinstance(data, dict):
        return {}
    return {str(k).lower(): str(v) for k, v in data.items() if str(k).strip() and str(v).strip()}


def save_corrections(corrections: dict[str, str], path: Path | None = None) -> None:
    target = path or dictionary_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(corrections, indent=2, sort_keys=True), encoding="utf-8")


def add_correction(wrong: str, right: str, path: Path | None = None) -> dict[str, str]:
    wrong = wrong.strip()
    right = right.strip()
    if not wrong or not right:
        raise ValueError("both the misspelling and the correction are required")
    corrections = load_corrections(path)
    corrections[wrong.lower()] = right
    save_corrections(corrections, path)
    return corrections


def remove_correction(wrong: str, path: Path | None = None) -> dict[str, str]:
    corrections = load_corrections(path)
    corrections.pop(wrong.strip().lower(), None)
    save_corrections(corrections, path)
    return corrections


def _apply_to_text(text: str, corrections: dict[str, str]) -> str:
    if not corrections or not text:
        return text

    def replace(match: re.Match[str]) -> str:
        word = match.group(0)
        replacement = corrections.get(word.lower())
        if replacement is None:
            return word
        if word.isupper() and len(word) > 1:
            return replacement.upper()
        if word[0].isupper():
            return replacement[:1].upper() + replacement[1:]
        return replacement

    return _WORD_CHARS_RE.sub(replace, text)


def apply_corrections(transcript: Transcript, corrections: dict[str, str] | None = None) -> Transcript:
    """Return a new Transcript with custom spelling corrections applied to segment and word text."""
    active = corrections if corrections is not None else load_corrections()
    if not active:
        return transcript
    updated_segments = []
    for segment in transcript.segments:
        updated_words = [word.model_copy(update={"text": _apply_to_text(word.text, active)}) for word in segment.words]
        updated_segments.append(
            segment.model_copy(update={"text": _apply_to_text(segment.text, active), "words": updated_words})
        )
    return transcript.model_copy(update={"segments": updated_segments})
