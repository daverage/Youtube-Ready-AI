from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from youtube_ready.models import BrandingProfile, ThumbnailFeedbackEntry

_STORE_DIR = Path.home() / ".youtube_ready"
_BUNDLED_FONTS_DIR = Path(__file__).parent / "assets" / "fonts"
_DEFAULT_FONT_FILE = "DejaVuSans-Bold.ttf"
_DEFAULT_FONT_NAME = "DejaVu Sans Bold (default)"
_BUNDLED_FONT = _BUNDLED_FONTS_DIR / _DEFAULT_FONT_FILE
_FONT_SUFFIXES = {".ttf", ".otf", ".ttc"}

# A handful of standard, permissively-licensed defaults so there's real choice
# without requiring a network fetch or a system font scan. All bold weights,
# since bold reads best for thumbnail overlay text at small sizes.
_BUNDLED_FONT_NAMES = {
    _DEFAULT_FONT_FILE: _DEFAULT_FONT_NAME,
    "DejaVuSerif-Bold.ttf": "DejaVu Serif Bold",
    "DejaVuSansMono-Bold.ttf": "DejaVu Sans Mono Bold",
    "STIXGeneralBol.ttf": "STIX Bold Serif",
}


def branding_path() -> Path:
    return _STORE_DIR / "branding_profile.json"


def feedback_path() -> Path:
    return _STORE_DIR / "thumbnail_feedback.json"


def fonts_dir() -> Path:
    return _STORE_DIR / "fonts"


def list_fonts() -> list[dict]:
    """Bundled defaults plus any fonts the creator has added. Font "search" in the
    UI filters this small local list client-side rather than scanning the whole
    system font catalog, which varies wildly by OS and is rarely what's wanted
    for a YouTube thumbnail anyway."""
    fonts = []
    for filename, name in _BUNDLED_FONT_NAMES.items():
        path = _BUNDLED_FONTS_DIR / filename
        if not path.is_file():
            continue
        is_default = filename == _DEFAULT_FONT_FILE
        # The true default's path is None so the profile round-trips to "use the
        # bundled default" rather than pinning an absolute path to it.
        fonts.append({"name": name, "path": None if is_default else str(path), "is_default": is_default})

    directory = fonts_dir()
    if directory.is_dir():
        for path in sorted(directory.iterdir()):
            if path.suffix.lower() in _FONT_SUFFIXES:
                fonts.append({"name": path.stem, "path": str(path), "is_default": False})
    return fonts


def save_font(source_path: Path, filename: str) -> dict:
    suffix = Path(filename).suffix.lower()
    if suffix not in _FONT_SUFFIXES:
        raise ValueError(f"unsupported font file type {suffix!r}; use .ttf, .otf or .ttc")
    directory = fonts_dir()
    directory.mkdir(parents=True, exist_ok=True)
    stem = Path(filename).stem.strip() or "custom-font"
    dest = directory / f"{stem}{suffix}"
    dest.write_bytes(Path(source_path).read_bytes())
    return {"name": dest.stem, "path": str(dest), "is_default": False}


def resolve_font_path(profile: BrandingProfile | None) -> Path:
    if profile and profile.font_path and Path(profile.font_path).is_file():
        return Path(profile.font_path)
    return _BUNDLED_FONT


def load_branding_profile(path: Path | None = None) -> BrandingProfile:
    """Ship a neutral default: branding is an enhancement, never a prerequisite."""
    target = path or branding_path()
    if not target.is_file():
        return BrandingProfile()
    try:
        return BrandingProfile.model_validate_json(target.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError, ValueError):
        return BrandingProfile()


def save_branding_profile(profile: BrandingProfile, path: Path | None = None) -> BrandingProfile:
    target = path or branding_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(profile.model_dump_json(indent=2), encoding="utf-8")
    return profile


def load_feedback(path: Path | None = None) -> list[ThumbnailFeedbackEntry]:
    target = path or feedback_path()
    if not target.is_file():
        return []
    try:
        raw = json.loads(target.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return []
    if not isinstance(raw, list):
        return []
    entries = []
    for item in raw:
        try:
            entries.append(ThumbnailFeedbackEntry.model_validate(item))
        except ValueError:
            continue
    return entries


def record_selection(
    *,
    job_id: str,
    variant_id: str,
    strategy: str,
    title: str,
    youtube_test_result: str | None = None,
    path: Path | None = None,
) -> ThumbnailFeedbackEntry:
    """Record which variant a creator actually chose to publish, and optionally a
    real YouTube test result. Never a predicted/synthetic performance score."""
    entries = load_feedback(path)
    entry = ThumbnailFeedbackEntry(
        job_id=job_id,
        variant_id=variant_id,
        strategy=strategy,
        title=title,
        selected_at=datetime.now(timezone.utc).isoformat(),
        youtube_test_result=youtube_test_result.strip() if youtube_test_result else None,
    )
    entries.append(entry)
    target = path or feedback_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps([e.model_dump() for e in entries], indent=2), encoding="utf-8")
    return entry
