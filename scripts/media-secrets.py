#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.12"
# dependencies = ["pyyaml>=6"]
# ///
"""Copy media-stack credentials from the vault into their SOPS secrets, and bump each consuming
Job's credentials-rev annotation so Flux re-runs it (force: true re-creates a Job on a spec
change).

    scripts/vault-set.py vault_newshosting_username vault_newshosting_password vault_nzbfinder_api_key \\
        vault_opensubtitles_username vault_opensubtitles_password vault_local_news_match
    scripts/media-secrets.py   # then commit the files it names

Values never touch argv; each goes from the vault straight to `sops set --value-stdin`. Keys
missing from the vault are left as they are in their secret.
"""

import json
import re
import subprocess
import sys
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent))
from vault import ROOT, vault_get  # noqa: E402

MEDIA = ROOT / "kubernetes/apps/media"
# secret file, the Job that reads it, and vault key -> secret key
TARGETS = [
    (MEDIA / "media-config/app/secret.sops.yaml", MEDIA / "media-config/app/job.yaml", {
        "vault_newshosting_username": "NEWSHOSTING_USERNAME",
        "vault_newshosting_password": "NEWSHOSTING_PASSWORD",
        "vault_nzbfinder_api_key": "NZBFINDER_API_KEY",
        # MyAnonamouse session for Prowlarr's searches (home IP, Charter ASN).
        "vault_mam_id": "MAM_ID",
    }),
    (MEDIA / "jellyfin/app/plugins-secret.sops.yaml", MEDIA / "jellyfin/app/plugins-job.yaml", {
        "vault_opensubtitles_username": "OPENSUBTITLES_USERNAME",
        "vault_opensubtitles_password": "OPENSUBTITLES_PASSWORD",
        # Not Ansible-sourced: copy it from Seerr's settings.json (main.apiKey) into the vault by
        # hand if you want it refreshed through this script; otherwise set it directly with
        # `sops set --value-stdin` (see the comment in plugins-secret.sops.yaml).
        "vault_seerr_api_key": "SEERR_API_KEY",
    }),
    # A regex for this market's local-news stations (Dispatcharr's setup.py), e.g. "Chicago|WGN".
    (MEDIA / "dispatcharr/app/secret.sops.yaml", MEDIA / "dispatcharr/app/setup-job.yaml", {
        "vault_local_news_match": "LOCAL_NEWS_MATCH",
    }),
    # Audiobookshelf's admin "jacob" (generated; Bitwarden "Homelab: Audiobookshelf").
    (MEDIA / "audiobookshelf/app/secret.sops.yaml", MEDIA / "audiobookshelf/app/setup-job.yaml", {
        "vault_audiobookshelf_password": "ABS_ROOT_PASSWORD",
    }),
    # Bindery's instance API key (read once from Bindery, Settings > General). The setup Jobs of
    # Bindery and Audiobookshelf use it, and it seeds a fresh install.
    (MEDIA / "bindery/app/secret.sops.yaml", MEDIA / "bindery/app/setup-job.yaml", {
        "vault_bindery_api_key": "BINDERY_API_KEY",
    }),
    # MyAnonamouse dynamic-seedbox session (VPN exit IP, "allow dynamic seedbox"). The
    # mam-upkeep container reads it as a mounted file, so no restart: job is None.
    (MEDIA / "gluetun-qbittorrent/app/secret.sops.yaml", None, {
        "vault_mam_seedbox_id": "MAM_SEEDBOX_ID",
    }),
]


def main() -> int:
    vault = vault_get()
    touched, missing = [], []
    for secret, job, keys in TARGETS:
        present = {vk: sk for vk, sk in keys.items() if vault.get(vk)}
        missing += [vk for vk in keys if vk not in present]
        for vault_key, secret_key in present.items():
            subprocess.run(
                ["mise", "-C", str(ROOT), "exec", "--", "sops", "set", "--value-stdin", str(secret),
                 f'["stringData"]["{secret_key}"]'],
                input=json.dumps(vault[vault_key]), text=True, check=True,
            )
            print(f"set {secret_key}")
        if not present:
            continue
        rev = yaml.safe_load(secret.read_text())["sops"]["lastmodified"]
        if job is not None:
            job.write_text(re.sub(r'(media-config/credentials-rev: )".*"', rf'\1"{rev}"', job.read_text(), count=1))
            touched.append(job)
        touched.append(secret)
    if not touched:
        print("none of the keys are in the vault; run scripts/vault-set.py first", file=sys.stderr)
        return 1
    print("commit: " + " ".join(str(p.relative_to(ROOT)) for p in touched))
    if missing:
        print(f"not in the vault yet (left unchanged): {', '.join(missing)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
