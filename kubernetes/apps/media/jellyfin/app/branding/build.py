#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.12"
# dependencies = ["fonttools>=4.55", "pillow>=11", "playwright>=1.50"]
# ///
"""Build Jellyfin's branding from this directory: ./build.py [logo] [splash]

  logo.svg    the header logo: a lit 35mm film gate and HOMENET set in Antonio (fonts/, OFL),
              outlined to paths so Jellyfin needs no webfont for it. plugins.py inlines it into
              the Custom CSS as a data URI.
  splash.jpg  the splash screen (the backdrop behind Jellyfin's login and server pages):
              splash.html's WebGL shader rendered at 2560x1440 in headless Chromium.

Both outputs are committed; ../plugins.py uploads the splash and sets the CSS. Re-run this after
changing splash.html or the logo below, then commit.
"""

from __future__ import annotations

import io
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
FONT = HERE / "fonts" / "Antonio[wght].ttf"

IVORY = "#F3ECDF"
WORD = "HOMENET"
WEIGHT = 600
CAP = 17.0  # cap height in logo units (viewBox height 40)
TRACK = 0.07  # letter-spacing, em
BASELINE = 28.5
WORD_X = 50.0

MARK = """
  <defs>
    <linearGradient id="plate" x1="0" y1="0" x2="0" y2="1">
      <stop offset="0" stop-color="#232C40"/><stop offset="1" stop-color="#0E1422"/>
    </linearGradient>
    <radialGradient id="gate" cx="0.42" cy="0.40" r="0.75">
      <stop offset="0" stop-color="#FFF4DE"/><stop offset="0.45" stop-color="#FFC27A"/>
      <stop offset="1" stop-color="#D9692A"/>
    </radialGradient>
  </defs>
  <rect x="1" y="4" width="38" height="32" rx="7" fill="url(#plate)" stroke="#F3ECDF" stroke-opacity="0.22"/>
  <g fill="#05070C" stroke="#F3ECDF" stroke-opacity="0.14" stroke-width="0.6">
    <rect x="4.4" y="8.6" width="4.2" height="3.6" rx="1.1"/>
    <rect x="4.4" y="15.0" width="4.2" height="3.6" rx="1.1"/>
    <rect x="4.4" y="21.4" width="4.2" height="3.6" rx="1.1"/>
    <rect x="4.4" y="27.8" width="4.2" height="3.6" rx="1.1"/>
    <rect x="31.4" y="8.6" width="4.2" height="3.6" rx="1.1"/>
    <rect x="31.4" y="15.0" width="4.2" height="3.6" rx="1.1"/>
    <rect x="31.4" y="21.4" width="4.2" height="3.6" rx="1.1"/>
    <rect x="31.4" y="27.8" width="4.2" height="3.6" rx="1.1"/>
  </g>
  <rect x="11" y="12.5" width="18" height="15" rx="2.2" fill="url(#gate)"/>
"""


def wordmark() -> tuple[str, float]:
    """HOMENET as one SVG path, and its right edge in logo units."""
    from fontTools.pens.svgPathPen import SVGPathPen
    from fontTools.pens.transformPen import TransformPen
    from fontTools.ttLib import TTFont
    from fontTools.varLib.instancer import instantiateVariableFont

    font = instantiateVariableFont(TTFont(FONT), {"wght": WEIGHT})
    upem = font["head"].unitsPerEm
    cap = font["OS/2"].sCapHeight
    scale = CAP / cap
    cmap, glyphs, hmtx = font.getBestCmap(), font.getGlyphSet(), font["hmtx"]
    pen = SVGPathPen(glyphs, ntos=lambda v: f"{v:.2f}".rstrip("0").rstrip("."))
    x = 0.0
    for i, ch in enumerate(WORD):
        name = cmap[ord(ch)]
        # font units -> logo units, y flipped, glyph placed at the pen position
        glyphs[name].draw(TransformPen(pen, (scale, 0, 0, -scale, WORD_X + x * scale, BASELINE)))
        x += hmtx[name][0]
        if i < len(WORD) - 1:
            x += TRACK * upem
    return pen.getCommands(), WORD_X + x * scale


def build_logo() -> None:
    path, right = wordmark()
    width = round(right + 2)
    svg = (
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width} 40" width="{width}" height="40">'
        f"<title>HomeNet</title>{MARK}"
        f'  <path fill="{IVORY}" d="{path}"/>\n</svg>\n'
    )
    (HERE / "logo.svg").write_text(svg)
    print(f"wrote logo.svg ({width}x40)")


def build_splash(width: int = 2560, height: int = 1440) -> None:
    from PIL import Image
    from playwright.sync_api import sync_playwright

    with sync_playwright() as pw:
        browser = pw.chromium.launch(args=["--use-angle=swiftshader", "--enable-unsafe-swiftshader"])
        page = browser.new_page(viewport={"width": width, "height": height})
        page.goto((HERE / "splash.html").as_uri() + f"?w={width}&h={height}")
        page.wait_for_function("window.splashDone === true", timeout=300_000)
        data = page.evaluate("document.getElementById('c').toDataURL('image/png')")
        browser.close()
    import base64

    png = Image.open(io.BytesIO(base64.b64decode(data.split(",", 1)[1]))).convert("RGB")
    png.save(HERE / "splash.jpg", "JPEG", quality=86, optimize=True, progressive=True, subsampling=0)
    print(f"wrote splash.jpg ({width}x{height}, {(HERE / 'splash.jpg').stat().st_size // 1024} KiB)")


if __name__ == "__main__":
    targets = sys.argv[1:] or ["logo", "splash"]
    for target in targets:
        {"logo": build_logo, "splash": build_splash}[target]()
