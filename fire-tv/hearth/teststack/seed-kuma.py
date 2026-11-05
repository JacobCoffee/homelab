#!/usr/bin/env -S uv run --script
# uptime-kuma-api2: maintained fork of the abandoned uptime-kuma-api (same import name), speaks Kuma 2.
# /// script
# requires-python = ">=3.11"
# dependencies = ["uptime-kuma-api2"]
# ///
"""Seed Uptime Kuma with an admin user, a handful of monitors, and a public
status page slug 'home'. Idempotent: skips setup if already configured and
skips monitors that already exist by name.

Usage: seed-kuma.py <base_url> <username> <password> <adguard_url>
"""
import sys
import time

from uptime_kuma_api import MonitorType, UptimeKumaApi, UptimeKumaException


def main() -> int:
    base_url, username, password, adguard_url = sys.argv[1:5]

    api = UptimeKumaApi(base_url, timeout=30, wait_events=0.5)
    try:
        needs_setup = api.need_setup()
        if needs_setup:
            api.setup(username, password)
        api.login(username, password)

        existing = {m["name"]: m for m in api.get_monitors()}

        monitors = [
            dict(type=MonitorType.HTTP, name="sidequest-guild.com",
                 url="https://sidequest-guild.com", interval=60),
            dict(type=MonitorType.HTTP, name="open-meteo api",
                 url="https://api.open-meteo.com/v1/forecast?latitude=0&longitude=0&current=temperature_2m",
                 interval=60),
            dict(type=MonitorType.HTTP, name="adguard home",
                 url=adguard_url, interval=60),
            dict(type=MonitorType.HTTP, name="definitely-down.invalid",
                 url="http://definitely-down.invalid:1", interval=60, maxretries=0),
        ]

        created_ids = []
        for mon in monitors:
            if mon["name"] in existing:
                created_ids.append(existing[mon["name"]]["id"])
                continue
            resp = api.add_monitor(**mon)
            created_ids.append(resp["monitorID"])

        status_pages = {sp["slug"]: sp for sp in api.get_status_pages()}
        if "home" not in status_pages:
            api.add_status_page(slug="home", title="Homelab")

        public_group_list = [{
            "name": "Homelab",
            "weight": 1,
            "monitorList": [{"id": mid} for mid in created_ids],
        }]
        api.save_status_page(
            "home",
            title="Homelab",
            description="Hearth test stack status page",
            publicGroupList=public_group_list,
        )

        print("Uptime Kuma seeded.")
        print(f"  monitors: {[m['name'] for m in monitors]}")
        print("  status page slug: home")
        return 0
    except UptimeKumaException as e:
        print(f"error seeding Uptime Kuma: {e}", file=sys.stderr)
        return 1
    finally:
        api.disconnect()


if __name__ == "__main__":
    # Kuma 2 answers HTTP a little before its socket API settles on a fresh volume; the first
    # attempt can fail with an empty error. Every step is idempotent, so just try again.
    for attempt in range(6):
        if main() == 0:
            raise SystemExit(0)
        time.sleep(5)
    raise SystemExit(1)
