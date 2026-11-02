#!/usr/bin/env python3
"""Create the homelab's GitHub Apps with GitHub's manifest flow; credentials go straight into the
Ansible vault.

GitHub has no API that creates a GitHub App (or a fine-grained token). The manifest flow is the
closest thing: this serves a local page that posts the app definition below to GitHub, you click
"Create GitHub App" (and, for apps that need one, "Install"), and `gh api` trades the one-time
code for the app's id, client id/secret, private key and webhook secret. Nothing is pasted or
printed.

    scripts/github-app.py cabotage   # Cabotage: webhook builds + GitHub login
    scripts/github-app.py grafana    # Grafana Git Sync: install on JacobCoffee/homelab only
    scripts/github-app.py cabotage grafana

Needs: gh (logged in), ansible-vault (via mise), openssl.
"""

from __future__ import annotations

import base64
import html
import http.server
import json
import os
import secrets
import subprocess
import sys
import tempfile
import threading
import time
import urllib.parse
import urllib.request
import webbrowser
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ANSIBLE_DIR = ROOT / "ansible"
VAULT_FILE = ANSIBLE_DIR / "inventory/group_vars/all/vault.yml"
OWNER = "JacobCoffee"
GRAFANA_REPO = f"{OWNER}/homelab"

# Permissions are what each app's code actually calls -- see the comments per entry.
APPS = {
    "cabotage": {
        "manifest": {
            "name": "cabotage-z7x",
            "description": "Cabotage PaaS on the z7x.org homelab",
            "url": "https://cabotage.apps.z7x.org",
            # The one public path into Cabotage (external Envoy Gateway).
            "hook_attributes": {"url": "https://cabotage.z7x.org/github/hooks", "active": True},
            "callback_urls": ["https://cabotage.apps.z7x.org/auth/github/callback"],
            # Cabotage branches on `state`, so the OAuth callback doubles as the setup URL.
            "setup_url": "https://cabotage.apps.z7x.org/auth/github/callback",
            "public": False,
            "default_permissions": {
                "contents": "read",  # git clone for builds; push events
                "deployments": "write",  # creates deployments, posts deployment statuses
                "checks": "write",  # creates/updates check runs
                "pull_requests": "write",  # PR comments, PR list/files
                "statuses": "read",  # combined commit status
                "administration": "read",  # branch protection required checks
                "metadata": "read",
                "emails": "read",  # GET /user/emails at login ("Email addresses" in the UI)
            },
            # What process_github_hook handles; installation* events always arrive.
            "default_events": ["push", "pull_request", "deployment", "check_suite", "repository"],
        },
        "vault": {
            "id": "vault_cabotage_github_app_id",
            "client_id": "vault_cabotage_github_app_client_id",
            "client_secret": "vault_cabotage_github_app_client_secret",
            "pem": "vault_cabotage_github_app_private_key",
            "webhook_secret": "vault_cabotage_github_webhook_secret",
        },
        # Installed later from Cabotage's own UI, which ties the installation to your account.
        "install": False,
    },
    "grafana": {
        "manifest": {
            "name": "grafana-z7x-git-sync",
            "description": "Grafana Git Sync for the z7x.org homelab dashboards",
            "url": "https://grafana.z7x.org",
            # Grafana is internal-only, so it polls instead of receiving webhooks.
            "hook_attributes": {"url": "https://grafana.z7x.org/", "active": False},
            "public": False,
            "default_permissions": {
                "contents": "write",  # branch pushes for dashboard-save PRs
                "pull_requests": "write",  # opens those PRs
                "administration": "read",  # branch protection check (Grafana docs)
                "metadata": "read",
            },
            "default_events": [],
        },
        "vault": {
            "id": "vault_grafana_github_app_id",
            "pem": "vault_grafana_github_app_private_key",
            "installation_id": "vault_grafana_github_app_installation_id",
        },
        "install": True,
    },
}


def b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def app_jwt(app_id: int, pem: str) -> str:
    now = int(time.time())
    head = b64url(json.dumps({"alg": "RS256", "typ": "JWT"}).encode())
    body = b64url(json.dumps({"iat": now - 60, "exp": now + 540, "iss": str(app_id)}).encode())
    with tempfile.NamedTemporaryFile("w", delete=False) as key:
        os.chmod(key.name, 0o600)
        key.write(pem)
    try:
        sig = subprocess.run(
            ["openssl", "dgst", "-sha256", "-sign", key.name],
            input=f"{head}.{body}".encode(), capture_output=True, check=True,
        ).stdout
    finally:
        os.unlink(key.name)
    return f"{head}.{body}.{b64url(sig)}"


def github(method: str, path: str, token: str, payload: dict | None = None) -> dict:
    req = urllib.request.Request(
        f"https://api.github.com{path}",
        method=method,
        data=json.dumps(payload).encode() if payload is not None else None,
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        },
    )
    with urllib.request.urlopen(req, timeout=20) as resp:
        return json.loads(resp.read() or b"{}")


def vault_set(values: dict[str, str]) -> None:
    """Replace or append top-level keys; multi-line values become block scalars."""
    view = subprocess.run(
        ["mise", "exec", "--", "ansible-vault", "view", str(VAULT_FILE)],
        cwd=ANSIBLE_DIR, capture_output=True, text=True, check=True,
    ).stdout.splitlines()

    def render(key: str, value: str) -> list[str]:
        if "\n" in value.strip():
            return [f"{key}: |"] + ["  " + line for line in value.strip().splitlines()]
        return [f"{key}: {json.dumps(value)}"]

    out, seen, i = [], set(), 0
    while i < len(view):
        key = view[i].split(":", 1)[0]
        if key in values and not view[i].startswith((" ", "#")):
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

    with tempfile.NamedTemporaryFile("w", dir=VAULT_FILE.parent, delete=False) as tmp:
        os.chmod(tmp.name, 0o600)
        tmp.write("\n".join(out) + "\n")
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


class Flow:
    """One app: GET / posts the manifest, /created converts it, /installed records the install."""

    def __init__(self, name: str, port: int) -> None:
        self.name, self.spec, self.port = name, APPS[name], port
        self.state = secrets.token_urlsafe(24)
        self.done = threading.Event()
        self.error: str | None = None
        self.app: dict = {}
        base = f"http://127.0.0.1:{port}"
        self.manifest = dict(self.spec["manifest"], redirect_url=f"{base}/created")
        if self.spec["install"]:
            self.manifest["setup_url"] = f"{base}/installed"

    def page(self) -> str:
        action = f"https://github.com/settings/apps/new?state={self.state}"
        return (
            f"<!doctype html><title>{self.name}</title><body onload='document.forms[0].submit()'>"
            f"<form method=post action='{action}'>"
            f"<input type=hidden name=manifest value='{html.escape(json.dumps(self.manifest), quote=True)}'>"
            f"<button>Continue to GitHub</button></form>"
        )

    def created(self, query: dict) -> str:
        if query.get("state") != [self.state]:
            raise ValueError("state mismatch; ignoring callback")
        code = query["code"][0]
        app = json.loads(subprocess.run(
            ["gh", "api", "--method", "POST", f"/app-manifests/{code}/conversions"],
            capture_output=True, text=True, check=True,
        ).stdout)
        self.app = app
        keys = self.spec["vault"]
        values = {keys["id"]: str(app["id"]), keys["pem"]: app["pem"]}
        for field in ("client_id", "client_secret"):
            if field in keys:
                values[keys[field]] = app[field]
        if "webhook_secret" in keys:
            secret = app.get("webhook_secret") or secrets.token_hex(32)
            if not app.get("webhook_secret"):
                github("PATCH", "/app/hook/config", app_jwt(app["id"], app["pem"]),
                       {"secret": secret, "content_type": "json"})
            values[keys["webhook_secret"]] = secret
        vault_set(values)
        print(f"[{self.name}] created {app['html_url']} (id {app['id']}); vault updated", flush=True)
        if self.spec["install"]:
            return f"https://github.com/apps/{app['slug']}/installations/new"
        self.done.set()
        return ""

    def installed(self, query: dict) -> None:
        inst_id = int(query["installation_id"][0])
        token = app_jwt(self.app["id"], self.app["pem"])
        inst = github("GET", f"/app/installations/{inst_id}", token)
        access = github("POST", f"/app/installations/{inst_id}/access_tokens", token)
        repos = [r["full_name"] for r in
                 github("GET", "/installation/repositories", access["token"])["repositories"]]
        vault_set({self.spec["vault"]["installation_id"]: str(inst_id)})
        print(f"[{self.name}] installation {inst_id}: {inst['repository_selection']} {repos}", flush=True)
        if repos != [GRAFANA_REPO]:
            self.error = (f"installed on {inst['repository_selection']} repos {repos}; restrict it to "
                          f"{GRAFANA_REPO} at https://github.com/settings/installations/{inst_id}")
        self.done.set()


def run(name: str) -> str | None:
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), http.server.BaseHTTPRequestHandler)
    flow = Flow(name, server.server_address[1])

    class Handler(http.server.BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def reply(self, status: int, body: str, location: str = "") -> None:
            self.send_response(status)
            if location:
                self.send_header("Location", location)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(body.encode())

        def do_GET(self):
            url = urllib.parse.urlsplit(self.path)
            query = urllib.parse.parse_qs(url.query)
            try:
                if url.path == "/":
                    return self.reply(200, flow.page())
                if url.path == "/created":
                    nxt = flow.created(query)
                    if nxt:
                        return self.reply(302, "", nxt)
                    return self.reply(200, f"<p>{name}: app created, credentials are in the vault. "
                                           "You can close this tab.")
                if url.path == "/installed":
                    flow.installed(query)
                    msg = flow.error or "installed; credentials are in the vault. You can close this tab."
                    return self.reply(200, f"<p>{name}: {html.escape(msg)}")
                return self.reply(404, "not found")
            except Exception as exc:  # surface to both the browser and the terminal
                flow.error = f"{type(exc).__name__}: {exc}"
                flow.done.set()
                self.reply(500, f"<p>{name} failed: {html.escape(flow.error)}")

    server.RequestHandlerClass = Handler
    threading.Thread(target=server.serve_forever, daemon=True).start()
    start = f"http://127.0.0.1:{flow.port}/"
    print(f"[{name}] open {start} and click 'Create GitHub App'"
          + (" then install it on JacobCoffee/homelab only" if APPS[name]["install"] else ""), flush=True)
    webbrowser.open(start)
    flow.done.wait()
    time.sleep(0.5)  # let the final page flush
    server.shutdown()
    return flow.error


def main(argv: list[str]) -> int:
    names = argv or ["cabotage", "grafana"]
    unknown = [n for n in names if n not in APPS]
    if unknown:
        print(f"unknown app(s): {unknown}; choose from {sorted(APPS)}", file=sys.stderr)
        return 2
    failed = False
    for name in names:
        error = run(name)
        if error:
            print(f"[{name}] ERROR {error}", file=sys.stderr, flush=True)
            failed = True
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
