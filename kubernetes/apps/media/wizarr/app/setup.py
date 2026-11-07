"""Codify Wizarr's media servers (Jellyfin and Audiobookshelf), their enabled libraries and the two
post-invite wizard-step presets, idempotently, so a fresh install converges without clicking
through the admin UI by hand.

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
4. Audiobookshelf: Wizarr creates accounts there with an Audiobookshelf API key, which
   Audiobookshelf shows only once. The key Wizarr already holds (read back from the server's edit
   form) is kept while it still works (GET /api/me); otherwise this script signs in to
   Audiobookshelf as its admin (ABS_ROOT_PASSWORD), deletes older "wizarr" keys and mints a new one.
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
ABS_URL = os.environ.get("ABS_URL", f"http://audiobookshelf.{SVC}:13378")
ABS_ROOT_USERNAME = "jacob"
ABS_KEY_NAME = "wizarr"
# name, type, URLs and the libraries an invite grants; Jellyfin's Playlists stays off. Downloads:
# off for Jellyfin (as before), on for Audiobookshelf so friends can listen offline in its apps.
SERVERS = [
    {"name": "HomeNet", "type": "jellyfin", "url": JELLYFIN_URL,
     "external_url": "https://media.z7x.org", "api_key": os.environ["JELLYFIN_API_KEY"],
     "libraries": ["Movies", "Shows", "Music", "Anime", "Anime Movies"], "allow_downloads": False},
    {"name": "HomeNet Books", "type": "audiobookshelf", "url": ABS_URL,
     "external_url": "https://books.z7x.org", "api_key": None,  # minted, see ensure_abs_key()
     "libraries": ["Audiobooks", "Ebooks"], "allow_downloads": True},
]

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


def scan_libraries(sess: Session, server: dict) -> dict[str, str]:
    """name -> external_id, resolved by asking the media server directly (POST
    /settings/scan-libraries, the same call the "Scan Libraries" button makes). Only touches Library
    rows with server_id=NULL (a legacy, unbound table scoped away from any real MediaServer), so
    it's safe to call even once a real server already exists."""
    _url, body = sess.post("/settings/scan-libraries", {
        "server_type": server["type"], "server_url": server["url"], "api_key": server["api_key"]})
    pairs = re.findall(
        r'value="([^"]+)"\s+id="lib-\d+"\s*(checked)?\s*class=.*?>\s*([^<]+)</label>',
        body, re.DOTALL)
    by_name: dict[str, str] = {}
    for value, _checked, label in pairs:
        label = label.strip()
        if label != value:  # Skip unresolved/stale rows (their label falls back to the id).
            by_name.setdefault(label, value)
    return by_name


def find_server(sess: Session, name: str) -> tuple[int, dict] | tuple[None, None]:
    _url, body = sess.get("/settings/servers")
    cards = re.findall(r'data-server-id="(\d+)">.*?title="([^"]*)"', body, re.DOTALL)
    server_id = next((int(sid) for sid, title in cards if title == name), None)
    if server_id is None:
        return None, None
    _url, edit_html = sess.get(f"/settings/servers/{server_id}/edit")
    fields = {k: (re.search(rf'id="{k}"[^>]*value="([^"]*)"', edit_html) or [None, ""])[1]
              for k in ("server_url", "external_url", "api_key")}
    enabled = set(re.findall(r'value="([^"]+)"\s+id="lib-\d+"\s+checked', edit_html))
    downloads = re.search(r'<input[^>]*id="allow_downloads"[^>]*>', edit_html)
    return server_id, {**fields, "libraries": enabled,
                       "allow_downloads": bool(downloads and "checked" in downloads.group(0))}


def abs_call(method: str, path: str, token: str | None = None, body: dict | None = None):
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(f"{ABS_URL}{path}", method=method, headers=headers,
                                 data=json.dumps(body).encode() if body is not None else None)
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
            return resp.status, json.loads(resp.read() or b"null")
    except urllib.error.HTTPError as e:
        return e.code, None


def ensure_abs_key(sess: Session, server: dict) -> None:
    """Fill server["api_key"]: the key Wizarr already has if Audiobookshelf still accepts it, else a
    fresh one (Audiobookshelf can't show an existing key again)."""
    _server_id, current = find_server(sess, server["name"])
    if current and current["api_key"] and abs_call("GET", "/api/me", current["api_key"])[0] == 200:
        server["api_key"] = current["api_key"]
        return
    status, login = abs_call("POST", "/login", body={
        "username": ABS_ROOT_USERNAME, "password": os.environ["ABS_ROOT_PASSWORD"]})
    if status != 200:
        sys.exit(f"Audiobookshelf: login as {ABS_ROOT_USERNAME!r} -> {status} (has its setup Job run?)")
    token, root_id = login["user"]["accessToken"], login["user"]["id"]
    for key in abs_call("GET", "/api/api-keys", token)[1]["apiKeys"]:
        if key["name"] == ABS_KEY_NAME:
            abs_call("DELETE", f"/api/api-keys/{key['id']}", token)
    status, created = abs_call("POST", "/api/api-keys", token,
                               {"name": ABS_KEY_NAME, "userId": root_id, "isActive": True})
    if status != 200:
        sys.exit(f"Audiobookshelf: creating an API key -> {status}")
    server["api_key"] = created["apiKey"]["apiKey"]
    print("Audiobookshelf: new API key minted for Wizarr")


def ensure_media_server(sess: Session, server: dict) -> None:
    by_name = scan_libraries(sess, server)
    missing = [n for n in server["libraries"] if n not in by_name]
    if missing:
        sys.exit(f"{server['type']} has no library named {', '.join(missing)}")
    desired_libraries = {by_name[n] for n in server["libraries"]}

    desired_fields = {
        "server_name": server["name"], "server_type": server["type"], "server_url": server["url"],
        "api_key": server["api_key"], "external_url": server["external_url"],
    }
    form = {**desired_fields, "libraries": sorted(desired_libraries)}
    if server["allow_downloads"]:
        form["allow_downloads"] = "on"  # An unchecked checkbox is simply absent.

    server_id, current = find_server(sess, server["name"])
    if server_id is None:
        path = "/settings/servers/create"
        url, body = sess.post(path, form)
        if url.endswith(path):
            sys.exit(f"Wizarr: creating {server['name']} failed\n{body[:400]}")
        print(f"Wizarr: media server {server['name']} added")
        return

    changed = [k for k in ("server_url", "external_url", "api_key") if current[k] != desired_fields[k]]
    if current["libraries"] != desired_libraries:
        changed.append("libraries")
    if current["allow_downloads"] != server["allow_downloads"]:
        changed.append("allow_downloads")
    if not changed:
        return
    path = f"/settings/servers/{server_id}/edit"
    url, body = sess.post(path, form)
    if url.endswith(path):
        sys.exit(f"Wizarr: updating {server['name']} failed\n{body[:400]}")
    print(f"Wizarr: media server {server['name']}: {', '.join(changed)}")


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
    for server in SERVERS:
        if server["type"] == "audiobookshelf":
            ensure_abs_key(sess, server)
        ensure_media_server(sess, server)
    ensure_wizard_presets(sess)

    print("done")
    return 0


if __name__ == "__main__":
    sys.exit(main())
