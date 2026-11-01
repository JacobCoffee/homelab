#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = ["pillow"]
# ///
"""Validate every theme under themes/ against the Hearth theme contract and
build dist/catalog/index.json + <id>-<version>.zip + <id>-<version>.jpg.

Usage: tools/build-catalog.py [--themes-dir DIR] [--dist-dir DIR]
Exits non-zero if any theme fails validation.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import zipfile
from pathlib import Path

from PIL import Image, ImageDraw

ID_RE = re.compile(r"^[a-z0-9-]+$")
HEX_RE = re.compile(r"^#(?:[0-9A-Fa-f]{6}|[0-9A-Fa-f]{8})$")
MMDD_RE = re.compile(r"^\d{2}-\d{2}$")
EFFECT_TYPES = {
    "none", "snow", "embers", "leaves", "fireflies", "confetti",
    "hearts", "stars", "bats", "fireworks",
}
REQUIRED_COLORS = {
    "background", "surface", "surfaceFocused", "onSurface",
    "onSurfaceMuted", "accent", "focusRing",
}
MAX_WALLPAPER_BYTES = 700 * 1024
MAX_LOGO_HEIGHT = 512


class ThemeError(Exception):
    pass


def fail(theme_id: str, msg: str) -> None:
    raise ThemeError(f"[{theme_id}] {msg}")


def check_hex(theme_id: str, field: str, value) -> None:
    if not isinstance(value, str) or not HEX_RE.match(value):
        fail(theme_id, f"colors.{field} must be #RRGGBB or #AARRGGBB, got {value!r}")


def validate_theme(folder: Path) -> dict:
    theme_id = folder.name
    theme_json = folder / "theme.json"
    if not theme_json.is_file():
        fail(theme_id, "missing theme.json")
    try:
        data = json.loads(theme_json.read_text())
    except json.JSONDecodeError as e:
        fail(theme_id, f"invalid JSON: {e}")

    if not ID_RE.match(theme_id):
        fail(theme_id, f"folder name is not a valid id (^[a-z0-9-]+$)")
    if data.get("schema") != 1:
        fail(theme_id, f"schema must be 1, got {data.get('schema')!r}")
    if data.get("id") != theme_id:
        fail(theme_id, f"theme.json id {data.get('id')!r} must equal folder name {theme_id!r}")
    if not isinstance(data.get("name"), str) or not data["name"]:
        fail(theme_id, "name must be a non-empty string")
    if not isinstance(data.get("version"), int) or data["version"] < 1:
        fail(theme_id, "version must be a positive integer")
    if not isinstance(data.get("author"), str) or not data["author"]:
        fail(theme_id, "author must be a non-empty string")

    colors = data.get("colors")
    if not isinstance(colors, dict):
        fail(theme_id, "colors must be an object")
    missing = REQUIRED_COLORS - colors.keys()
    if missing:
        fail(theme_id, f"colors missing required keys: {sorted(missing)}")
    for key in REQUIRED_COLORS:
        check_hex(theme_id, key, colors[key])
    if "backgroundGradient" in colors:
        grad = colors["backgroundGradient"]
        if not isinstance(grad, list) or not (2 <= len(grad) <= 3):
            fail(theme_id, "colors.backgroundGradient must be a list of 2-3 stops")
        for i, stop in enumerate(grad):
            check_hex(theme_id, f"backgroundGradient[{i}]", stop)

    wallpaper_path = None
    if "wallpaper" in data:
        wallpaper_rel = data["wallpaper"]
        wallpaper_path = folder / wallpaper_rel
        if not wallpaper_path.is_file():
            fail(theme_id, f"wallpaper file not found: {wallpaper_rel}")
        size = wallpaper_path.stat().st_size
        if size > MAX_WALLPAPER_BYTES:
            fail(theme_id, f"wallpaper {wallpaper_rel} is {size} bytes, exceeds {MAX_WALLPAPER_BYTES}")
        with Image.open(wallpaper_path) as im:
            if im.size != (1920, 1080):
                fail(theme_id, f"wallpaper {wallpaper_rel} must be 1920x1080, got {im.size}")
        if wallpaper_path.suffix.lower() not in (".webp", ".jpg", ".jpeg"):
            fail(theme_id, f"wallpaper {wallpaper_rel} must be webp or jpg")

    if "wallpaperDim" in data:
        dim = data["wallpaperDim"]
        if not isinstance(dim, (int, float)) or not (0 <= dim <= 1):
            fail(theme_id, "wallpaperDim must be a number in 0..1")

    if "logo" in data:
        logo_rel = data["logo"]
        logo_path = folder / logo_rel
        if not logo_path.is_file():
            fail(theme_id, f"logo file not found: {logo_rel}")
        if logo_path.suffix.lower() != ".png":
            fail(theme_id, f"logo {logo_rel} must be a transparent PNG")
        with Image.open(logo_path) as im:
            if im.size[1] > MAX_LOGO_HEIGHT:
                fail(theme_id, f"logo {logo_rel} height {im.size[1]} exceeds {MAX_LOGO_HEIGHT}px")
            if im.mode != "RGBA":
                fail(theme_id, f"logo {logo_rel} must have an alpha channel (transparent PNG)")

    if "fonts" in data:
        fonts = data["fonts"]
        if not isinstance(fonts, dict):
            fail(theme_id, "fonts must be an object")
        for role in ("display", "body"):
            if role not in fonts:
                continue
            font_rel = fonts[role]
            font_path = folder / font_rel
            if not font_path.is_file():
                fail(theme_id, f"fonts.{role} file not found: {font_rel}")
            if font_path.suffix.lower() not in (".ttf", ".otf"):
                fail(theme_id, f"fonts.{role} must be TTF/OTF, got {font_rel}")

    if "displayCaps" in data and not isinstance(data["displayCaps"], bool):
        fail(theme_id, "displayCaps must be a boolean")

    if "wallpaperMotion" in data and data["wallpaperMotion"] not in ("none", "drift"):
        fail(theme_id, f"wallpaperMotion must be 'none' or 'drift', got {data['wallpaperMotion']!r}")
    if "focusLight" in data:
        check_hex(theme_id, "focusLight", data["focusLight"])

    effect = data.get("effect")
    if not isinstance(effect, dict):
        fail(theme_id, "effect is required and must be an object")
    if effect.get("type") not in EFFECT_TYPES:
        fail(theme_id, f"effect.type must be one of {sorted(EFFECT_TYPES)}, got {effect.get('type')!r}")
    if not isinstance(effect.get("density"), (int, float)):
        fail(theme_id, "effect.density must be a number")
    if "color" in effect:
        check_hex(theme_id, "effect.color", effect["color"])

    tile = data.get("tile")
    if not isinstance(tile, dict) or not isinstance(tile.get("cornerRadius"), (int, float)):
        fail(theme_id, "tile.cornerRadius is required and must be a number")
    if tile.get("frame", "plain") not in ("plain", "chamber"):
        fail(theme_id, f"tile.frame must be 'plain' or 'chamber', got {tile.get('frame')!r}")

    if "greetings" in data:
        greetings = data["greetings"]
        if not isinstance(greetings, dict):
            fail(theme_id, "greetings must be an object")
        for k, v in greetings.items():
            if not isinstance(v, str):
                fail(theme_id, f"greetings.{k} must be a string")

    if "season" in data:
        season = data["season"]
        if not isinstance(season, dict):
            fail(theme_id, "season must be an object")
        for key in ("from", "to"):
            if not MMDD_RE.match(str(season.get(key, ""))):
                fail(theme_id, f"season.{key} must be MM-DD, got {season.get(key)!r}")
        if not isinstance(season.get("priority"), int):
            fail(theme_id, "season.priority must be an integer")

    # Every referenced file must exist (already checked above); scan for stray
    # top-level garbage is not required. Validation passed.
    return data


def contrast_ratio(hex1: str, hex2: str) -> float:
    def lin(c: int) -> float:
        c = c / 255
        return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4

    def lum(hexval: str) -> float:
        h = hexval.lstrip("#")
        if len(h) == 8:
            h = h[2:]
        r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
        return 0.2126 * lin(r) + 0.7152 * lin(g) + 0.0722 * lin(b)

    l1, l2 = lum(hex1), lum(hex2)
    l1, l2 = max(l1, l2), min(l1, l2)
    return (l1 + 0.05) / (l2 + 0.05)


def build_zip(folder: Path, out_zip: Path) -> None:
    with zipfile.ZipFile(out_zip, "w", zipfile.ZIP_DEFLATED) as zf:
        for path in sorted(folder.rglob("*")):
            if path.is_file():
                zf.write(path, path.relative_to(folder))


def render_preview(theme: dict, folder: Path, out_jpg: Path) -> None:
    W, H = 640, 360
    colors = theme["colors"]

    def hx(c: str) -> tuple:
        c = c.lstrip("#")
        if len(c) == 8:
            c = c[2:]
        return tuple(int(c[i:i + 2], 16) for i in (0, 2, 4))

    bg = hx(colors["background"])
    wallpaper_rel = theme.get("wallpaper")
    if wallpaper_rel:
        with Image.open(folder / wallpaper_rel) as wp:
            im = wp.convert("RGB").resize((W, H), Image.LANCZOS)
        dim = theme.get("wallpaperDim", 0.3)
        if dim > 0:
            scrim = Image.new("RGB", (W, H), (0, 0, 0))
            im = Image.blend(im, scrim, dim)
    elif "backgroundGradient" in colors:
        stops = [hx(s) for s in colors["backgroundGradient"]]
        im = Image.new("RGB", (W, H), bg)
        n = len(stops) - 1
        for y in range(H):
            t = y / (H - 1)
            seg = min(int(t * n), n - 1)
            local_t = t * n - seg
            c0, c1 = stops[seg], stops[seg + 1]
            color = tuple(int(c0[i] + (c1[i] - c0[i]) * local_t) for i in range(3))
            im.paste(color, (0, y, W, y + 1))
    else:
        im = Image.new("RGB", (W, H), bg)

    draw = ImageDraw.Draw(im, "RGBA")
    surface = hx(colors["surface"])
    surface_focused = hx(colors["surfaceFocused"])
    on_surface = hx(colors["onSurface"])
    accent = hx(colors["accent"])
    radius = int(theme.get("tile", {}).get("cornerRadius", 14))

    tile_w, tile_h, gap = 160, 90, 16
    x0, y0 = 32, H - tile_h - 32
    for i in range(3):
        x = x0 + i * (tile_w + gap)
        fill = surface_focused + (255,) if i == 1 else surface + (200,)
        draw.rounded_rectangle([x, y0, x + tile_w, y0 + tile_h], radius=radius, fill=fill)
        draw.rectangle([x + 12, y0 + 12, x + 12 + 30, y0 + 12 + 6], fill=accent + (255,))
        draw.rectangle([x + 12, y0 + tile_h - 20, x + 70, y0 + tile_h - 14], fill=on_surface + (255,))

    draw.text((32, 28), theme["name"], fill=on_surface)
    im.convert("RGB").save(out_jpg, "JPEG", quality=85)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--themes-dir", default="themes")
    parser.add_argument("--dist-dir", default="dist")
    args = parser.parse_args()

    repo_root = Path(__file__).resolve().parent.parent
    themes_dir = (repo_root / args.themes_dir).resolve()
    dist_dir = (repo_root / args.dist_dir).resolve()
    catalog_dir = dist_dir / "catalog"
    catalog_dir.mkdir(parents=True, exist_ok=True)

    if not themes_dir.is_dir():
        print(f"error: themes dir not found: {themes_dir}", file=sys.stderr)
        return 1

    theme_folders = sorted(p for p in themes_dir.iterdir() if p.is_dir())
    if not any(p.name == "default" for p in theme_folders):
        print("error: themes/default must exist", file=sys.stderr)
        return 1

    entries = []
    errors = []
    contrast_report = []

    for folder in theme_folders:
        try:
            data = validate_theme(folder)
        except ThemeError as e:
            errors.append(str(e))
            continue

        colors = data["colors"]
        c_text_surface = contrast_ratio(colors["onSurface"], colors["surface"])
        c_muted_surface = contrast_ratio(colors["onSurfaceMuted"], colors["surface"])
        c_text_bg = contrast_ratio(colors["onSurface"], colors["background"])
        contrast_report.append(
            f"  {data['id']:20s} onSurface/surface={c_text_surface:5.2f}  "
            f"onSurfaceMuted/surface={c_muted_surface:5.2f}  onSurface/bg={c_text_bg:5.2f}"
        )
        if c_text_surface < 4.5:
            errors.append(
                f"[{data['id']}] onSurface vs surface contrast {c_text_surface:.2f} < 4.5:1"
            )

        theme_id, version = data["id"], data["version"]
        zip_name = f"{theme_id}-{version}.zip"
        jpg_name = f"{theme_id}-{version}.jpg"
        zip_path = catalog_dir / zip_name
        jpg_path = catalog_dir / jpg_name

        build_zip(folder, zip_path)
        render_preview(data, folder, jpg_path)

        sha256 = hashlib.sha256(zip_path.read_bytes()).hexdigest()
        size = zip_path.stat().st_size

        entries.append({
            "id": theme_id,
            "name": data["name"],
            "version": version,
            "url": zip_name,
            "sha256": sha256,
            "size": size,
            "preview": jpg_name,
        })

    print("Contrast report:")
    for line in contrast_report:
        print(line)

    if errors:
        print("\nValidation errors:", file=sys.stderr)
        for e in errors:
            print(f"  {e}", file=sys.stderr)
        return 1

    entries.sort(key=lambda e: e["id"])
    index = {"schema": 1, "themes": entries}
    (catalog_dir / "index.json").write_text(json.dumps(index, indent=2) + "\n")

    print(f"\nBuilt catalog with {len(entries)} themes -> {catalog_dir / 'index.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
