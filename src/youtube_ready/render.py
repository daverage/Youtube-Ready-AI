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


_LEGACY_TEXT_TREATMENT = "legacy_strip"


def _draw_overlay_text(
    image, variant: ThumbnailVariant, backdrop_color: tuple[int, int, int], font_path: Path | None = None
) -> None:
    from PIL import Image, ImageDraw

    text = variant.overlay_text.strip()
    if not text:
        return
    draw = ImageDraw.Draw(image, "RGBA")
    margin = int(variant.text.safe_margin * RENDER_WIDTH)
    max_width = RENDER_WIDTH - 2 * margin

    font_size = {"clear": 68, "balanced": 84, "bold": 98}.get(variant.style_intent, 84)
    font = _load_font(font_size, font_path)
    lines = _wrap_text(draw, text, font, max_width)
    while font_size > 32 and (len(lines) > 3 or any(draw.textlength(line, font=font) > max_width for line in lines)):
        font_size -= 6
        font = _load_font(font_size, font_path)
        lines = _wrap_text(draw, text, font, max_width)

    line_height = int(font_size * 1.25)
    pad_y = margin // 4
    text_height = line_height * len(lines)
    block_height = text_height + pad_y * 2
    if variant.text.position == "top":
        block_top = margin // 2
    elif variant.text.position == "center":
        block_top = (RENDER_HEIGHT - block_height) // 2
    else:  # bottom
        block_top = RENDER_HEIGHT - block_height - margin // 2
    block_top = max(block_top, 0)
    block_bottom = min(block_top + block_height, RENDER_HEIGHT)

    # Pre-compute each line's x position so treatments can size a backdrop
    # around the actual text footprint instead of the full render width.
    positioned_lines: list[tuple[str, float]] = []
    widest_line = 0.0
    leftmost_x = RENDER_WIDTH
    rightmost_x = 0.0
    y = block_top + pad_y
    for line in lines:
        line_width = draw.textlength(line, font=font)
        if variant.text.align == "left":
            x = margin
        elif variant.text.align == "right":
            x = RENDER_WIDTH - margin - line_width
        else:
            x = (RENDER_WIDTH - line_width) / 2
        positioned_lines.append((line, x))
        widest_line = max(widest_line, line_width)
        leftmost_x = min(leftmost_x, x)
        rightmost_x = max(rightmost_x, x + line_width)
        y += line_height

    opacity = {"clear": 105, "balanced": _BACKDROP_OPACITY, "bold": 172}.get(variant.style_intent, _BACKDROP_OPACITY)
    treatment = variant.composition.text_treatment if variant.composition else _LEGACY_TEXT_TREATMENT
    pad_x = margin // 2
    panel_box = (
        max(int(leftmost_x - pad_x), 0),
        block_top,
        min(int(rightmost_x + pad_x), RENDER_WIDTH),
        block_bottom,
    )

    if treatment == "restrained_high_contrast":
        pass  # No backdrop: rely on the outline/shadow stroke below for contrast.
    elif treatment == "large_bold_high_contrast":
        # Tight panel sized to the wrapped text block rather than the full width.
        draw.rectangle([panel_box[0:2], panel_box[2:4]], fill=(*backdrop_color, opacity))
    elif treatment == "oversized_bold_stroked":
        # Local gradient confined to the text footprint, fading out at the edges.
        gx0, gy0, gx1, gy1 = panel_box
        gw, gh = max(gx1 - gx0, 1), max(gy1 - gy0, 1)
        gradient = Image.new("RGBA", (gw, gh), (0, 0, 0, 0))
        gradient_draw = ImageDraw.Draw(gradient)
        for row in range(gh):
            fade = 1.0 - abs((row / max(gh - 1, 1)) - 0.5) * 2
            row_opacity = int(opacity * (0.35 + 0.65 * fade))
            gradient_draw.line([(0, row), (gw, row)], fill=(*backdrop_color, row_opacity))
        image.paste(gradient, (gx0, gy0), gradient)
    else:
        # Legacy fallback: full-width backdrop strip.
        draw.rectangle([(0, block_top), (RENDER_WIDTH, block_bottom)], fill=(*backdrop_color, opacity))

    y = block_top + pad_y
    outline = max(font_size // 24, 2)
    for line, x in positioned_lines:
        for dx in range(-outline, outline + 1):
            for dy in range(-outline, outline + 1):
                if dx or dy:
                    draw.text((x + dx, y + dy), line, font=font, fill=_OUTLINE_COLOR)
        draw.text((x, y), line, font=font, fill=_TEXT_COLOR)
        y += line_height


def _auto_zoom_for_variant(variant: ThumbnailVariant) -> float:
    if variant.style_intent == "bold":
        return 0.78
    if variant.style_intent == "clear":
        return 1.0
    if variant.composition and "tightly" in variant.composition.crop_instruction.lower():
        return 0.82
    return 0.9


def _apply_composition_treatment(image, variant: ThumbnailVariant):
    if not variant.composition:
        return image
    treatment = variant.composition.background_treatment
    if treatment not in {"blur_and_darken", "subtle_contrast"}:
        return image

    from PIL import ImageEnhance, ImageFilter

    if treatment == "blur_and_darken":
        treated = image.filter(ImageFilter.GaussianBlur(radius=1.2 if variant.style_intent != "bold" else 1.8))
        treated = ImageEnhance.Brightness(treated).enhance(0.82 if variant.style_intent != "clear" else 0.9)
        treated = ImageEnhance.Contrast(treated).enhance(1.08)
        return treated

    contrast = 1.06 if variant.style_intent != "bold" else 1.16
    return ImageEnhance.Contrast(image).enhance(contrast)


def _supporting_asset_for_variant(variant: ThumbnailVariant) -> dict | None:
    assets = variant.source_assets.get("uploaded_assets", []) if isinstance(variant.source_assets, dict) else []
    plan = variant.composition
    allowed_roles = [role for role in (plan.supporting_asset_roles if plan else []) if role]
    if plan is not None and not allowed_roles:
        return None
    plan_text = " ".join(
        [
            plan.primary_subject if plan else "",
            plan.secondary_subject if plan else "",
            plan.visual_anchor if plan else "",
            plan.secondary_anchor if plan else "",
            plan.layout if plan else "",
        ]
    ).lower()
    preferred_roles: list[str] = []
    if any(word in plan_text for word in ("person", "presenter", "face", "human", "host")):
        preferred_roles.append("person")
    if any(word in plan_text for word in ("product", "device", "gear", "item", "tool")):
        preferred_roles.extend(["product", "result"])
    preferred_roles.extend(["product", "result", "screenshot", "chart", "after", "before", "person", "logo"])
    if allowed_roles:
        allowed_set = set(allowed_roles)
        preferred_roles = [role for role in preferred_roles if role in allowed_set]
        preferred_roles.extend(role for role in allowed_roles if role not in preferred_roles)
    for role in preferred_roles:
        for asset in assets:
            if not isinstance(asset, dict) or asset.get("role") != role:
                continue
            path = Path(str(asset.get("path") or ""))
            if path.is_file() and path != Path(variant.frame_path or ""):
                return asset
    return None


def _supporting_asset_position(variant: ThumbnailVariant, role: str, asset_size: tuple[int, int]) -> tuple[int, int]:
    asset_w, asset_h = asset_size
    margin = int(RENDER_WIDTH * 0.055)
    layout = (variant.composition.layout if variant.composition else "").lower()

    if role == "logo":
        x = RENDER_WIDTH - asset_w - margin
        y = RENDER_HEIGHT - asset_h - margin if variant.text.position == "top" else margin
        return x, y

    if "presenter_left" in layout:
        x = margin if role == "person" else RENDER_WIDTH - asset_w - margin
    elif "product_left" in layout:
        x = margin if role in {"product", "result", "screenshot", "chart", "after", "before"} else RENDER_WIDTH - asset_w - margin
    elif "left" in layout and role == "person":
        x = margin
    elif variant.text.align == "left":
        x = RENDER_WIDTH - asset_w - margin
    else:
        x = margin

    if variant.text.position == "bottom":
        y = margin
    elif variant.text.position == "top":
        y = RENDER_HEIGHT - asset_h - margin
    else:
        y = int((RENDER_HEIGHT - asset_h) * 0.48)
    return x, y


def _paste_supporting_asset(image, variant: ThumbnailVariant):
    asset_meta = _supporting_asset_for_variant(variant)
    if asset_meta is None:
        return image
    asset_path = Path(str(asset_meta.get("path") or ""))
    role = str(asset_meta.get("role") or "other")

    from PIL import Image, ImageFilter

    try:
        with Image.open(asset_path) as asset:
            asset = asset.convert("RGBA")
            bbox = asset.getbbox()
            if bbox:
                asset = asset.crop(bbox)
            if role == "logo":
                max_w = int(RENDER_WIDTH * 0.2)
                max_h = int(RENDER_HEIGHT * 0.16)
            elif role == "person":
                max_w = int(RENDER_WIDTH * (0.38 if variant.style_intent != "bold" else 0.45))
                max_h = int(RENDER_HEIGHT * 0.78)
            else:
                max_w = int(RENDER_WIDTH * (0.42 if variant.style_intent != "bold" else 0.48))
                max_h = int(RENDER_HEIGHT * 0.68)
            ratio = min(max_w / max(asset.width, 1), max_h / max(asset.height, 1))
            target_size = (max(int(asset.width * ratio), 1), max(int(asset.height * ratio), 1))
            asset = asset.resize(target_size, Image.LANCZOS)

            x, y = _supporting_asset_position(variant, role, asset.size)

            shadow = Image.new("RGBA", image.size, (0, 0, 0, 0))
            shadow_mask = asset.split()[-1].filter(ImageFilter.GaussianBlur(12))
            shadow_layer = Image.new("RGBA", asset.size, (0, 0, 0, 150))
            shadow.paste(shadow_layer, (x + 10, y + 12), shadow_mask)

            base = image.convert("RGBA")
            base.alpha_composite(shadow)

            outline = Image.new("RGBA", image.size, (0, 0, 0, 0))
            mask = asset.split()[-1]
            expanded = mask.filter(ImageFilter.MaxFilter(7))
            stroke = Image.new("RGBA", asset.size, (255, 255, 255, 210))
            outline.paste(stroke, (x, y), expanded)
            base.alpha_composite(outline)
            base.paste(asset, (x, y), asset)

            return base.convert("RGB")
    except Exception:  # noqa: BLE001
        return image


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


def resolve_frame_path(frame_path: str | None, output_path: Path) -> Path | None:
    """`frame_path` is an absolute path baked in when the frame was extracted. If the
    project/output folder has since been moved or renamed (or a session was reopened
    from a relocated output directory), that absolute path no longer exists — fall
    back to the same filename inside the current thumbnails directory (the frame
    itself always lives alongside the file we're about to render, `output_path`'s
    parent) instead of silently rendering a blank placeholder."""
    if not frame_path:
        return None
    candidate = Path(frame_path)
    if candidate.is_file():
        return candidate
    fallback = output_path.parent / candidate.name
    return fallback if fallback.is_file() else candidate


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
    frame_path = resolve_frame_path(variant.frame_path, output_path)
    if frame_path and frame_path.is_file():
        try:
            with Image.open(frame_path) as source:
                source = source.convert("RGB")
                box = (
                    variant.crop
                    if not _is_identity_crop(variant.crop)
                    else compute_crop_box(source.size, variant.focal_x, variant.focal_y, _auto_zoom_for_variant(variant))
                )
                cropped = source.crop(_pixel_box(source.size, box))
                canvas = cropped.resize((RENDER_WIDTH, RENDER_HEIGHT), Image.LANCZOS)
        except Exception:  # noqa: BLE001
            canvas = None

    if canvas is None:
        canvas = Image.new("RGB", (RENDER_WIDTH, RENDER_HEIGHT), placeholder_color)

    canvas = _apply_composition_treatment(canvas, variant)
    canvas = _paste_supporting_asset(canvas, variant)
    _draw_overlay_text(canvas, variant, backdrop_color, font_path)
    _draw_logo(canvas, branding)
    canvas.save(output_path, "JPEG", quality=92)
    return str(output_path)
