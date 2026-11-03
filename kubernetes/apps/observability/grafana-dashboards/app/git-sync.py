"""Register Grafana Git Sync for the homelab dashboards: GitHub App Connection + Repository.

Idempotent upserts through Grafana's provisioning API, then a pull and a wait until the repository
reports a healthy, finished sync. Exit non-zero (failing the Job, and the Flux Kustomization) if
the sync errors or never finishes.
"""

import base64
import json
import os
import sys
import time
import urllib.error
import urllib.request

GRAFANA = os.environ["GRAFANA_URL"].rstrip("/")
API = GRAFANA + "/apis/provisioning.grafana.app/v0alpha1/namespaces/default"
# Grafana's home page: the Lab Overview dashboard (dashboards/lab/lab-overview.json).
HOME_DASHBOARD_UID = "lab-overview"
AUTH = "Basic " + base64.b64encode(
    f"{os.environ['GRAFANA_USER']}:{os.environ['GRAFANA_PASSWORD']}".encode()
).decode()

CONNECTION = {
    "apiVersion": "provisioning.grafana.app/v0alpha1",
    "kind": "Connection",
    "metadata": {"name": "homelab-github"},
    "spec": {
        "title": "JacobCoffee/homelab (GitHub App)",
        "type": "github",
        "url": "https://github.com",
        "github": {
            "appID": os.environ["GITHUB_APP_ID"],
            "installationID": os.environ["GITHUB_APP_INSTALLATION_ID"],
        },
    },
    # Grafana wants the PEM itself base64-encoded ("privateKey must be base64 encoded").
    "secure": {"privateKey": {"create": base64.b64encode(os.environ["GITHUB_APP_PRIVATE_KEY"].encode()).decode()}},
}

REPOSITORY = {
    "apiVersion": "provisioning.grafana.app/v0alpha1",
    "kind": "Repository",
    "metadata": {"name": "homelab"},
    "spec": {
        "title": "Homelab",
        "type": "github",
        "github": {
            "url": "https://github.com/JacobCoffee/homelab",
            "branch": "main",
            "path": "kubernetes/apps/observability/grafana-dashboards/app/dashboards/",
        },
        "connection": {"name": "homelab-github"},
        # Saves from the UI go to a branch + PR, never straight to main.
        "workflows": ["branch"],
        "sync": {"enabled": True, "target": "folder", "intervalSeconds": 60},
    },
}


def call(method, path, body=None, base=API):
    req = urllib.request.Request(
        base + path,
        method=method,
        data=json.dumps(body).encode() if body is not None else None,
        headers={"Authorization": AUTH, "Content-Type": "application/json", "Accept": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return resp.status, json.loads(resp.read() or b"{}")
    except urllib.error.HTTPError as err:
        raw = err.read()
        try:
            return err.code, json.loads(raw)
        except ValueError:
            return err.code, {"message": raw.decode(errors="replace")}


def upsert(plural, obj):
    name = obj["metadata"]["name"]
    status, current = call("GET", f"/{plural}/{name}")
    if status == 404:
        status, result = call("POST", f"/{plural}", obj)
        verb = "created"
    elif status == 200:
        obj["metadata"]["resourceVersion"] = current["metadata"]["resourceVersion"]
        status, result = call("PUT", f"/{plural}/{name}", obj)
        verb = "updated"
    else:
        sys.exit(f"GET {plural}/{name}: {status} {current.get('message', current)}")
    if status >= 300:
        sys.exit(f"{plural}/{name}: {status} {result.get('message', result)}")
    print(f"{plural}/{name} {verb}", flush=True)


def main():
    upsert("connections", CONNECTION)
    upsert("repositories", REPOSITORY)

    status, job = call("POST", "/repositories/homelab/jobs", {"action": "pull", "pull": {"incremental": False}})
    print(f"pull job: {status} {job.get('metadata', {}).get('name', job.get('message', ''))}", flush=True)

    deadline = time.time() + 300
    while time.time() < deadline:
        time.sleep(10)
        _, repo = call("GET", "/repositories/homelab")
        st = repo.get("status", {})
        health, sync = st.get("health", {}), st.get("sync", {})
        state = sync.get("state")
        print(f"healthy={health.get('healthy')} sync={state} {sync.get('message') or health.get('message') or ''}",
              flush=True)
        # "warning" means some files were skipped (unparseable, UID owned by another provisioner):
        # those dashboards are missing, so fail rather than report a partial sync as done.
        if state in ("error", "warning") or health.get("healthy") is False:
            sys.exit(f"sync {state or 'unhealthy'}: {json.dumps(st)}")
        if health.get("healthy") and state == "success":
            print("stats:", json.dumps(st.get("stats", [])), flush=True)
            set_home_dashboard()
            return
    sys.exit("sync did not finish within 5 minutes")


def set_home_dashboard():
    status, prefs = call("GET", "/api/org/preferences", base=GRAFANA)
    if status == 200 and prefs.get("homeDashboardUID") == HOME_DASHBOARD_UID:
        print(f"home dashboard already {HOME_DASHBOARD_UID}", flush=True)
        return
    status, result = call("PATCH", "/api/org/preferences", {"homeDashboardUID": HOME_DASHBOARD_UID}, base=GRAFANA)
    if status >= 300:
        sys.exit(f"home dashboard: {status} {result.get('message', result)}")
    print(f"home dashboard set to {HOME_DASHBOARD_UID}", flush=True)


if __name__ == "__main__":
    main()
