#!/usr/bin/env python3
"""Set Ansible vault keys from hidden prompts: nothing echoed, no editor, file stays mode 600.

    scripts/vault-set.py vault_cabotage_discord_client_id vault_cabotage_discord_client_secret

Uses scripts/vault.py (replace-or-append, multi-line values as block scalars). An empty answer
leaves that key unchanged.
"""

import getpass
import sys

from vault import vault_set


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
    vault_set(values)
    print(f"vault updated: {', '.join(values)}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
