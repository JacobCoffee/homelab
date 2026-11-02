#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = ["pillow>=10", "numpy>=1.26", "fonttools>=4.50"]
# ///
"""Paint the procedural art for the holiday and date-night themes.

Writes wallpaper.webp, logo.png, display.ttf, body.ttf and OFL.txt into
themes/<id>/ for christmas, new-year and date-night. Deterministic: the same
seed gives the same pixels, so the art can be regenerated from source.

    uv run tools/paint-themes.py            # all themes
    uv run tools/paint-themes.py christmas  # one theme

Fonts come from github.com/google/fonts (all OFL); variable fonts are pinned to
one weight and subset to Latin so they load predictably on Android and stay small.
"""

from __future__ import annotations

import io
import math
import sys
import urllib.request
from pathlib import Path

import numpy as np
from fontTools import subset
from fontTools.ttLib import TTFont
from fontTools.varLib import instancer
from PIL import Image, ImageDraw, ImageFilter

ROOT = Path(__file__).resolve().parent.parent
THEMES = ROOT / "themes"
W, H = 1920, 1080
SS = 2  # supersampling for drawn silhouettes
LOGO = 360
FONT_BASE = "https://raw.githubusercontent.com/google/fonts/main/ofl"
LATIN = "U+0020-007E,U+00A0-00FF,U+2013-2014,U+2018-201E,U+2022,U+2026,U+2032-2033"


# --------------------------------------------------------------------------- canvas


def hexrgb(h: str) -> np.ndarray:
    h = h.lstrip("#")
    return np.array([int(h[i : i + 2], 16) / 255 for i in (0, 2, 4)], dtype=np.float32)


def vertical(stops: list[tuple[float, str]]) -> np.ndarray:
    """A W x H x 3 vertical gradient through (position 0..1, color) stops."""
    ys = np.linspace(0, 1, H, dtype=np.float32)
    pos = [p for p, _ in stops]
    cols = np.stack([hexrgb(c) for _, c in stops])
    rows = np.stack([np.interp(ys, pos, cols[:, k]) for k in range(3)], axis=1)
    return np.repeat(rows[:, None, :], W, axis=1)


GRID_Y, GRID_X = np.mgrid[0:H, 0:W].astype(np.float32)


def glow(c: np.ndarray, x: float, y: float, r: float, color: str, a: float, sx: float = 1.0) -> None:
    """Additive gaussian light; sx stretches it horizontally."""
    d2 = ((GRID_X - x) / (r * sx)) ** 2 + ((GRID_Y - y) / r) ** 2
    c += hexrgb(color) * (a * np.exp(-d2))[..., None]


def over(c: np.ndarray, mask: np.ndarray, color: str | np.ndarray, a: float = 1.0) -> None:
    col = hexrgb(color) if isinstance(color, str) else color
    m = (mask * a)[..., None]
    c[:] = c * (1 - m) + col * m


def add(c: np.ndarray, mask: np.ndarray, color: str, a: float = 1.0) -> None:
    c += hexrgb(color) * (mask * a)[..., None]


class Mask:
    """A supersampled drawing surface that resolves to an anti-aliased 0..1 mask."""

    def __init__(self) -> None:
        self.im = Image.new("L", (W * SS, H * SS), 0)
        self.d = ImageDraw.Draw(self.im)

    def poly(self, pts, fill: int = 255) -> None:
        self.d.polygon([(x * SS, y * SS) for x, y in pts], fill=fill)

    def rect(self, x0, y0, x1, y1, fill: int = 255) -> None:
        self.d.rectangle([x0 * SS, y0 * SS, x1 * SS, y1 * SS], fill=fill)

    def ellipse(self, x0, y0, x1, y1, fill: int = 255) -> None:
        self.d.ellipse([x0 * SS, y0 * SS, x1 * SS, y1 * SS], fill=fill)

    def line(self, pts, width: float, fill: int = 255) -> None:
        self.d.line([(x * SS, y * SS) for x, y in pts], fill=fill, width=max(1, round(width * SS)))

    def get(self, blur: float = 0.0) -> np.ndarray:
        im = self.im.resize((W, H), Image.LANCZOS)
        if blur:
            im = im.filter(ImageFilter.GaussianBlur(blur))
        return np.asarray(im, dtype=np.float32) / 255


def bokeh(c: np.ndarray, x: float, y: float, r: float, color: str, a: float, rim: float = 0.35) -> None:
    """An out-of-focus light: a soft disc with a slightly brighter rim, added as light."""
    x0, x1 = int(max(0, x - r - 3)), int(min(W, x + r + 4))
    y0, y1 = int(max(0, y - r - 3)), int(min(H, y + r + 4))
    if x0 >= x1 or y0 >= y1:
        return
    d = np.hypot(GRID_X[y0:y1, x0:x1] - x, GRID_Y[y0:y1, x0:x1] - y) / r
    disc = np.clip((1 - d) * r / 2.2, 0, 1)
    ring = np.exp(-((d - 0.9) / 0.09) ** 2) * (d < 1.05)
    m = disc * (1 - rim) + np.clip(disc + ring, 0, 1) * rim
    c[y0:y1, x0:x1] += hexrgb(color) * (m * a)[..., None]


def stars(c: np.ndarray, rng, n: int, y_max: float, color: str = "#e8eef8", a: tuple = (0.15, 0.6)) -> None:
    m = Mask()
    for _ in range(n):
        x, y = rng.uniform(0, W), rng.uniform(0, H * y_max) ** 1.0
        r = rng.choice([0.6, 0.8, 1.0, 1.4], p=[0.5, 0.3, 0.15, 0.05])
        m.ellipse(x - r, y - r, x + r, y + r, fill=int(255 * rng.uniform(*a)))
    add(c, m.get(0.4), color)


def smooth_noise(rng, cells: int, n: int = W) -> np.ndarray:
    """1-D value noise across n samples with `cells` control points, range -1..1."""
    ctrl = rng.uniform(-1, 1, cells + 3)
    xs = np.linspace(0, cells, n)
    i = np.floor(xs).astype(int)
    t = xs - i
    t = t * t * (3 - 2 * t)
    return ctrl[i] * (1 - t) + ctrl[i + 1] * t


def finish(c: np.ndarray, rng, path: Path, grain: float = 1.6) -> None:
    """Dither with fine grain so dark gradients don't band, then write WebP."""
    out = np.clip(c * 255 + rng.normal(0, grain, c.shape), 0, 255).astype(np.uint8)
    Image.fromarray(out, "RGB").save(path, "WEBP", quality=88, method=6)


# --------------------------------------------------------------------------- christmas


def pine(m: Mask, rng, x: float, base: float, h: float, spread: float = 0.36) -> None:
    """A layered fir: jagged tiers widening toward the ground, plus a short trunk."""
    tiers = int(rng.integers(6, 10))
    top = base - h
    m.rect(x - h * 0.025, base - h * 0.12, x + h * 0.025, base + 2)
    for i in range(tiers):
        f = (i + 1) / tiers
        ty = top + (i / tiers) * h * 0.82
        by = top + f * h * 0.9
        half = h * spread * (0.25 + 0.75 * f) * rng.uniform(0.9, 1.1)
        droop = h * 0.025
        m.poly([(x, ty), (x + half, by + droop), (x + half * 0.55, by - droop), (x, by),
                (x - half * 0.55, by - droop), (x - half, by + droop)])


def snowcaps(mask: np.ndarray, depth: int = 3) -> np.ndarray:
    """Top edges of a silhouette: where snow settles."""
    below = np.zeros_like(mask)
    below[depth:] = mask[:-depth]
    return np.clip(mask - below, 0, 1)


def ridge(rng, base: float, wobble: float, h: tuple, step: tuple, x_range=(-60, W + 60)) -> Mask:
    m = Mask()
    noise = smooth_noise(rng, 7)
    x = x_range[0]
    pts = [(0, H)]
    for xi in range(0, W + 1, 8):
        pts.append((xi, base + noise[min(xi, W - 1)] * wobble))
    pts.append((W, H))
    m.poly(pts)
    while x < x_range[1]:
        ground = base + noise[int(np.clip(x, 0, W - 1))] * wobble
        pine(m, rng, x, ground + 4, rng.uniform(*h))
        x += rng.uniform(*step)
    return m


def cabin(m: Mask, lights: Mask, x: float, base: float, w: float) -> list[tuple[float, float]]:
    h = w * 0.55
    m.rect(x, base - h, x + w, base)
    m.poly([(x - w * 0.12, base - h), (x + w / 2, base - h - w * 0.5), (x + w * 1.12, base - h)])
    m.rect(x + w * 0.7, base - h - w * 0.5, x + w * 0.82, base - h - w * 0.18)
    wins = []
    for wx in (x + w * 0.16, x + w * 0.62):
        wy = base - h * 0.72
        ww, wh = w * 0.22, h * 0.36
        lights.rect(wx, wy, wx + ww, wy + wh)
        wins.append((wx + ww / 2, wy + wh / 2))
    return wins


def christmas(out: Path) -> None:
    rng = np.random.default_rng(1225)
    c = vertical([(0, "#03080d"), (0.42, "#0a1a24"), (0.66, "#1a3240"), (1, "#0a1519")])
    glow(c, 1390, 250, 420, "#9fc4d0", 0.10)
    stars(c, rng, 320, 0.6)
    moon = Mask()
    moon.ellipse(1390 - 44, 250 - 44, 1390 + 44, 250 + 44)
    over(c, moon.get(0.8), "#f2eedd", 0.92)
    glow(c, 1390, 250, 90, "#f2eedd", 0.18)
    # Aurora-like cold haze over the far hills.
    glow(c, 960, 640, 260, "#2b5a66", 0.18, sx=3.4)

    far = ridge(rng, 650, 26, (38, 90), (14, 30)).get(1.4)
    over(c, far, "#1b3441")
    add(c, snowcaps(far, 2), "#7c9cae", 0.35)
    glow(c, 960, 700, 160, "#41707e", 0.10, sx=5)

    mid = ridge(rng, 735, 34, (80, 170), (26, 56)).get(0.8)
    over(c, mid, "#0f2129")
    add(c, snowcaps(mid, 3), "#9db8c6", 0.45)

    # Snowfield with a lit cabin on the right.
    field = Mask()
    noise = smooth_noise(rng, 5)
    field.poly([(0, H)] + [(x, 800 + noise[min(x, W - 1)] * 18) for x in range(0, W + 1, 8)] + [(W, H)])
    fm = field.get(0.6)
    snow = vertical([(0, "#1f3a48"), (0.75, "#2a4a58"), (1, "#0e1c24")])
    c[:] = c * (1 - fm[..., None]) + snow * fm[..., None]

    house, lights = Mask(), Mask()
    wins = cabin(house, lights, 1480, 818, 150)
    hm = house.get(0.5)
    over(c, hm, "#0a1418")
    add(c, snowcaps(hm, 4), "#c8dbe4", 0.6)
    over(c, lights.get(0.4), "#ffcf7a")
    for wx, wy in wins:
        glow(c, wx, wy, 34, "#ffb655", 0.35)
    glow(c, 1555, 860, 140, "#ff9f45", 0.16, sx=1.8)
    # Warm windows of a far cottage on the left.
    for wx in (420, 438):
        glow(c, wx, 770, 10, "#ffc46a", 0.55)
    glow(c, 430, 780, 70, "#ff9f45", 0.10, sx=1.6)

    # Dark framing firs on both edges.
    edge = Mask()
    for x, hh in ((40, 760), (170, 560), (-30, 900), (1830, 820), (1700, 600), (1960, 940)):
        pine(edge, rng, x, H + 20, hh, spread=0.30)
    em = edge.get(0.6)
    over(c, em, "#03080a")
    add(c, snowcaps(em, 4), "#a7c0cc", 0.38)

    # Out-of-focus flakes drifting past the lens.
    for _ in range(26):
        x, y = rng.uniform(0, W), rng.uniform(0, H)
        bokeh(c, x, y, rng.uniform(6, 26), "#dfeaf2", rng.uniform(0.05, 0.16), rim=0.15)
    finish(c, rng, out / "wallpaper.webp")


def star_logo(path: Path) -> None:
    """A faceted eight-point star, light and shadow halves on each point."""
    s = LOGO * 4
    im = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    cx = cy = s / 2
    outer, inner = s * 0.47, s * 0.17
    pts = []
    for k in range(16):
        ang = -math.pi / 2 + k * math.pi / 8
        r = outer if k % 2 == 0 else (inner if k % 4 == 1 or k % 4 == 3 else inner)
        if k % 4 == 2:
            r = outer * 0.62  # the diagonal points are shorter
        pts.append((cx + r * math.cos(ang), cy + r * math.sin(ang)))
    light, dark = (246, 216, 140, 255), (176, 128, 58, 255)
    for k in range(0, 16, 2):
        tip, left, right = pts[k], pts[k - 1], pts[(k + 1) % 16]
        d.polygon([(cx, cy), left, tip], fill=light)
        d.polygon([(cx, cy), tip, right], fill=dark)
    d.ellipse([cx - s * 0.035, cy - s * 0.035, cx + s * 0.035, cy + s * 0.035], fill=(255, 240, 200, 255))
    save_logo(im, path)


# --------------------------------------------------------------------------- new year


def burst(c: np.ndarray, rng, x: float, y: float, r: float, color: str, a: float) -> None:
    m = Mask()
    rays = int(rng.integers(56, 90))
    for k in range(rays):
        ang = k / rays * 2 * math.pi + rng.uniform(-0.03, 0.03)
        rr = r * rng.uniform(0.75, 1.05)
        segs = 7
        for s in range(segs):
            r0, r1 = rr * (0.25 + 0.75 * s / segs), rr * (0.25 + 0.75 * (s + 1) / segs)
            fill = int(255 * (0.25 + 0.75 * s / segs))
            m.line([(x + r0 * math.cos(ang), y + r0 * math.sin(ang) + s * s * 0.25),
                    (x + r1 * math.cos(ang), y + r1 * math.sin(ang) + (s + 1) ** 2 * 0.25)], 1.6, fill)
        ex, ey = x + rr * math.cos(ang), y + rr * math.sin(ang) + segs * segs * 0.25
        m.ellipse(ex - 2.2, ey - 2.2, ex + 2.2, ey + 2.2)
    add(c, m.get(1.0), color, a)
    glow(c, x, y, r * 0.55, color, a * 0.10)


def skyline(rng, base: float, h: tuple, w: tuple, lights: Mask, lit: float, spires: float) -> Mask:
    m = Mask()
    x = -20.0
    while x < W + 20:
        bw, bh = rng.uniform(*w), rng.uniform(*h)
        top = base - bh
        m.rect(x, top, x + bw, base + 2)
        if rng.random() < spires:
            m.poly([(x + bw * 0.3, top), (x + bw / 2, top - bh * 0.35), (x + bw * 0.7, top)])
            m.rect(x + bw / 2 - 0.8, top - bh * 0.5, x + bw / 2 + 0.8, top)
        elif rng.random() < 0.3:
            m.rect(x + bw * 0.2, top - bh * 0.08, x + bw * 0.8, top)
        cols = int(bw // 9)
        for col in range(cols):
            for wy in np.arange(top + 8, base - 6, 11):
                if rng.random() < lit:
                    wx = x + 5 + col * 9
                    lights.rect(wx, wy, wx + 3.5, wy + 5, fill=int(255 * rng.uniform(0.35, 1)))
        x += bw + rng.uniform(-4, 6)
    return m


def new_year(out: Path) -> None:
    rng = np.random.default_rng(2027)
    c = vertical([(0, "#020208"), (0.45, "#0a0a1d"), (0.74, "#22163a"), (0.86, "#2c1830"), (1, "#07060c")])
    glow(c, 960, 860, 260, "#ff9a5c", 0.16, sx=3.6)
    stars(c, rng, 140, 0.45, a=(0.1, 0.4))
    for x, y, r, col, a in ((470, 300, 230, "#f6d48a", 0.55), (1380, 230, 270, "#ff8fa6", 0.45),
                            (1020, 430, 120, "#9fe3e0", 0.40), (1700, 470, 140, "#f6d48a", 0.35),
                            (240, 520, 100, "#ff8fa6", 0.30)):
        burst(c, rng, x, y, r, col, a)
    farlights, nearlights = Mask(), Mask()
    far = skyline(rng, 880, (60, 230), (28, 80), farlights, 0.10, 0.10).get(0.9)
    over(c, far, "#130f25")
    add(c, farlights.get(0.6) * far, "#ffd89a", 0.40)
    near = skyline(rng, H, (110, 330), (50, 130), nearlights, 0.09, 0.18).get(0.5)
    over(c, near, "#050409")
    add(c, nearlights.get(0.5) * near, "#ffcf8a", 0.5)
    # Champagne bubbles of light in the lower corners.
    for _ in range(16):
        side = rng.choice([0, 1])
        x = rng.uniform(-40, 420) if side == 0 else rng.uniform(1500, W + 40)
        y = rng.uniform(820, H + 40)
        bokeh(c, x, y, rng.uniform(14, 52), rng.choice(["#f6d48a", "#ffe6b0", "#ff9fb2"]), rng.uniform(0.05, 0.14))
    finish(c, rng, out / "wallpaper.webp")


def medallion_logo(path: Path) -> None:
    """An art-deco medallion: a ring, a fan of rays and a center diamond."""
    s = LOGO * 4
    im = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    cx = cy = s / 2
    gold, deep = (242, 207, 122, 255), (184, 142, 70, 255)
    R = s * 0.46
    d.ellipse([cx - R, cy - R, cx + R, cy + R], outline=gold, width=int(s * 0.022))
    d.ellipse([cx - R * 0.88, cy - R * 0.88, cx + R * 0.88, cy + R * 0.88], outline=deep, width=int(s * 0.008))
    for k in range(24):
        ang = k / 24 * 2 * math.pi
        r0, r1 = R * 0.30, R * (0.80 if k % 2 == 0 else 0.62)
        half = 0.045 if k % 2 == 0 else 0.03
        d.polygon([(cx + r0 * math.cos(ang - half), cy + r0 * math.sin(ang - half)),
                   (cx + r1 * math.cos(ang), cy + r1 * math.sin(ang)),
                   (cx + r0 * math.cos(ang + half), cy + r0 * math.sin(ang + half))],
                  fill=gold if k % 2 == 0 else deep)
    q = R * 0.2
    d.polygon([(cx, cy - q), (cx + q * 0.7, cy), (cx, cy + q), (cx - q * 0.7, cy)], fill=gold)
    save_logo(im, path)


# --------------------------------------------------------------------------- date night


def date_night(out: Path) -> None:
    rng = np.random.default_rng(1402)
    c = vertical([(0, "#07030a"), (0.4, "#1a0914"), (0.75, "#240c18"), (1, "#0c0408")])
    glow(c, 640, 560, 520, "#5e1a30", 0.45, sx=1.6)
    glow(c, 1500, 820, 420, "#7a2c22", 0.30, sx=1.3)
    # Two strands of out-of-focus string lights draped across the room.
    for y0, sag, n, r in ((150, 150, 26, (20, 44)), (300, 120, 22, (26, 58))):
        for k in range(n):
            t = (k + rng.uniform(-0.3, 0.3)) / (n - 1)
            x = -60 + t * (W + 120)
            y = y0 + sag * 4 * t * (1 - t) + rng.uniform(-10, 10)
            col = rng.choice(["#ffb35c", "#ffcf8a", "#ffa070", "#ff8a7a"], p=[0.4, 0.3, 0.2, 0.1])
            bokeh(c, x, y, rng.uniform(*r), col, rng.uniform(0.16, 0.36))
    # A scatter of softer, larger lights deeper in the room.
    for _ in range(40):
        bokeh(c, rng.uniform(0, W), rng.uniform(360, 760), rng.uniform(30, 90),
              rng.choice(["#ff9a6a", "#d9607a", "#ffc48a"]), rng.uniform(0.04, 0.12), rim=0.2)
    # Two candles low on the right, their light pooling on the table.
    table = Mask()
    table.rect(0, 960, W, H)
    over(c, table.get(6), "#0d0508", 0.85)
    glow(c, 1600, 950, 260, "#ff8f45", 0.20, sx=2.4)
    for x, h, w in ((1560, 150, 46), (1650, 105, 42)):
        top = 975 - h
        body = Mask()
        body.rect(x - w / 2, top, x + w / 2, 975)
        bm = body.get(0.6)
        shade = np.clip((GRID_X - (x - w / 2)) / w, 0, 1)
        col = hexrgb("#f1dcc0") * (1 - shade[..., None] * 0.55)
        c[:] = c * (1 - bm[..., None]) + col * bm[..., None] * 0.9
        flame = Mask()
        fy = top - 6
        flame.poly([(x, fy - 34), (x + 8, fy - 12), (x + 6, fy - 2), (x, fy + 2), (x - 6, fy - 2), (x - 8, fy - 12)])
        add(c, flame.get(1.4), "#fff1c8", 1.0)
        glow(c, x, fy - 14, 22, "#ffd27a", 0.55)
        glow(c, x, fy - 14, 120, "#ff9a45", 0.22)
    finish(c, rng, out / "wallpaper.webp")


def moon_logo(path: Path) -> None:
    """A rose-gold crescent cradling a four-point star."""
    s = LOGO * 4
    yy, xx = np.mgrid[0:s, 0:s].astype(np.float32)
    cx, cy, R = s * 0.46, s * 0.52, s * 0.40
    outer = np.hypot(xx - cx, yy - cy) < R
    inner = np.hypot(xx - (cx + R * 0.42), yy - (cy - R * 0.22)) < R * 0.86
    crescent = outer & ~inner
    t = np.clip((yy - (cy - R)) / (2 * R), 0, 1)[..., None]
    col = hexrgb("#f6cfae") * (1 - t) + hexrgb("#c47c6a") * t
    rgba = np.zeros((s, s, 4), dtype=np.float32)
    rgba[..., :3] = col
    rgba[..., 3] = crescent
    im = Image.fromarray((rgba * 255).astype(np.uint8), "RGBA")
    d = ImageDraw.Draw(im)
    sx, sy, a, b = s * 0.70, s * 0.36, s * 0.13, s * 0.035
    d.polygon([(sx, sy - a), (sx + b, sy - b), (sx + a, sy), (sx + b, sy + b),
               (sx, sy + a), (sx - b, sy + b), (sx - a, sy), (sx - b, sy - b)], fill=(255, 226, 196, 255))
    save_logo(im, path)


def save_logo(im: Image.Image, path: Path) -> None:
    im.resize((LOGO, LOGO), Image.LANCZOS).save(path, optimize=True)


# --------------------------------------------------------------------------- fonts


def fetch(url: str) -> bytes:
    with urllib.request.urlopen(url, timeout=60) as r:
        return r.read()


def font(family_dir: str, file: str, weight: int, name: str, out: Path) -> None:
    """Download a Google Fonts variable TTF, pin it to one weight, subset to Latin, rename.

    Pinning and subsetting make an OFL "Modified Version", which may not carry a Reserved
    Font Name (Josefin Sans has one), so every generated font gets its own family name.
    Copyright and license strings (name IDs 0, 13, 14) are kept.
    """
    tt = TTFont(io.BytesIO(fetch(f"{FONT_BASE}/{family_dir}/{file}")))
    if "fvar" in tt:
        tt = instancer.instantiateVariableFont(tt, {"wght": weight})
    opts = subset.Options()
    opts.layout_features = ["kern", "liga", "lnum", "pnum", "tnum"]
    opts.name_IDs = ["*"]
    sub = subset.Subsetter(opts)
    sub.populate(unicodes=subset.parse_unicodes(LATIN))
    sub.subset(tt)
    names = tt["name"]
    for nid in (16, 17, 21, 22, 25):
        names.removeNames(nameID=nid)
    ps = name.replace(" ", "")
    for nid, value in ((1, name), (2, "Regular"), (3, f"{ps}-Regular"), (4, name), (6, f"{ps}-Regular")):
        names.setName(value, nid, 3, 1, 0x409)
        names.setName(value, nid, 1, 0, 0)
    tt["OS/2"].usWeightClass = weight
    tt.save(out)


THEME_ART = {
    "christmas": dict(
        paint=christmas, logo=star_logo,
        display=("cinzel", "Cinzel[wght].ttf", 500),
        body=("ebgaramond", "EBGaramond[wght].ttf", 450),
    ),
    "new-year": dict(
        paint=new_year, logo=medallion_logo,
        display=("josefinsans", "JosefinSans[wght].ttf", 300),
        body=("josefinsans", "JosefinSans[wght].ttf", 400),
    ),
    "date-night": dict(
        paint=date_night, logo=moon_logo,
        display=("cormorantgaramond", "CormorantGaramond[wght].ttf", 500),
        body=("ebgaramond", "EBGaramond[wght].ttf", 450),
    ),
}


def build(theme_id: str) -> None:
    spec = THEME_ART[theme_id]
    out = THEMES / theme_id
    out.mkdir(parents=True, exist_ok=True)
    spec["paint"](out)
    spec["logo"](out / "logo.png")
    licenses = []
    for role in ("display", "body"):
        family, file, weight = spec[role]
        name = f"Hearth {theme_id.replace('-', ' ').title()} {role.title()}"
        font(family, file.replace("[", "%5B").replace("]", "%5D"), weight, name, out / f"{role}.ttf")
        lic = fetch(f"{FONT_BASE}/{family}/OFL.txt").decode()
        if lic not in licenses:
            licenses.append(lic)
    (out / "OFL.txt").write_text("\n\n".join(licenses))
    print(f"painted {theme_id}")


if __name__ == "__main__":
    for tid in sys.argv[1:] or list(THEME_ART):
        build(tid)
