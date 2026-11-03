#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.12"
# dependencies = ["pyyaml>=6"]
# ///
"""Copy the Usenet provider/indexer credentials from the vault into
kubernetes/apps/media/media-config/app/secret.sops.yaml, and bump the media-config Job's
credentials-rev annotation so Flux re-runs it (force: true re-creates the Job on a spec change).

    scripts/vault-set.py vault_newshosting_username vault_newshosting_password vault_nzbfinder_api_key
    scripts/media-config-secrets.py   # then commit both files it names

Values never touch argv; each goes from the vault straight to `sops set --value-stdin`. Keys
missing from the vault are left as they are in the secret.
"""

import json
import re
import subprocess
import sys
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent))
from vault import ROOT, vault_get  # noqa: E402

APP = ROOT / "kubernetes/apps/media/media-config/app"
SECRET = APP / "secret.sops.yaml"
JOB = APP / "job.yaml"
KEYS = {
    "vault_newshosting_username": "NEWSHOSTING_USERNAME",
    "vault_newshosting_password": "NEWSHOSTING_PASSWORD",
    "vault_nzbfinder_api_key": "NZBFINDER_API_KEY",
}


def main() -> int:
    vault = vault_get()
    present = {vk: sk for vk, sk in KEYS.items() if vault.get(vk)}
    if not present:
        print(f"none of {', '.join(KEYS)} are in the vault; run scripts/vault-set.py first", file=sys.stderr)
        return 1
    for vault_key, secret_key in present.items():
        subprocess.run(
            ["mise", "-C", str(ROOT), "exec", "--", "sops", "set", "--value-stdin", str(SECRET),
             f'["stringData"]["{secret_key}"]'],
            input=json.dumps(vault[vault_key]), text=True, check=True,
        )
        print(f"set {secret_key}")
    rev = yaml.safe_load(SECRET.read_text())["sops"]["lastmodified"]
    JOB.write_text(re.sub(r'(media-config/credentials-rev: )".*"', rf'\1"{rev}"', JOB.read_text(), count=1))
    print(f"credentials-rev {rev}; commit {SECRET.relative_to(ROOT)} and {JOB.relative_to(ROOT)}")
    missing = set(KEYS) - set(present)
    if missing:
        print(f"not in the vault yet (left unchanged): {', '.join(sorted(missing))}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
