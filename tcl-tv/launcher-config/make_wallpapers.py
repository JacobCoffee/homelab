#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = ["numpy>=2", "pillow>=11"]
# ///
"""Draws launcher-config/wallpapers/ for the "Local Dimming" theme: the QM8L's own backlight, rendered.

Each picture simulates a mini-LED panel showing a single soft light. The light is sampled into a grid
of dimming zones (each zone is driven to the brightest pixel it covers, as local dimming does), every
zone's LED is drawn as a point, and the light spreads through a diffuser with a tight core and a long
halo, the "blooming" a mini-LED is known for. Nothing but code: no photos, artwork or fonts.

    launcher-config/make_wallpapers.py          # writes wallpapers/{morning,evening,night}-{1,2,3}.jpg

Kept dark where the launcher draws (themes/README.md, Wallpapers): the clock and the top-right buttons
sit on black, the light lives behind and below the dock, and nothing in the hero name's band is bright.
Linear-light maths, sRGB out, triangular dither before 8-bit so the dark gradients never band.
"""
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from PIL import Image

W, H = 1920, 1080
PITCH = 24                              # zone pitch in px: an 80 x 45 zone array
ZX, ZY = W // PITCH, H // PITCH
OUT = Path(__file__).resolve().parent / "wallpapers"
rng = np.random.default_rng(7331)


@dataclass(frozen=True)
class Light:
    x: float                            # centre, fraction of width
    y: float                            # centre, fraction of height
    rx: float                           # radius, fraction of width
    ry: float                           # radius, fraction of height
    gain: float                         # linear intensity at the centre
    rgb: tuple[float, float, float]     # linear colour, roughly unit luminance


def srgb_to_linear(hex_colour: str) -> tuple[float, float, float]:
    c = np.array([int(hex_colour[i:i + 2], 16) / 255 for i in (1, 3, 5)])
    lin = np.where(c <= 0.04045, c / 12.92, ((c + 0.055) / 1.055) ** 2.4)
    lum = float(lin @ [0.2126, 0.7152, 0.0722])
    return tuple(lin / lum)


# Three moods, three frames each: the frames drift the light so a 20-minute rotation feels alive.
# Each light sits right of the hero name and around the dock's top edge, so the tiles are backlit and
# the name, clock and buttons stay on black.
ICE = srgb_to_linear("#A8DCFF")
SKY = srgb_to_linear("#4F8BFF")
AMBER = srgb_to_linear("#FF8A3D")
ROSE = srgb_to_linear("#FF3D7A")
COBALT = srgb_to_linear("#2F5BFF")
VIOLET = srgb_to_linear("#7A3DFF")

MOODS: dict[str, list[list[Light]]] = {
    # Sunrise to noon: cold daylight high on the right, a blue tail running down behind the dock.
    "morning": [
        [Light(0.80, 0.56, 0.13, 0.12, 1.0, ICE), Light(0.64, 0.70, 0.20, 0.09, 0.35, SKY)],
        [Light(0.74, 0.55, 0.13, 0.12, 1.0, ICE), Light(0.58, 0.70, 0.20, 0.09, 0.35, SKY)],
        [Light(0.85, 0.57, 0.12, 0.12, 1.0, ICE), Light(0.70, 0.70, 0.19, 0.09, 0.35, SKY)],
    ],
    # Noon to after sunset: amber over the middle of the dock, rose bleeding off to the right.
    "evening": [
        [Light(0.56, 0.62, 0.14, 0.12, 1.0, AMBER), Light(0.74, 0.74, 0.20, 0.10, 0.50, ROSE)],
        [Light(0.62, 0.61, 0.14, 0.12, 1.0, AMBER), Light(0.80, 0.74, 0.19, 0.10, 0.50, ROSE)],
        [Light(0.50, 0.63, 0.14, 0.12, 1.0, AMBER), Light(0.68, 0.74, 0.20, 0.10, 0.50, ROSE)],
    ],
    # Night: one tight cobalt cluster with a violet tail. The dimmest set.
    "night": [
        [Light(0.68, 0.64, 0.12, 0.11, 0.9, COBALT), Light(0.84, 0.72, 0.16, 0.10, 0.40, VIOLET)],
        [Light(0.62, 0.65, 0.12, 0.11, 0.9, COBALT), Light(0.78, 0.73, 0.16, 0.10, 0.40, VIOLET)],
        [Light(0.74, 0.63, 0.11, 0.11, 0.9, COBALT), Light(0.90, 0.71, 0.15, 0.10, 0.40, VIOLET)],
    ],
}
EXPOSURE = {"morning": 0.18, "evening": 0.19, "night": 0.16}


def smoothstep(e0: float, e1: float, x: np.ndarray) -> np.ndarray:
    t = np.clip((x - e0) / (e1 - e0), 0, 1)
    return t * t * (3 - 2 * t)


def target(lights: list[Light], xs: np.ndarray, ys: np.ndarray) -> np.ndarray:
    """The picture the panel is asked to show, linear RGB at (ys, xs), before any dimming."""
    out = np.zeros(xs.shape + (3,))
    for light in lights:
        d2 = ((xs - light.x) / light.rx) ** 2 + ((ys - light.y) / light.ry) ** 2
        out += light.gain * np.exp(-0.5 * d2)[..., None] * np.array(light.rgb)
    # The top of the screen is the clock's and the buttons': no zone up there is ever lit.
    return out * smoothstep(0.18, 0.44, ys)[..., None]


def blur(img: np.ndarray, sigma: float) -> np.ndarray:
    """Gaussian blur of an H x W x 3 image by FFT, edges padded by reflection (no wrap-around glow)."""
    pad = int(3 * sigma)
    padded = np.pad(img, ((pad, pad), (pad, pad), (0, 0)), mode="reflect")
    h, w = padded.shape[:2]
    fy = np.fft.fftfreq(h)[:, None]
    fx = np.fft.rfftfreq(w)[None, :]
    kernel = np.exp(-2 * (np.pi * sigma) ** 2 * (fx ** 2 + fy ** 2))
    out = np.fft.irfft2(np.fft.rfft2(padded, axes=(0, 1)) * kernel[..., None], s=(h, w), axes=(0, 1))
    return out[pad:pad + H, pad:pad + W]


def zone_drive(lights: list[Light]) -> np.ndarray:
    """Each zone driven to the brightest light it covers (sampled 4 x 4 inside the zone): ZY x ZX x 3."""
    sub = (np.arange(4) + 0.5) / 4
    zx = (np.arange(ZX)[:, None] + sub[None, :]).ravel() * PITCH / W
    zy = (np.arange(ZY)[:, None] + sub[None, :]).ravel() * PITCH / H
    xs, ys = np.meshgrid(zx, zy)
    samples = target(lights, xs, ys).reshape(ZY, 4, ZX, 4, 3)
    lum = samples @ np.array([0.2126, 0.7152, 0.0722])
    brightest = lum.reshape(ZY, 4, ZX, 4).transpose(0, 2, 1, 3).reshape(ZY, ZX, 16).argmax(-1)
    flat = samples.transpose(0, 2, 1, 3, 4).reshape(ZY, ZX, 16, 3)
    return np.take_along_axis(flat, brightest[..., None, None], axis=2)[:, :, 0]


def led_tile() -> np.ndarray:
    """One zone's LED as seen through the diffuser: a soft point, peak 1, centred in its PITCH cell."""
    c = (np.arange(PITCH) + 0.5) - PITCH / 2
    return np.exp(-0.5 * (c[:, None] ** 2 + c[None, :] ** 2) / 1.7 ** 2)


def render(lights: list[Light], exposure: float) -> np.ndarray:
    drive = zone_drive(lights)
    blocks = np.kron(drive, np.ones((PITCH, PITCH, 1)))
    # Diffuser: a tight spread that softens the zone steps, plus the wide, faint halo (blooming).
    core = blur(blocks, 0.85 * PITCH)
    halo = blur(blocks, 7.0 * PITCH)
    light = exposure * (0.85 * core + 0.22 * halo)
    # The LED array itself, only where its zone runs hot: crisp points at the core, gone a little way out.
    lum = drive @ np.array([0.2126, 0.7152, 0.0722])
    heat = (np.clip(lum, 0, None) / lum.max()) ** 2.5
    dots = np.kron(drive * heat[..., None], led_tile()[..., None]) * exposure * 1.1
    out = light + dots
    # Bloom whitens what it saturates: the brightest light goes towards white, the tail keeps its colour.
    y = out @ np.array([0.2126, 0.7152, 0.0722])
    whiten = smoothstep(0.14, 0.60, y)[..., None]
    out = out * (1 - whiten) + y[..., None] * whiten
    # A floor a hair above black, cool-tinted, so the panel reads as "on" without lifting the blacks.
    return out + np.array([0.0006, 0.0007, 0.0011])


def encode(linear: np.ndarray) -> Image.Image:
    c = np.clip(linear, 0, 1)
    srgb = np.where(c <= 0.0031308, c * 12.92, 1.055 * c ** (1 / 2.4) - 0.055) * 255
    srgb += rng.random(srgb.shape) - rng.random(srgb.shape)        # triangular dither, +/- 1 LSB
    return Image.fromarray(np.clip(np.rint(srgb), 0, 255).astype(np.uint8))


def main() -> None:
    OUT.mkdir(exist_ok=True)
    for mood, frames in MOODS.items():
        for i, lights in enumerate(frames, 1):
            path = OUT / f"{mood}-{i}.jpg"
            encode(render(lights, EXPOSURE[mood])).save(path, quality=96, subsampling=0, optimize=True)
            print(f"{path.name}  {path.stat().st_size // 1024} KB")


if __name__ == "__main__":
    main()
