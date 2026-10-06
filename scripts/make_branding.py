"""Draw the brand images. Usage: python scripts/make_branding.py

The icon is a teal badge with a white invoice sheet and an amber bell over its lower
right corner — "an invoice" and "a notification", the two things the integration is
about. Both still read at the 24 px the frontend renders an integration icon at.

It deliberately borrows nothing from the KSeF or Ministry of Finance marks: no
lettering, no national colours, no emblem. This is an unofficial integration and
must not look like an official one (docs/BRANDING.md).

Everything is drawn at four times the delivered size and reduced with LANCZOS, which
antialiases the edges far better than drawing at final size does.

Output goes to `custom_components/ksef_notification/brand/`, where Home Assistant
(2026.3 and newer) reads a custom integration's own brand images from.
"""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

REPO_ROOT = Path(__file__).resolve().parents[1]
TARGET = REPO_ROOT / "custom_components" / "ksef_notification" / "brand"

#: Delivered icon sizes, fixed by the brand-image specification: square, 256 and 512.
ICON_SIZE = 256
ICON_2X_SIZE = 512

#: Drawing happens this many times larger than the delivered image.
SUPERSAMPLE = 4

#: Badge gradient, top to bottom.
BADGE_TOP = (0x2B, 0xB3, 0xA3)
BADGE_BOTTOM = (0x0E, 0x5E, 0x6B)

SHEET = (0xFF, 0xFF, 0xFF, 0xFF)
#: The folded corner and the text lines on the sheet: the badge colour, lightened.
FOLD = (0xC4, 0xE6, 0xE2, 0xFF)
TEXT_LINE = (0x8C, 0xC9, 0xC1, 0xFF)
BELL_DISC = (0xFF, 0xB3, 0x2E, 0xFF)
BELL = (0x0B, 0x4A, 0x55, 0xFF)

WORDMARK_LIGHT = (0x0B, 0x4A, 0x55, 0xFF)
WORDMARK_DARK = (0xE8, 0xF5, 0xF3, 0xFF)

WORDMARK_TEXT = "KSeF Notification"

FONT_CANDIDATES = (
    Path("C:/Windows/Fonts/DejaVuSans-Bold.ttf"),
    Path("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"),
    Path("/usr/share/fonts/TTF/DejaVuSans-Bold.ttf"),
    Path("/usr/share/fonts/dejavu/DejaVuSans-Bold.ttf"),
    Path("/Library/Fonts/DejaVuSans-Bold.ttf"),
)

#: Geometry, in fractions of the badge side.
SHEET_BOX = (0.20, 0.14, 0.66, 0.84)
SHEET_RADIUS = 0.045
FOLD_SIZE = 0.13
#: Text lines on the sheet: (left, top, right) — the last one short, like a total.
TEXT_LINES = (
    (0.27, 0.36, 0.59),
    (0.27, 0.46, 0.59),
    (0.27, 0.56, 0.47),
)
TEXT_LINE_HEIGHT = 0.045
BELL_CENTRE = (0.69, 0.70)
BELL_DISC_RADIUS = 0.215
#: The ring of badge colour that separates the disc from the sheet it overlaps.
BELL_RING = 0.035


def _font(size: int) -> ImageFont.FreeTypeFont:
    """The wordmark face, or a clear error naming what to install."""
    for candidate in FONT_CANDIDATES:
        if candidate.is_file():
            return ImageFont.truetype(candidate, size)
    searched = "\n  ".join(str(path) for path in FONT_CANDIDATES)
    message = f"DejaVu Sans Bold not found. Looked in:\n  {searched}"
    raise SystemExit(message)


def _scaled(box: tuple[float, ...], size: int) -> tuple[int, ...]:
    return tuple(round(value * size) for value in box)


def _badge(size: int) -> Image.Image:
    """The rounded square, filled top to bottom with the gradient."""
    column = Image.new("RGB", (1, size))
    pixels = column.load()
    assert pixels is not None
    for y in range(size):
        ratio = y / (size - 1)
        pixels[0, y] = tuple(
            round(top + (bottom - top) * ratio)
            for top, bottom in zip(BADGE_TOP, BADGE_BOTTOM, strict=True)
        )
    badge = column.resize((size, size)).convert("RGBA")

    mask = Image.new("L", (size, size), 0)
    ImageDraw.Draw(mask).rounded_rectangle(
        (0, 0, size - 1, size - 1), radius=round(size * 0.22), fill=255
    )
    badge.putalpha(mask)
    return badge


def _sheet(draw: ImageDraw.ImageDraw, size: int) -> None:
    """The invoice: a sheet with its top right corner folded down, and three text lines."""
    left, top, right, bottom = _scaled(SHEET_BOX, size)
    fold = round(FOLD_SIZE * size)
    draw.rounded_rectangle(
        (left, top, right, bottom), radius=round(SHEET_RADIUS * size), fill=SHEET
    )
    # Cut the corner off, then lay the fold into the cut.
    draw.polygon(
        [(right - fold, top - 1), (right + 1, top - 1), (right + 1, top + fold)], fill=(0, 0, 0, 0)
    )
    draw.polygon([(right - fold, top), (right, top + fold), (right - fold, top + fold)], fill=FOLD)

    height = round(TEXT_LINE_HEIGHT * size)
    for line_left, line_top, line_right in TEXT_LINES:
        x0, y0, x1 = _scaled((line_left, line_top, line_right), size)
        draw.rounded_rectangle((x0, y0, x1, y0 + height), radius=height // 2, fill=TEXT_LINE)


def _bell(draw: ImageDraw.ImageDraw, size: int) -> None:
    """An amber disc with a bell, cut out of the sheet by a ring that lets the badge show."""
    cx, cy = BELL_CENTRE[0] * size, BELL_CENTRE[1] * size
    disc = BELL_DISC_RADIUS * size
    ring = disc + BELL_RING * size
    draw.ellipse((cx - ring, cy - ring, cx + ring, cy + ring), fill=(0, 0, 0, 0))
    draw.ellipse((cx - disc, cy - disc, cx + disc, cy + disc), fill=BELL_DISC)

    # The bell, in units of the disc radius: a dome on a flared body, a rim and a clapper.
    unit = disc
    dome_r = 0.36 * unit
    dome_cy = cy - 0.20 * unit
    rim_y = cy + 0.34 * unit
    draw.ellipse((cx - dome_r, dome_cy - dome_r, cx + dome_r, dome_cy + dome_r * 1.1), fill=BELL)
    draw.polygon(
        [
            (cx - dome_r, dome_cy),
            (cx + dome_r, dome_cy),
            (cx + 0.52 * unit, rim_y),
            (cx - 0.52 * unit, rim_y),
        ],
        fill=BELL,
    )
    rim = 0.09 * unit
    draw.rounded_rectangle(
        (cx - 0.62 * unit, rim_y - rim, cx + 0.62 * unit, rim_y + rim), radius=rim, fill=BELL
    )
    clapper = 0.14 * unit
    clapper_cy = rim_y + rim + 0.06 * unit
    draw.ellipse(
        (cx - clapper, clapper_cy - clapper, cx + clapper, clapper_cy + clapper), fill=BELL
    )
    knob = 0.09 * unit
    knob_cy = dome_cy - dome_r
    draw.ellipse((cx - knob, knob_cy - knob, cx + knob, knob_cy + knob), fill=BELL)


def _render_icon(canvas: int) -> Image.Image:
    """The icon at exactly `canvas` pixels square, with no reduction of its own."""
    icon = _badge(canvas)
    art = Image.new("RGBA", (canvas, canvas), (0, 0, 0, 0))
    draw = ImageDraw.Draw(art)
    _sheet(draw, canvas)
    _bell(draw, canvas)
    icon.alpha_composite(art)
    return icon


def draw_icon(size: int) -> Image.Image:
    return _render_icon(size * SUPERSAMPLE).resize((size, size), Image.Resampling.LANCZOS)


def draw_logo(height: int, colour: tuple[int, int, int, int]) -> Image.Image:
    """The icon with the wordmark beside it, trimmed to what it covers.

    The wordmark is the manifest name, the same in every language: a brand image cannot
    be swapped per language. "Powiadomienia KSeF" lives in `translations/pl.json`.
    """
    canvas_h = height * SUPERSAMPLE
    icon = _render_icon(canvas_h)

    font = _font(round(canvas_h * 0.40))
    gap = round(canvas_h * 0.18)
    measure = ImageDraw.Draw(Image.new("RGBA", (1, 1)))
    left, top, right, bottom = measure.textbbox((0, 0), WORDMARK_TEXT, font=font)

    logo = Image.new("RGBA", (canvas_h + gap + (right - left), canvas_h), (0, 0, 0, 0))
    logo.alpha_composite(icon, (0, 0))
    ImageDraw.Draw(logo).text(
        (canvas_h + gap - left, canvas_h // 2 - (top + bottom) // 2),
        WORDMARK_TEXT,
        font=font,
        fill=colour,
    )

    box = logo.getbbox()
    assert box is not None
    logo = logo.crop(box)
    return logo.resize(
        (round(logo.width / SUPERSAMPLE), round(logo.height / SUPERSAMPLE)),
        Image.Resampling.LANCZOS,
    )


def _save(image: Image.Image, name: str) -> None:
    path = TARGET / name
    image.save(path, format="PNG", optimize=True)
    print(f"{path.relative_to(REPO_ROOT)}  {image.width}x{image.height}  {path.stat().st_size} B")


def main() -> None:
    TARGET.mkdir(parents=True, exist_ok=True)
    _save(draw_icon(ICON_SIZE), "icon.png")
    _save(draw_icon(ICON_2X_SIZE), "icon@2x.png")
    for scale, suffix in ((ICON_SIZE, ""), (ICON_2X_SIZE, "@2x")):
        _save(draw_logo(scale, WORDMARK_LIGHT), f"logo{suffix}.png")
        _save(draw_logo(scale, WORDMARK_DARK), f"dark_logo{suffix}.png")


if __name__ == "__main__":
    main()
