#!/usr/bin/env python3
"""Build Pocket ID's branding images from icons/ (stdlib only): python3 build.py

Writes into this directory:
  background.svg      the sign-in page's right panel: the lab's apps on three rings around the
                      Homelab mark. Each app sends a request to the centre, which answers, and the
                      app then shows in its brand colour. CSS animation only (Pocket ID shows it in
                      an <img>, where scripts never run); static under prefers-reduced-motion.
                      Also ../app/theme/background-still.svg, the same picture without motion,
                      which lab.css shows instead when the visitor asks for reduced motion.
  logo-light.svg      the Homelab mark for light mode (dark ink), logo-dark.svg for dark mode
  favicon.svg         the mark on its own dark tile, legible on any tab strip
  clients/<id>.svg    logos for the OIDC clients in scripts/pocket-id.py CLIENTS

scripts/pocket-id.py uploads these files (the still copy ships with the theme, via Flux). Re-run
this script after changing icons/ or the rings below, then re-run scripts/pocket-id.py and commit.
Icon sources and licences are listed in NOTICE.md.
"""

from __future__ import annotations

import base64
import colorsys
import math
import re
from pathlib import Path

HERE = Path(__file__).resolve().parent
ICONS = HERE / "icons"
FONT = HERE.parent / "app" / "theme" / "departure-mono.woff2"

AMBER = "#FFB224"
# Rings from the centre out: (radius, seconds per turn, direction, label, apps).
RINGS = [
    (170, 120, 1, "EDGE · OPNSENSE CARP · ENVOY GATEWAY · CLOUDFLARED · ADGUARD DNS ·",
     ["opnsense", "cloudflare", "envoyproxy", "adguard-home"]),
    (295, 170, -1, "PLATFORM · PROXMOX HNHV01 / HNHV02 · KUBERNETES · FLUX · CILIUM · OBSERVABILITY ·",
     ["proxmox", "kubernetes", "flux", "cilium", "grafana", "prometheus", "loki", "uptime-kuma"]),
    (425, 240, 1, "APPS · MEDIA · HOME · STATUS · *.Z7X.ORG · ONE PASSKEY ·",
     ["jellyfin", "sonarr", "radarr", "lidarr", "prowlarr", "bazarr", "sabnzbd", "qbittorrent",
      "seerr", "home-assistant", "homepage", "gluetun"]),
]
# simple-icons glyphs (icons/<name>.simple.svg) are single paths; these are their brand colours.
SIMPLE_HEX = {"envoyproxy": "#AC6199", "flux": "#5468FF", "homepage": "#009BD5"}
CYCLE = 26  # seconds between one app's sign-ins; the apps are spread evenly across it
NODE_R = 27
CORE = 128  # core tile size


# --- the Homelab mark: three rack units, the middle one lit -------------------------------------
def mark(ink: str, led: str = AMBER, size: float = 512, x: float = 0, y: float = 0) -> str:
    s = size / 512
    units = []
    for i, uy in enumerate((92, 212, 332)):
        # faceplate with two vent slots and a status LED, punched out with evenodd
        units.append(
            f'<path fill-rule="evenodd" fill="{ink}" d="M{x + 56 * s:.2f} {y + (uy + 18) * s:.2f}'
            f"a{18 * s:.2f} {18 * s:.2f} 0 0 1 {18 * s:.2f}-{18 * s:.2f}h{364 * s:.2f}"
            f"a{18 * s:.2f} {18 * s:.2f} 0 0 1 {18 * s:.2f} {18 * s:.2f}v{52 * s:.2f}"
            f"a{18 * s:.2f} {18 * s:.2f} 0 0 1-{18 * s:.2f} {18 * s:.2f}h-{364 * s:.2f}"
            f"a{18 * s:.2f} {18 * s:.2f} 0 0 1-{18 * s:.2f}-{18 * s:.2f}z"
            f"M{x + 104 * s:.2f} {y + (uy + 34) * s:.2f}h{120 * s:.2f}v{20 * s:.2f}h-{120 * s:.2f}z"
            f"M{x + 244 * s:.2f} {y + (uy + 34) * s:.2f}h{56 * s:.2f}v{20 * s:.2f}h-{56 * s:.2f}z"
            f"M{x + 392 * s:.2f} {y + (uy + 44) * s:.2f}m-{16 * s:.2f} 0a{16 * s:.2f} {16 * s:.2f} 0 1 0 {32 * s:.2f} 0"
            f'a{16 * s:.2f} {16 * s:.2f} 0 1 0-{32 * s:.2f} 0z"/>'
        )
        if i == 1:
            units.append(f'<circle cx="{x + 392 * s:.2f}" cy="{y + (uy + 44) * s:.2f}" r="{11 * s:.2f}" fill="{led}"/>')
    return "".join(units)


def mark_svg(ink: str) -> str:
    return f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 512 512">{mark(ink)}</svg>\n'


def favicon_svg() -> str:
    return (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 512 512">'
        '<rect width="512" height="512" rx="112" fill="#0E1319"/>'
        f"{mark('#E6EAEE', size=420, x=46, y=46)}</svg>\n"
    )


def lab_apps_svg() -> str:
    """lab-gateway ("Lab apps"): a ring with three apps on it around a lit centre."""
    return (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 512 512">'
        '<circle cx="256" cy="256" r="168" fill="none" stroke="#8E99A6" stroke-width="22"/>'
        f'<circle cx="256" cy="256" r="58" fill="{AMBER}"/>'
        '<circle cx="256" cy="88" r="52" fill="#E6EAEE" stroke="#0E1319" stroke-width="18"/>'
        '<circle cx="401" cy="340" r="52" fill="#E6EAEE" stroke="#0E1319" stroke-width="18"/>'
        '<circle cx="111" cy="340" r="52" fill="#E6EAEE" stroke="#0E1319" stroke-width="18"/>'
        "</svg>\n"
    )


# --- icons ---------------------------------------------------------------------------------------
def inner(svg: str) -> tuple[str, str]:
    view = re.search(r'viewBox="([^"]+)"', svg).group(1)
    body = re.sub(r"^.*?<svg[^>]*>|</svg>\s*$", "", svg, flags=re.S)
    return view, body


def prefix_ids(body: str, p: str) -> str:
    ids = re.findall(r'id="([^"]+)"', body)
    for i in ids:
        body = body.replace(f'id="{i}"', f'id="{p}{i}"').replace(f"url(#{i})", f"url(#{p}{i})")
        body = body.replace(f'href="#{i}"', f'href="#{p}{i}"')
    return body


def brand_hex(name: str, color_svg: str) -> str:
    if name in SIMPLE_HEX:
        return SIMPLE_HEX[name]
    best, score = "#8E99A6", -1.0
    for h in re.findall(r"#([0-9a-fA-F]{6})\b", color_svg):
        r, g, b = (int(h[i:i + 2], 16) / 255 for i in (0, 2, 4))
        _, sat, val = colorsys.rgb_to_hsv(r, g, b)
        if sat * val > score:
            best, score = f"#{h.upper()}", sat * val
    return best


def icon_symbols(name: str) -> tuple[str, str]:
    """(<symbol id=m-name> in currentColor, <symbol id=c-name> in brand colour), plus hex."""
    simple = ICONS / f"{name}.simple.svg"
    if simple.exists():
        view, body = inner(simple.read_text())
        mono = f'<symbol id="m-{name}" viewBox="{view}"><g fill="currentColor">{body}</g></symbol>'
        color = f'<symbol id="c-{name}" viewBox="{view}"><g fill="{SIMPLE_HEX[name]}">{body}</g></symbol>'
        return mono + color, SIMPLE_HEX[name]
    color_svg = (ICONS / f"{name}.svg").read_text()
    mview, mbody = inner((ICONS / f"{name}.mono.svg").read_text())
    mbody = re.sub(r"#(?:fff|ffffff|FFF|FFFFFF)\b", "currentColor", prefix_ids(mbody, f"m{name}-"))
    cview, cbody = inner(color_svg)
    cbody = prefix_ids(cbody, f"c{name}-")
    return (
        f'<symbol id="m-{name}" viewBox="{mview}">{mbody}</symbol>'
        f'<symbol id="c-{name}" viewBox="{cview}">{cbody}</symbol>',
        brand_hex(name, color_svg),
    )


# --- background ----------------------------------------------------------------------------------
STYLE = """
@font-face{font-family:DM;src:url(data:font/woff2;base64,__FONT__) format("woff2")}
svg{--ground:#0B0E12;--ground-edge:#07090C;--ink:#E6EAEE;--quiet:#8E99A6;--line:rgb(230 234 238/.09);
--grid:rgb(230 234 238/.11);--node:#131A23;--node-edge:rgb(230 234 238/.11);--mono:#A3ADB8;
--tile:#151B23;--tile-edge:rgb(230 234 238/.14);--shade:#000;--label:rgb(230 234 238/.30);--amber:__AMBER__}
@media (prefers-color-scheme:light){svg{--ground:#EEF1F4;--ground-edge:#DCE2E8;--ink:#0E1319;--quiet:#5A6470;
--line:rgb(14 19 25/.10);--grid:rgb(14 19 25/.13);--node:#F8FAFB;--node-edge:rgb(14 19 25/.10);--mono:#6B7683;
--tile:#FFFFFF;--tile-edge:rgb(14 19 25/.10);--shade:#3A4A5C;--label:rgb(14 19 25/.38);--amber:#E89A00}}
.label{font:11px DM,ui-monospace,monospace;letter-spacing:2.2px;fill:var(--label)}
.host{font:14px DM,ui-monospace,monospace;letter-spacing:1.5px;fill:var(--quiet)}
.ring{animation:spin var(--t) linear infinite}
.ring.rev{animation-direction:reverse}
.upright{animation:spin var(--t) linear infinite reverse}
.rev .upright{animation-direction:normal}
@keyframes spin{to{transform:rotate(360deg)}}
.ev{animation-duration:__CYCLE__s;animation-iteration-count:infinite;animation-timing-function:linear;animation-delay:var(--d)}
.spoke{opacity:0;animation-name:spoke}
.req{opacity:0;animation-name:req}
.ans{opacity:0;animation-name:ans}
.lit{opacity:0;animation-name:lit}
.halo{opacity:0;animation-name:halo}
.rip{opacity:0;animation-name:rip}
.mono{color:var(--mono)}
@keyframes spoke{0%{opacity:0}3%{opacity:.55}10%{opacity:.55}14%{opacity:0}100%{opacity:0}}
@keyframes req{0%{opacity:0;transform:translateX(var(--from))}1%{opacity:1}
5%{opacity:1;transform:translateX(var(--to))}5.5%,100%{opacity:0;transform:translateX(var(--to))}}
@keyframes ans{0%,6%{opacity:0;transform:translateX(var(--to))}6.5%{opacity:1}
10%{opacity:1;transform:translateX(var(--from))}10.5%,100%{opacity:0;transform:translateX(var(--from))}}
@keyframes lit{0%,10%{opacity:0}12%{opacity:1}26%{opacity:1}34%,100%{opacity:0}}
@keyframes halo{0%,10%{opacity:0;transform:scale(1)}12%{opacity:.9;transform:scale(1)}
20%{opacity:0;transform:scale(1.55)}100%{opacity:0;transform:scale(1.55)}}
@keyframes rip{0%,5%{opacity:0;transform:scale(.55)}5.5%{opacity:.7}12%,100%{opacity:0;transform:scale(1.25)}}
@media (prefers-reduced-motion:reduce){__STILL__}
"""
# Browsers don't reliably pass prefers-reduced-motion into an SVG shown as an <img>, so the theme
# also carries a still copy (../app/theme/background-still.svg) that lab.css swaps in.
STILL = "*{animation:none!important}.req,.ans,.rip,.spoke,.halo{display:none}"


def background_svg(still: bool = False) -> str:
    defs, layers = [], []
    font = base64.b64encode(FONT.read_bytes()).decode()
    hexes: dict[str, str] = {}
    for _, _, _, _, apps in RINGS:
        for a in apps:
            sym, hexes[a] = icon_symbols(a)
            defs.append(sym)
    # every app gets an evenly spaced slot in the cycle, interleaved across rings
    order: list[str] = []
    queues = [list(r[4]) for r in RINGS]
    while any(queues):
        for q in queues:
            if q:
                order.append(q.pop(0))
    slot = {a: i * CYCLE / len(order) for i, a in enumerate(order)}

    rip, core_out = [], CORE / 2 + 8
    for ri, (radius, period, direction, label, apps) in enumerate(RINGS):
        n = len(apps)
        offset = 360 / n / 2 + ri * 11
        parts = [
            f'<circle r="{radius}" fill="none" stroke="var(--line)"/>',
            # a short lit arc that travels with the ring
            f'<circle r="{radius}" fill="none" stroke="var(--amber)" stroke-opacity=".38" stroke-width="1.5" '
            f'stroke-dasharray="{radius * 0.42:.1f} {2 * math.pi * radius:.1f}" stroke-linecap="round" '
            f'transform="rotate({40 + ri * 120})"/>',
            f'<path id="lp{ri}" fill="none" d="M{-(radius - 44)} 0a{radius - 44} {radius - 44} 0 1 1 {2 * (radius - 44)} 0'
            f'a{radius - 44} {radius - 44} 0 1 1 -{2 * (radius - 44)} 0"/>',
            f'<text class="label"><textPath href="#lp{ri}" startOffset="{3 + ri * 9}%">{label}</textPath></text>',
        ]
        for i, a in enumerate(apps):
            ang = offset + i * 360 / n
            ev = f'style="--d:-{CYCLE - slot[a]:.2f}s"'
            frm, to = radius - NODE_R - 8, core_out
            parts.append(
                f'<g transform="rotate({ang:.2f})">'
                f'<line class="ev spoke" {ev} x1="{to}" x2="{frm}" stroke="url(#spoke)" stroke-width="1.2"/>'
                f'<g class="ev req" style="--d:-{CYCLE - slot[a]:.2f}s;--from:{frm}px;--to:{to}px">'
                f'<rect x="0" y="-1.75" width="30" height="3.5" rx="1.75" fill="url(#pkt-in)"/></g>'
                f'<g class="ev ans" style="--d:-{CYCLE - slot[a]:.2f}s;--from:{frm}px;--to:{to}px">'
                f'<rect x="-30" y="-1.75" width="30" height="3.5" rx="1.75" fill="url(#pkt-out)"/></g>'
                f'<g transform="translate({radius} 0) rotate({-ang:.2f})"><g class="upright">'
                f'<circle cy="7" r="{NODE_R + 3}" fill="url(#shade)"/>'
                f'<circle r="{NODE_R}" fill="var(--node)" stroke="var(--node-edge)"/>'
                f'<circle class="ev halo" {ev} r="{NODE_R}" fill="none" stroke="{hexes[a]}" stroke-width="1.5"/>'
                f'<use class="mono" href="#m-{a}" x="-14" y="-14" width="28" height="28"/>'
                f'<use class="ev lit" {ev} href="#c-{a}" x="-14" y="-14" width="28" height="28"/>'
                f"</g></g></g>"
            )
            rip.append(f'<circle class="ev rip" {ev} r="{CORE * 0.78:.0f}" fill="none" stroke="{hexes[a]}" stroke-width="1.2"/>')
        cls = "ring" + (" rev" if direction < 0 else "")
        layers.append(f'<g class="{cls}" style="--t:{period}s">{"".join(parts)}</g>')

    half = CORE / 2
    core = (
        f'<rect x="{-half}" y="{-half + 14}" width="{CORE}" height="{CORE}" rx="34" fill="var(--shade)" opacity=".35" filter="url(#soft)"/>'
        f'<rect x="{-half}" y="{-half}" width="{CORE}" height="{CORE}" rx="34" fill="var(--tile)" stroke="var(--tile-edge)"/>'
        f'<rect x="{-half + 1}" y="{-half + 1}" width="{CORE - 2}" height="{CORE / 2}" rx="33" fill="url(#sheen)"/>'
        f'<g transform="translate(-42 -42)"><svg width="84" height="84" viewBox="0 0 512 512">{mark("var(--ink)")}</svg></g>'
        f'<text class="host" y="{half + 34}" text-anchor="middle">id.z7x.org</text>'
    )
    return varstyles(f"""<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1200 1200" preserveAspectRatio="xMidYMid slice">
<title>The lab's apps, signing in through Pocket ID</title>
<style>{STYLE.replace("__FONT__", font).replace("__AMBER__", AMBER).replace("__CYCLE__", str(CYCLE)).replace("__STILL__", STILL) + (STILL if still else "")}</style>
<defs>
<radialGradient id="ground" cx=".5" cy=".5" r=".72"><stop offset="0" stop-color="var(--ground)"/><stop offset="1" stop-color="var(--ground-edge)"/></radialGradient>
<radialGradient id="warm" cx=".5" cy=".5" r=".5"><stop offset="0" stop-color="var(--amber)" stop-opacity=".10"/><stop offset="1" stop-color="var(--amber)" stop-opacity="0"/></radialGradient>
<radialGradient id="fade" cx=".5" cy=".5" r=".5"><stop offset=".35" stop-color="#fff"/><stop offset="1" stop-color="#fff" stop-opacity="0"/></radialGradient>
<radialGradient id="shade" cx=".5" cy=".5" r=".5"><stop offset=".55" stop-color="var(--shade)" stop-opacity=".32"/><stop offset="1" stop-color="var(--shade)" stop-opacity="0"/></radialGradient>
<linearGradient id="sheen" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="#fff" stop-opacity=".06"/><stop offset="1" stop-color="#fff" stop-opacity="0"/></linearGradient>
<linearGradient id="spoke" gradientUnits="objectBoundingBox" x1="0" x2="1"><stop offset="0" stop-color="var(--amber)" stop-opacity=".7"/><stop offset="1" stop-color="var(--amber)" stop-opacity=".05"/></linearGradient>
<linearGradient id="pkt-in" x1="0" x2="1"><stop offset="0" stop-color="var(--amber)"/><stop offset="1" stop-color="var(--amber)" stop-opacity="0"/></linearGradient>
<linearGradient id="pkt-out" x1="0" x2="1"><stop offset="0" stop-color="var(--ink)" stop-opacity="0"/><stop offset="1" stop-color="var(--ink)"/></linearGradient>
<pattern id="dots" width="30" height="30" patternUnits="userSpaceOnUse"><circle cx="15" cy="15" r="1.1" fill="var(--grid)"/></pattern>
<mask id="gridfade"><rect x="-600" y="-600" width="1200" height="1200" fill="url(#fade)"/></mask>
<filter id="soft" x="-50%" y="-50%" width="200%" height="200%"><feGaussianBlur stdDeviation="16"/></filter>
{"".join(defs)}
</defs>
<rect width="1200" height="1200" fill="url(#ground)"/>
<g transform="translate(600 600)">
<rect x="-600" y="-600" width="1200" height="1200" fill="url(#dots)" mask="url(#gridfade)"/>
<circle r="330" fill="url(#warm)"/>
<circle r="{radius_ticks()}" fill="none" stroke="var(--line)" stroke-width="7" stroke-dasharray="1 {2 * math.pi * radius_ticks() / 180 - 1:.3f}"/>
{"".join(rip)}
{"".join(layers)}
{core}
</g>
</svg>
""")


def varstyles(svg: str) -> str:
    """Presentation attributes can't hold var(); move fill="var(--x)" & co. into style=""."""

    def tag(m: re.Match) -> str:
        t = m.group(0)
        decls = [f"{k}:{v}" for k, v in re.findall(r'\s(fill|stroke|stop-color)="(var\([^"]+\))"', t)]
        if not decls:
            return t
        t = re.sub(r'\s(?:fill|stroke|stop-color)="var\([^"]+\)"', "", t)
        if ' style="' in t:
            return t.replace(' style="', f' style="{";".join(decls)};', 1)
        return re.sub(r"(/?>)$", f' style="{";".join(decls)}"\\1', t)

    return re.sub(r"<[a-zA-Z][^<>]*>", tag, svg)


def radius_ticks() -> int:
    return RINGS[-1][0] + 44


def main() -> None:
    (HERE / "clients").mkdir(exist_ok=True)
    outputs = {
        "background.svg": background_svg(),
        "../app/theme/background-still.svg": background_svg(still=True),
        "logo-light.svg": mark_svg("#0E1319"),
        "logo-dark.svg": mark_svg("#E6EAEE"),
        "favicon.svg": favicon_svg(),
        "clients/lab-gateway.svg": lab_apps_svg(),
    }
    # the apps with their own OIDC client use their colour logo
    for cid, icon in {"grafana": "grafana", "proxmox": "proxmox", "jellyfin": "jellyfin", "opnsense": "opnsense"}.items():
        outputs[f"clients/{cid}.svg"] = (ICONS / f"{icon}.svg").read_text()
    view, body = inner((ICONS / "flux.simple.svg").read_text())
    outputs["clients/flux.svg"] = f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="{view}"><g fill="{SIMPLE_HEX["flux"]}">{body}</g></svg>\n'
    for name, text in outputs.items():
        (HERE / name).write_text(text)
        print(f"{name}: {len(text.encode()) // 1024} KiB")


if __name__ == "__main__":
    main()
