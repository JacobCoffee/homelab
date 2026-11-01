#!/usr/bin/env python3
"""Render config/hearth.local.json from config/hearth.example.json plus config/site.env.

The example holds what's the same for everyone (the shape, guild event count, app layout);
site.env holds this house: name, place, devices and service addresses. Environment variables
override site.env, which is how teststack/up.sh hands over its generated endpoints and password.

    tools/make-config.py            # writes config/hearth.local.json
    hearth/tools/push-config.sh config/hearth.local.json
"""

from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LINE = re.compile(r"^\s*([A-Z_][A-Z0-9_]*)=(.*)$")
KEYS = ("HEARTH_NAME", "HEARTH_TIMEZONE", "WEATHER_LAT", "WEATHER_LON", "WEATHER_LABEL", "WEATHER_UNITS",
        "GUILD_URL", "KUMA_URL", "KUMA_STATUS_SLUG", "ADGUARD_URL", "ADGUARD_USER", "ADGUARD_PASSWORD",
        "THEME_CATALOG_URL", "TV_HOST")


def site() -> dict[str, str]:
    path = Path(os.environ.get("SITE_ENV", ROOT / "config" / "site.env"))
    values: dict[str, str] = {}
    if path.is_file():
        for line in path.read_text().splitlines():
            if m := LINE.match(line):
                values[m[1]] = m[2].strip().strip("\"'")
    else:
        sys.exit("error: config/site.env not found. Copy config/site.env.example to config/site.env and fill it in.")
    values.update({k: os.environ[k] for k in KEYS if k in os.environ})
    return values


def main() -> None:
    s = site()
    get = lambda key: s.get(key, "").strip()  # noqa: E731
    config = json.loads((ROOT / "config" / "hearth.example.json").read_text())

    config["greetingName"] = get("HEARTH_NAME") or None
    config["timezone"] = get("HEARTH_TIMEZONE") or None
    config["weather"] = (
        {"lat": float(get("WEATHER_LAT")), "lon": float(get("WEATHER_LON")),
         "label": get("WEATHER_LABEL"), "units": get("WEATHER_UNITS") or "fahrenheit"}
        if get("WEATHER_LAT") else None
    )
    guild = config.get("guild") or {}
    config["guild"] = {**guild, "baseUrl": get("GUILD_URL")} if get("GUILD_URL") else None
    config["uptimeKuma"] = (
        {"baseUrl": get("KUMA_URL"), "statusPageSlug": get("KUMA_STATUS_SLUG") or "home"} if get("KUMA_URL") else None
    )
    config["adguard"] = (
        {"baseUrl": get("ADGUARD_URL"), "username": get("ADGUARD_USER") or "admin",
         "password": get("ADGUARD_PASSWORD")} if get("ADGUARD_URL") else None
    )
    config["themeCatalogUrl"] = get("THEME_CATALOG_URL") or None
    tv = config.get("tv") or {}
    config["tv"] = {**tv, "host": get("TV_HOST")} if get("TV_HOST") else None

    out = ROOT / "config" / "hearth.local.json"
    out.write_text(json.dumps({k: v for k, v in config.items() if v is not None}, indent=2) + "\n")
    out.chmod(0o600)
    print(f"wrote {out.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
