#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.12"
# dependencies = ["pyyaml>=6"]
# ///
"""Push vault_jellyfin_api_key into kubernetes/apps/media/jellyfin/app/secret.sops.yaml's
JELLYFIN_API_KEY, so the jellyfin-sso-install Job can authenticate to Jellyfin's API.

    scripts/vault-set.py vault_jellyfin_api_key   # after creating the key in Dashboard > API Keys
    scripts/jellyfin-sso-key.py                   # push it into the sops secret, then commit

The value never touches argv; it goes from the vault straight to `sops set --value-stdin`.
"""

import json
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from vault import ROOT, vault_get  # noqa: E402

SECRET = ROOT / "kubernetes/apps/media/jellyfin/app/secret.sops.yaml"


def main() -> int:
    key = vault_get().get("vault_jellyfin_api_key")
    if not key:
        print("vault_jellyfin_api_key not set; run scripts/vault-set.py vault_jellyfin_api_key first",
              file=sys.stderr)
        return 1
    subprocess.run(
        ["mise", "-C", str(ROOT), "exec", "--", "sops", "set", "--value-stdin", str(SECRET),
         '["stringData"]["JELLYFIN_API_KEY"]'],
        input=json.dumps(key), text=True, check=True,
    )
    print(f"set JELLYFIN_API_KEY in {SECRET}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
