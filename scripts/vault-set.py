#!/usr/bin/env python3
"""Set Ansible vault keys from hidden prompts: nothing echoed, no editor, file stays mode 600.

    scripts/vault-set.py vault_cabotage_discord_client_id vault_cabotage_discord_client_secret

Uses the same writer as scripts/github-app.py (replace-or-append, multi-line values as block
scalars). An empty answer leaves that key unchanged.
"""

import getpass
import importlib.util
import sys
from pathlib import Path

spec = importlib.util.spec_from_file_location("github_app", Path(__file__).with_name("github-app.py"))
github_app = importlib.util.module_from_spec(spec)
spec.loader.exec_module(github_app)


def main(keys: list[str]) -> int:
    if not keys:
        print(__doc__.strip(), file=sys.stderr)
        return 2
    values = {}
    for key in keys:
        value = getpass.getpass(f"{key} (hidden, empty = keep): ").strip()
        if value:
            values[key] = value
    if not values:
        print("nothing to change")
        return 0
    github_app.vault_set(values)
    print(f"vault updated: {', '.join(values)}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
