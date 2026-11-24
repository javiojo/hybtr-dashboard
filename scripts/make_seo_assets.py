#!/usr/bin/env python3
"""Generate og-image.png (1200x630) and a compressed nav-sized logo.png.

Run from the repo root:

    python scripts/make_seo_assets.py
"""
from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont

ROOT = Path(__file__).resolve().parents[1]
NAV_HEIGHT = 52
LOGO_2X_HEIGHT = NAV_HEIGHT * 2  # 104px
OG_SIZE = (1200, 630)

NAVY_DEEP = (11, 20, 40, 255)      # #0b1428
NAVY_DARK = (17, 30, 61, 255)      # #111e3d
NAVY = (27, 46, 91, 255)           # #1b2e5b
LIME = (211, 224, 80, 255)         # #d3e050
TXT = (232, 237, 248, 255)         # #e8edf8
TXT2 = (143, 175, 212, 255)        # #8fafd4

INTER = Path(
    "/usr/share/fonts/truetype/sand-box/google/Inter/"
    "Inter-VariableFont_opsz,wght.ttf"
)
FALLBACK = Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf")


def _font(size: int, weight: int = 500) -> ImageFont.FreeTypeFont:
    path = INTER if INTER.exists() else FALLBACK
    font = ImageFont.truetype(str(path), size=size)
    try:
        font.set_variation_by_axes({"wght": weight})
    except Exception:
        try:
            font.set_variation_by_name("Regular" if weight < 550 else "SemiBold")
        except Exception:
            pass
    return font


def optimize_logo() -> Path:
    src = Image.open(ROOT / "logo.png").convert("RGBA")
    w = max(1, round(src.width * LOGO_2X_HEIGHT / src.height))
    out = src.resize((w, LOGO_2X_HEIGHT), Image.Resampling.LANCZOS)
    dest = ROOT / "logo.png"
    out.save(dest, "PNG", optimize=True)
    print(f"logo.png -> {out.size[0]}x{out.size[1]} ({dest.stat().st_size} bytes)")
    return dest


def make_og_image() -> Path:
    W, H = OG_SIZE
    img = Image.new("RGBA", OG_SIZE, NAVY_DEEP)
    draw = ImageDraw.Draw(img)

    # Soft lime glow behind the logo
    glow = Image.new("RGBA", OG_SIZE, (0, 0, 0, 0))
    gdraw = ImageDraw.Draw(glow)
    gdraw.ellipse((W // 2 - 380, 40, W // 2 + 380, 420), fill=(211, 224, 80, 28))
    img = Image.alpha_composite(img, glow.filter(ImageFilter.GaussianBlur(80)))
    draw = ImageDraw.Draw(img)

    # Top lime accent bar
    draw.rectangle((0, 0, W, 8), fill=LIME)
    # Bottom bar
    draw.rectangle((0, H - 8, W, H), fill=LIME)

    logo = Image.open(ROOT / "logo.png").convert("RGBA")
    # Display logo larger on the OG canvas (~160px tall)
    logo_h = 160
    logo_w = round(logo.width * logo_h / logo.height)
    logo = logo.resize((logo_w, logo_h), Image.Resampling.LANCZOS)
    logo_x = (W - logo_w) // 2
    logo_y = 110
    img.paste(logo, (logo_x, logo_y), logo)
    draw = ImageDraw.Draw(img)

    tagline = "Ranked by 1-year total return, not yield alone"
    font_tag = _font(28, 500)
    bbox = draw.textbbox((0, 0), tagline, font=font_tag)
    tw = bbox[2] - bbox[0]
    draw.text(((W - tw) // 2, logo_y + logo_h + 36), tagline, font=font_tag, fill=TXT)

    # Disambiguation pill
    pill = "Not the HYTR ETF"
    font_pill = _font(20, 600)
    pb = draw.textbbox((0, 0), pill, font=font_pill)
    pw, ph = pb[2] - pb[0], pb[3] - pb[1]
    pad_x, pad_y = 16, 8
    px = (W - pw) // 2
    py = logo_y + logo_h + 88
    draw.rounded_rectangle(
        (px - pad_x, py - pad_y, px + pw + pad_x, py + ph + pad_y),
        radius=8,
        outline=LIME,
        width=2,
        fill=(211, 224, 80, 20),
    )
    draw.text((px, py), pill, font=font_pill, fill=LIME)

    font_url = _font(22, 500)
    url = "hybtretfs.com"
    ub = draw.textbbox((0, 0), url, font=font_url)
    uw = ub[2] - ub[0]
    draw.text(((W - uw) // 2, H - 70), url, font=font_url, fill=TXT2)

    dest = ROOT / "og-image.png"
    rgb = Image.new("RGB", OG_SIZE, NAVY_DEEP[:3])
    rgb.paste(img, mask=img.split()[-1])
    rgb.save(dest, "PNG", optimize=True)
    print(f"og-image.png -> {OG_SIZE[0]}x{OG_SIZE[1]} ({dest.stat().st_size} bytes)")
    return dest


if __name__ == "__main__":
    optimize_logo()
    make_og_image()
