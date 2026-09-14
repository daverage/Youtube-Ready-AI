from __future__ import annotations

from pathlib import Path

from youtube_ready.models import BrandingProfile, CropBox, ThumbnailVariant

RENDER_WIDTH = 1280
RENDER_HEIGHT = 720

_TEXT_COLOR = (255, 255, 255)
_OUTLINE_COLOR = (0, 0, 0)
_BACKDROP_OPACITY = 140  # 0-255


def _load_font(size: int, font_path: Path | None = None):
    from PIL import ImageFont

    from youtube_ready.branding import resolve_font_path

    try:
        return ImageFont.truetype(str(font_path or resolve_font_path(None)), size)
    except Exception:  # noqa: BLE001
        return ImageFont.load_default()


def _hex_to_rgb(value: str, fallback: tuple[int, int, int]) -> tuple[int, int, int]:
    value = (value or "").strip().lstrip("#")
    if len(value) != 6:
        return fallback
    try:
        return tuple(int(value[i : i + 2], 16) for i in (0, 2, 4))  # type: ignore[return-value]
    except ValueError:
        return fallback


def compute_crop_box(image_size: tuple[int, int], focal_x: float, focal_y: float, zoom: float = 1.0) -> CropBox:
    """Normalized 16:9 crop box, centered on the focal point. zoom=1.0 is the
    largest 16:9 box that fits the image; smaller values zoom in further."""
    w, h = image_size
    target_ratio = RENDER_WIDTH / RENDER_HEIGHT
    if w / h > target_ratio:
        max_h = h
        max_w = int(h * target_ratio)
    else:
        max_w = w
        max_h = int(w / target_ratio)

    zoom = min(max(zoom, 0.3), 1.0)
    crop_w = max(int(max_w * zoom), 16)
    crop_h = max(int(max_h * zoom), 9)
    cx, cy = focal_x * w, focal_y * h
    left = min(max(int(cx - crop_w / 2), 0), max(w - crop_w, 0))
    top = min(max(int(cy - crop_h / 2), 0), max(h - crop_h, 0))
    return CropBox(x=left / w, y=top / h, width=crop_w / w, height=crop_h / h)


def _pixel_box(image_size: tuple[int, int], box: CropBox) -> tuple[int, int, int, int]:
    w, h = image_size
    left = max(0, min(int(box.x * w), w - 1))
    top = max(0, min(int(box.y * h), h - 1))
    right = max(left + 1, min(int((box.x + box.width) * w), w))
    bottom = max(top + 1, min(int((box.y + box.height) * h), h))
    return left, top, right, bottom


def _is_identity_crop(box: CropBox) -> bool:
    return box.x == 0.0 and box.y == 0.0 and box.width == 1.0 and box.height == 1.0


def _wrap_text(draw, text: str, font, max_width: int) -> list[str]:
    words = text.split()
    if not words:
        return []
    lines: list[str] = []
    current = words[0]
    for word in words[1:]:
        trial = f"{current} {word}"
        if draw.textlength(trial, font=font) <= max_width:
            current = trial
        else:
            lines.append(current)
            current = word
    lines.append(current)
    return lines


def _draw_overlay_text(
    image, variant: ThumbnailVariant, backdrop_color: tuple[int, int, int], font_path: Path | None = None
) -> None:
    from PIL import ImageDraw

    text = variant.overlay_text.strip()
    if not text:
        return
    draw = ImageDraw.Draw(image, "RGBA")
    margin = int(variant.text.safe_margin * RENDER_WIDTH)
    max_width = RENDER_WIDTH - 2 * margin

    font_size = 84
    font = _load_font(font_size, font_path)
    lines = _wrap_text(draw, text, font, max_width)
    while font_size > 32 and (len(lines) > 3 or any(draw.textlength(line, font=font) > max_width for line in lines)):
        font_size -= 6
        font = _load_font(font_size, font_path)
        lines = _wrap_text(draw, text, font, max_width)

    line_height = int(font_size * 1.25)
    block_height = line_height * len(lines) + margin
    if variant.text.position == "top":
        block_top = margin // 2
    elif variant.text.position == "center":
        block_top = (RENDER_HEIGHT - block_height) // 2
    else:  # bottom
        block_top = RENDER_HEIGHT - block_height - margin // 2

    draw.rectangle(
        [(0, max(block_top - margin // 4, 0)), (RENDER_WIDTH, min(block_top + block_height, RENDER_HEIGHT))],
        fill=(*backdrop_color, _BACKDROP_OPACITY),
    )

    y = block_top
    outline = max(font_size // 24, 2)
    for line in lines:
        line_width = draw.textlength(line, font=font)
        if variant.text.align == "left":
            x = margin
        elif variant.text.align == "right":
            x = RENDER_WIDTH - margin - line_width
        else:
            x = (RENDER_WIDTH - line_width) / 2
        for dx in range(-outline, outline + 1):
            for dy in range(-outline, outline + 1):
                if dx or dy:
                    draw.text((x + dx, y + dy), line, font=font, fill=_OUTLINE_COLOR)
        draw.text((x, y), line, font=font, fill=_TEXT_COLOR)
        y += line_height


def _draw_logo(image, branding: BrandingProfile | None) -> None:
    if not branding or not branding.logo_path or not Path(branding.logo_path).is_file():
        return
    from PIL import Image

    try:
        with Image.open(branding.logo_path) as logo:
            logo = logo.convert("RGBA")
            target_h = int(RENDER_HEIGHT * 0.12)
            ratio = target_h / max(logo.height, 1)
            logo = logo.resize((max(int(logo.width * ratio), 1), target_h))
            margin = int(RENDER_WIDTH * 0.03)
            image.paste(logo, (RENDER_WIDTH - logo.width - margin, margin), logo)
    except Exception:  # noqa: BLE001
        return


def render_variant(
    variant: ThumbnailVariant, output_path: Path, *, branding: BrandingProfile | None = None
) -> str | None:
    """Render one deterministic 1280x720 image for `variant`. Falls back to a
    plain text-only placeholder when no frame is available or Pillow is missing,
    so a package is always renderable even in a fully offline/no-frame run."""
    try:
        from PIL import Image
    except Exception:  # noqa: BLE001
        return None

    from youtube_ready.branding import resolve_font_path

    output_path.parent.mkdir(parents=True, exist_ok=True)
    placeholder_color = _hex_to_rgb(branding.primary_color if branding else "", (32, 34, 40))
    backdrop_color = (0, 0, 0)
    font_path = resolve_font_path(branding)

    canvas = None
    frame_path = variant.frame_path
    if frame_path and Path(frame_path).is_file():
        try:
            with Image.open(frame_path) as source:
                source = source.convert("RGB")
                box = (
                    variant.crop
                    if not _is_identity_crop(variant.crop)
                    else compute_crop_box(source.size, variant.focal_x, variant.focal_y)
                )
                cropped = source.crop(_pixel_box(source.size, box))
                canvas = cropped.resize((RENDER_WIDTH, RENDER_HEIGHT), Image.LANCZOS)
        except Exception:  # noqa: BLE001
            canvas = None

    if canvas is None:
        canvas = Image.new("RGB", (RENDER_WIDTH, RENDER_HEIGHT), placeholder_color)

    _draw_overlay_text(canvas, variant, backdrop_color, font_path)
    _draw_logo(canvas, branding)
    canvas.save(output_path, "JPEG", quality=92)
    return str(output_path)
