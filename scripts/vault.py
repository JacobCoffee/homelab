"""The Ansible vault (ansible/inventory/group_vars/all/vault.yml) for the scripts here: read it,
set or remove top-level keys. Every write re-encrypts with ansible-vault (via mise) and leaves the
file mode 600. Values never touch argv or a plaintext file outside the vault's own directory.
"""

from __future__ import annotations

import json
import os
import subprocess
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ANSIBLE_DIR = ROOT / "ansible"
VAULT_FILE = ANSIBLE_DIR / "inventory/group_vars/all/vault.yml"


def _view() -> list[str]:
    return subprocess.run(
        ["mise", "exec", "--", "ansible-vault", "view", str(VAULT_FILE)],
        cwd=ANSIBLE_DIR, capture_output=True, text=True, check=True,
    ).stdout.splitlines()


def vault_get() -> dict[str, str]:
    """Every top-level key. Needs PyYAML (the uv scripts here declare it)."""
    import yaml

    return {k: str(v) for k, v in (yaml.safe_load("\n".join(_view())) or {}).items()}


def _write(lines: list[str]) -> None:
    with tempfile.NamedTemporaryFile("w", dir=VAULT_FILE.parent, delete=False) as tmp:
        os.chmod(tmp.name, 0o600)
        tmp.write("\n".join(lines) + "\n")
    try:
        subprocess.run(
            ["mise", "exec", "--", "ansible-vault", "encrypt", tmp.name],
            cwd=ANSIBLE_DIR, capture_output=True, check=True,
        )
        os.chmod(tmp.name, 0o600)
        os.replace(tmp.name, VAULT_FILE)
    finally:
        if os.path.exists(tmp.name):
            os.unlink(tmp.name)
    os.chmod(VAULT_FILE, 0o600)


def _edit(values: dict[str, str], remove: set[str]) -> None:
    view = _view()

    def render(key: str, value: str) -> list[str]:
        if "\n" in value.strip():
            return [f"{key}: |"] + ["  " + line for line in value.strip().splitlines()]
        return [f"{key}: {json.dumps(value)}"]

    out, seen, i = [], set(), 0
    while i < len(view):
        key = view[i].split(":", 1)[0]
        if (key in values or key in remove) and not view[i].startswith((" ", "#")):
            if key in values:
                out += render(key, values[key])
            seen.add(key)
            i += 1
            while i < len(view) and (view[i].startswith("  ") or not view[i].strip()):
                i += 1
            continue
        out.append(view[i])
        i += 1
    for key, value in values.items():
        if key not in seen:
            out += render(key, value)
    _write(out)


def vault_set(values: dict[str, str]) -> None:
    """Replace or append top-level keys; multi-line values become block scalars."""
    _edit(values, set())


def vault_delete(keys: set[str]) -> None:
    """Remove top-level keys (missing ones are ignored)."""
    _edit({}, keys)
