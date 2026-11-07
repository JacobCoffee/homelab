"""Codify Wizarr's Jellyfin media server, enabled libraries and the two post-invite wizard-step
presets, idempotently, so a fresh install converges without clicking through the admin UI by hand.

Wizarr's REST API (docs/API.md, /api/swagger.json) is read-only for servers/libraries/wizard
steps (GET only) -- every write this script needs goes through the same HTML admin forms a human
would use, over an authenticated session (confirmed against a throwaway v2026.10.1 pod, see the
Job's header comment):

1. Admin session: with DISABLE_BUILTIN_AUTH=true, a bare GET /login immediately calls
   login_user(AdminUser()) and sets a session cookie, no credentials checked at all
   (app/blueprints/auth/routes.py) -- *except* before Wizarr's own first-run /setup/ has created an
   AdminAccount row, when every route (including /login) redirects back to /setup/. A fresh install
   is bootstrapped by completing /setup/ with a random password first (DISABLE_BUILTIN_AUTH means
   that password is never actually used to sign in again).
2. Media server + libraries: POST /settings/servers/create (or .../<id>/edit for an existing one)
   is a plain `request.form` handler, not a FlaskForm -- confirmed empirically, no CSRF token
   needed. The library checkboxes it accepts are external ids; POST /settings/scan-libraries
   resolves name -> external id by asking Jellyfin directly (same call the "Scan Libraries" button
   makes), so this script never has to hardcode or separately query Jellyfin for them.
3. Wizard step presets ("Automatic requests", "Discord community"): created by POST
   /settings/wizard/create-preset, which *is* a FlaskForm (WizardPresetForm) and does enforce CSRF
   -- but its own GET page never renders a csrf_token field (app/templates/.../preset_form.html has
   no hidden_tag()/csrf_token), a template bug confirmed against the live source. Flask-WTF's CSRF
   secret is per-session, not per-form, so this script instead takes a token from a different
   admin form that *does* render one (GET /settings/connections/create, otherwise unused here) and
   submits it with the preset POST; confirmed working against the throwaway pod. Idempotency is
   checked first via GET /settings/wizard/export/<server_type>, a JSON dump of every existing step
   (no form, no side effects) -- create-preset itself has no dedupe and would add a duplicate step
   on every run otherwise.
"""

import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

SVC = "media.svc.cluster.local"
WIZARR = os.environ.get("WIZARR_URL", f"http://wizarr.{SVC}:5690").rstrip("/")

JELLYFIN_URL = os.environ.get("JELLYFIN_URL", f"http://jellyfin.{SVC}:8096")
JELLYFIN_API_KEY = os.environ["JELLYFIN_API_KEY"]
JELLYFIN_EXTERNAL_URL = "https://media.z7x.org"
SERVER_NAME = "HomeNet"
LIBRARY_NAMES = ["Movies", "Shows", "Music", "Anime", "Anime Movies"]  # Playlists stays off.

DISCORD_SERVER_ID = "809526790488981544"  # public guild id, not a secret  # gitleaks:allow
SEERR_URL = "https://requests.z7x.org"
# (preset_id, title, extra form fields) -- title must match PRESETS[id].title in
# app/services/wizard_presets.py, it's how this script recognises an already-created step.
PRESETS = [
    ("overseerr_requests", "Automatic requests", {"overseerr_url": SEERR_URL}),
    ("discord_community", "Discord community", {"discord_id": DISCORD_SERVER_ID}),
]

TIMEOUT = 30


class Session:
    """Cookie-carrying HTTP client for Wizarr's HTML admin UI -- there's no JSON write API.

    urllib's default opener follows redirects transparently (POST 302 -> GET, same as a browser),
    so the response status is always the *final* page's status (200), not the 302 a successful
    form POST actually returns. Success/failure is told apart by the final URL instead: a
    redirect away from the page just posted to means it was accepted, Flask re-rendering the same
    URL directly (no redirect at all) means a validation/connection error.
    """

    def __init__(self, base: str):
        self.base = base
        self.opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor())

    def request(self, method: str, path: str, data: dict | None = None) -> tuple[str, str]:
        body = urllib.parse.urlencode(data, doseq=True).encode() if data is not None else None
        req = urllib.request.Request(f"{self.base}{path}", data=body, method=method)
        try:
            with self.opener.open(req, timeout=TIMEOUT) as resp:
                return resp.geturl(), resp.read().decode(errors="replace")
        except urllib.error.HTTPError as e:
            return e.geturl(), e.read().decode(errors="replace")

    def get(self, path: str) -> tuple[str, str]:
        return self.request("GET", path)

    def post(self, path: str, data: dict) -> tuple[str, str]:
        """Returns (final_url, body); compare final_url against `path` to tell a redirect
        (accepted) apart from a direct re-render (rejected)."""
        return self.request("POST", path, data)


def wait_for_wizarr(sess: Session) -> None:
    for _ in range(90):
        try:
            sess.get("/")
            return
        except (urllib.error.URLError, OSError) as e:
            print(f"waiting for Wizarr: {e}")
        time.sleep(10)
    sys.exit("Wizarr never became reachable")


def csrf_token(html: str) -> str | None:
    m = re.search(r'name="csrf_token"[^>]*value="([^"]+)"', html)
    return m.group(1) if m else None


def ensure_setup_complete(sess: Session) -> None:
    """Wizarr redirects every route to /setup/ until an AdminAccount exists. DISABLE_BUILTIN_AUTH
    means that account is never actually used to sign in again (see module docstring), so a
    random, unrecorded password is fine."""
    url, body = sess.get("/setup/")
    if "/setup/" not in url:
        return  # Already past setup.
    token = csrf_token(body)
    if token is None:
        return  # No setup form rendered: already set up.
    password = f"{os.urandom(18).hex()}Aa1"  # Wizarr requires upper+lower+digit.
    url, body = sess.post("/setup/", {
        "csrf_token": token, "username": "admin", "password": password, "confirm": password})
    if "/setup/" in url:
        sys.exit("Wizarr first-run setup failed (still on /setup/ after posting the form)")
    print("Wizarr: first-run admin account created")


def login(sess: Session) -> None:
    sess.get("/login")


def any_csrf_token(sess: Session) -> str:
    """create-preset's own form never renders a csrf_token field (module docstring); borrow one
    from a different admin form in the same session instead."""
    _url, body = sess.get("/settings/connections/create")
    token = csrf_token(body)
    if token is None:
        sys.exit("Wizarr: could not find a CSRF token on any admin form")
    return token


def scan_jellyfin_libraries(sess: Session) -> dict[str, str]:
    """name -> external_id, resolved by asking Jellyfin directly (POST /settings/scan-libraries,
    the same call the "Scan Libraries" button makes). Only touches Library rows with
    server_id=NULL (a legacy, unbound table scoped away from any real MediaServer), so it's safe
    to call even once a real server already exists."""
    _url, body = sess.post("/settings/scan-libraries", {
        "server_type": "jellyfin", "server_url": JELLYFIN_URL, "api_key": JELLYFIN_API_KEY})
    pairs = re.findall(
        r'value="([^"]+)"\s+id="lib-\d+"\s*(checked)?\s*class=.*?>\s*([^<]+)</label>',
        body, re.DOTALL)
    by_name: dict[str, str] = {}
    for value, _checked, label in pairs:
        label = label.strip()
        if label != value:  # Skip unresolved/stale rows (their label falls back to the id).
            by_name.setdefault(label, value)
    return by_name


def find_server(sess: Session) -> tuple[int, dict] | tuple[None, None]:
    _url, body = sess.get("/settings/servers")
    m = re.search(r'data-server-id="(\d+)">.*?title="([^"]*)"', body, re.DOTALL)
    if m is None or m.group(2) != SERVER_NAME:
        return None, None
    server_id = int(m.group(1))
    _url, edit_html = sess.get(f"/settings/servers/{server_id}/edit")
    fields = {k: (re.search(rf'id="{k}"[^>]*value="([^"]*)"', edit_html) or [None, ""])[1]
              for k in ("server_url", "external_url", "api_key")}
    enabled = set(re.findall(r'value="([0-9a-f]{32})"\s+id="lib-\d+"\s+checked', edit_html))
    return server_id, {**fields, "libraries": enabled}


def ensure_media_server(sess: Session) -> None:
    by_name = scan_jellyfin_libraries(sess)
    missing = [n for n in LIBRARY_NAMES if n not in by_name]
    if missing:
        sys.exit(f"Jellyfin has no library named {', '.join(missing)}")
    desired_libraries = {by_name[n] for n in LIBRARY_NAMES}

    desired_fields = {
        "server_name": SERVER_NAME, "server_type": "jellyfin", "server_url": JELLYFIN_URL,
        "api_key": JELLYFIN_API_KEY, "external_url": JELLYFIN_EXTERNAL_URL,
    }
    form = {**desired_fields, "libraries": sorted(desired_libraries)}

    server_id, current = find_server(sess)
    if server_id is None:
        path = "/settings/servers/create"
        url, body = sess.post(path, form)
        if url.endswith(path):
            sys.exit(f"Wizarr: creating {SERVER_NAME} failed\n{body[:400]}")
        print(f"Wizarr: media server {SERVER_NAME} added")
        return

    changed = [k for k in ("server_url", "external_url", "api_key") if current[k] != desired_fields[k]]
    if current["libraries"] != desired_libraries:
        changed.append("libraries")
    if not changed:
        return
    path = f"/settings/servers/{server_id}/edit"
    url, body = sess.post(path, form)
    if url.endswith(path):
        sys.exit(f"Wizarr: updating {SERVER_NAME} failed\n{body[:400]}")
    print(f"Wizarr: media server {SERVER_NAME}: {', '.join(changed)}")


def existing_step_titles(sess: Session, server_type: str) -> set[str]:
    _url, body = sess.get(f"/settings/wizard/export/{server_type}")
    try:
        export = json.loads(body)
    except ValueError:
        return set()  # No steps at all yet for this server type: redirected to an HTML page.
    return {s["title"] for s in export.get("steps", []) if s.get("category") == "post_invite"}


def ensure_wizard_presets(sess: Session) -> None:
    current_titles = existing_step_titles(sess, "jellyfin")
    for preset_id, title, extra in PRESETS:
        if title in current_titles:
            continue
        token = any_csrf_token(sess)
        path = "/settings/wizard/create-preset"
        url, body = sess.post(path, {
            "csrf_token": token, "server_type": "jellyfin", "category": "post_invite",
            "preset_id": preset_id, **extra})
        if url.endswith(path):
            sys.exit(f"Wizarr: creating wizard step {title!r} failed\n{body[:400]}")
        print(f"Wizarr: wizard step {title!r} added")


def main() -> int:
    sess = Session(WIZARR)
    wait_for_wizarr(sess)
    ensure_setup_complete(sess)
    login(sess)
    ensure_media_server(sess)
    ensure_wizard_presets(sess)

    print("done")
    return 0


if __name__ == "__main__":
    sys.exit(main())
