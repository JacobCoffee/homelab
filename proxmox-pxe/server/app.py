#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.12"
# dependencies = [
#   "litestar>=2.25,<3",
#   "jinja2>=3.1",
#   "granian>=2.8,<3",
# ]
# ///
"""Netboot server + web UI for unattended Proxmox VE installs.

Installer endpoints (open to the LAN; the PCs being installed call these):
  GET  /boot.ipxe?mac=..     iPXE entry: install, or `exit` to local disk if already installed
  GET  /pve/<file>           installer kernel/initrd/ISO produced by build.sh
  POST /answer               auto-installer answer file (assigns hostname by MAC)
  POST /installed            post-installation webhook (records IP for DHCP reservation)
  GET  /first-boot           first-boot script run on each new node
  POST /first-boot-report    result report from the first-boot script
  GET  /status               JSON node state

Web UI (this Mac only): /  with live nodes, downloads and dnsmasq/installer activity.
API reference (this Mac only): /api  (Scalar; schema at /api/openapi.json).
Also starts dnsmasq (proxyDHCP + TFTP) from out/dnsmasq.conf and stops it on exit.
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import itertools
import json
import os
import re
import signal
import subprocess
import sys
import time
from collections import deque
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlsplit

from jinja2 import Environment, FileSystemLoader, select_autoescape
from litestar import Litestar, Request, Response, Router, get, post
from litestar.connection import ASGIConnection
from litestar.datastructures import CacheControlHeader
from litestar.exceptions import NotFoundException, PermissionDeniedException
from litestar.handlers import BaseRouteHandler
from litestar.openapi import OpenAPIConfig
from litestar.openapi.plugins import ScalarRenderPlugin
from litestar.openapi.spec import Server
from litestar.response import ServerSentEvent, ServerSentEventMessage, Stream
from litestar.static_files import create_static_files_router

sys.path.insert(0, str(Path(__file__).resolve().parent))
import core  # noqa: E402

SERVER_DIR = Path(__file__).resolve().parent
DNSMASQ_CONF = core.ROOT / "out" / "dnsmasq.conf"
DNSMASQ_PID = core.ROOT / "out" / "dnsmasq.pid"
CHUNK = 1 << 20

# Defaults; main() overrides STATE/RUN_DNSMASQ from its flags. PXE_* variables let tests
# importing this module point it at a scratch state file or a fake dnsmasq.
CFG = core.load_cfg()
STATE = core.State(Path(os.environ.get("PXE_STATE", core.DEFAULT_STATE)), CFG["HOST_PREFIX"])
DNSMASQ = os.environ.get("PXE_DNSMASQ", "/opt/homebrew/opt/dnsmasq/sbin/dnsmasq")
SUDO = os.environ.get("PXE_SUDO", "sudo -n").split()
RUN_DNSMASQ = os.environ.get("PXE_NO_DNSMASQ") != "1"

TEMPLATES = Environment(
    loader=FileSystemLoader(SERVER_DIR / "templates"),
    autoescape=select_autoescape(["html"]),
    trim_blocks=True,
    lstrip_blocks=True,
)


# --- Live event hub -------------------------------------------------------------


@dataclass
class Transfer:
    id: int
    client: str
    file: str
    total: int
    sent: int = 0
    started: float = field(default_factory=time.monotonic)
    finished: float | None = None
    ok: bool = False
    samples: deque = field(default_factory=lambda: deque(maxlen=12))  # (time, sent), ~6 s at 2 Hz

    @property
    def pct(self) -> float:
        return 100.0 * self.sent / self.total if self.total else 100.0

    @property
    def rate(self) -> float:
        """Bytes/s: recent window while running (the first chunks only fill socket buffers), average once done."""
        if self.finished:
            elapsed = self.finished - self.started
            return self.sent / elapsed if elapsed > 0 else 0.0
        if len(self.samples) < 2:
            return 0.0
        (t0, s0), (t1, s1) = self.samples[0], self.samples[-1]
        return (s1 - s0) / (t1 - t0) if t1 > t0 else 0.0

    @property
    def eta(self) -> float | None:
        return (self.total - self.sent) / self.rate if self.rate > 0 and not self.finished else None


class Hub:
    def __init__(self):
        self.log: deque[dict] = deque(maxlen=1000)
        self.subscribers: set[asyncio.Queue] = set()
        self.transfers: dict[int, Transfer] = {}
        self.transfers_dirty = False
        self.ids = itertools.count(1)
        self.dnsmasq = {"state": "off", "detail": "not started"}
        self.dnsmasq_pid: int | None = None  # only stop the dnsmasq this process started
        self.closing = False

    def close(self):
        """Shutdown: end live UI streams and in-flight downloads so the server can stop."""
        self.closing = True
        for queue in list(self.subscribers):
            with contextlib.suppress(asyncio.QueueFull):
                queue.put_nowait(None)

    def emit(self, event: str, data: str):
        for queue in list(self.subscribers):
            try:
                queue.put_nowait((event, data))
            except asyncio.QueueFull:  # a stalled browser tab; it resyncs on reconnect
                self.subscribers.discard(queue)

    def add_log(self, source: str, text: str, level: str = "info"):
        entry = {
            "id": next(self.ids),
            "ts": time.strftime("%H:%M:%S"),
            "source": source,
            "level": level,
            "text": text,
        }
        self.log.append(entry)
        print(f"[{entry['ts']}] {source:<8} {text}", flush=True)
        self.emit("log", render("_log.html", entries=[entry]))

    def nodes_changed(self):
        self.emit("nodes", render("_nodes.html", **nodes_context()))
        self.emit("summary", render("_summary.html", **nodes_context()))

    def set_dnsmasq(self, state: str, detail: str):
        self.dnsmasq = {"state": state, "detail": detail}
        self.emit("status", render("_status.html", **status_context()))

    def start_transfer(self, client: str, file: str, total: int) -> Transfer:
        t = Transfer(next(self.ids), client, file, total)
        self.transfers[t.id] = t
        self.transfers_dirty = True
        self.add_log("http", f"{client} downloading {file} ({human_bytes(total)})")
        return t

    def finish_transfer(self, t: Transfer, ok: bool):
        t.finished, t.ok = time.monotonic(), ok
        self.transfers_dirty = True
        if ok:
            self.add_log("http", f"{t.client} finished {t.file} at {human_bytes(t.rate)}/s")
        else:
            self.add_log("http", f"{t.client} dropped {t.file} at {t.pct:.0f}%", "warn")

    async def tick_transfers(self):
        """Push download progress at most twice a second; drop finished rows after a minute."""
        while True:
            await asyncio.sleep(0.5)
            for tid, t in list(self.transfers.items()):
                if t.finished and time.monotonic() - t.finished > 60:
                    del self.transfers[tid]
                    self.transfers_dirty = True
            for t in self.transfers.values():
                if not t.finished:
                    t.samples.append((time.monotonic(), t.sent))
                    self.transfers_dirty = True
            if self.transfers_dirty:
                self.transfers_dirty = False
                self.emit("transfers", render("_transfers.html", transfers=self.transfer_list()))

    def transfer_list(self) -> list[Transfer]:
        return sorted(self.transfers.values(), key=lambda t: (t.finished is not None, -t.started))


HUB = Hub()


def human_bytes(n: float) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} GB"


def human_seconds(s: float | None) -> str:
    if s is None:
        return ""
    s = int(s)
    return f"{s // 60}m {s % 60:02d}s" if s >= 60 else f"{s}s"


TEMPLATES.filters.update(bytes=human_bytes, seconds=human_seconds)


def render(name: str, **context) -> str:
    return TEMPLATES.get_template(name).render(**context)


STATUS_ORDER = {"installing": 0, "installed": 1, "ready": 2}


def nodes_context() -> dict:
    hosts = STATE.snapshot()
    nodes = []
    for name, host in hosts.items():
        system = host.get("dmi", {}).get("system", {})
        vendor = system.get("manufacturer")
        model = system.get("name") or ""
        # HP-style DMI already starts the name with the vendor ("HP" + "HP EliteDesk ...").
        if vendor and vendor not in ("System manufacturer", "To Be Filled By O.E.M.") and not model.startswith(vendor):
            model_full = f"{vendor} {model}".strip()
        else:
            model_full = model
        nodes.append(
            {
                "name": name,
                "fqdn": f"{name}.{CFG['DOMAIN']}",
                "status": host.get("status", "installing"),
                "ip": host.get("ip"),
                "mac": host.get("mgmt_mac") or (host["macs"][0] if host.get("macs") else None),
                "macs": host.get("macs", []),
                "model": model or None,
                "model_full": model_full or None,
                "boot_disk": host.get("boot_disk"),
                "first_boot": host.get("first_boot", []),
                "updated": host.get("updated", "").replace(" ", "T"),
            }
        )
    nodes.sort(key=lambda n: n["name"])
    counts = {s: sum(1 for n in nodes if n["status"] == s) for s in STATUS_ORDER}
    n = 1
    while f"{STATE.prefix}{n:02d}" in hosts:
        n += 1
    return {"nodes": nodes, "counts": counts, "next_name": f"{STATE.prefix}{n:02d}"}


def status_context() -> dict:
    return {"dnsmasq": HUB.dnsmasq, "base": core.base_url(CFG), "iface": CFG.get("LAN_IFACE", "")}


# --- dnsmasq supervisor ---------------------------------------------------------

DNSMASQ_PREFIX = re.compile(r"^dnsmasq(?:-(\w+))?\[\d+\]:\s*")


async def supervise_dnsmasq():
    if not DNSMASQ_CONF.exists():
        HUB.set_dnsmasq("off", "out/dnsmasq.conf missing; start with ./run.sh")
        return
    DNSMASQ_PID.unlink(missing_ok=True)
    cmd = [*SUDO, DNSMASQ, "--keep-in-foreground", "--log-facility=-", f"--pid-file={DNSMASQ_PID}", "-C", str(DNSMASQ_CONF)]
    proc = await asyncio.create_subprocess_exec(
        *cmd, stdin=asyncio.subprocess.DEVNULL, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT
    )
    pid_task = asyncio.create_task(read_pid_when_ready())
    HUB.set_dnsmasq("starting", "waiting for dnsmasq")
    assert proc.stdout
    async for raw in proc.stdout:
        line = raw.decode(errors="replace").rstrip()
        if not line:
            continue
        m = DNSMASQ_PREFIX.match(line)
        text = line[m.end():] if m else line
        if m and HUB.dnsmasq["state"] != "running" and text.startswith("started"):
            HUB.set_dnsmasq("running", f"proxyDHCP + TFTP on {CFG.get('LAN_IFACE', '?')}")
        level = "error" if re.search(r"fail|error|cannot|denied|password is required", text, re.I) else "info"
        HUB.add_log("dnsmasq", text, level)
    rc = await proc.wait()
    HUB.set_dnsmasq("error" if rc else "off", f"exited with code {rc}")


async def read_pid_when_ready():
    """Remember our dnsmasq's PID; out/dnsmasq.pid may later belong to another instance."""
    for _ in range(100):
        with contextlib.suppress(FileNotFoundError, ValueError):
            HUB.dnsmasq_pid = int(DNSMASQ_PID.read_text().strip())
            return
        await asyncio.sleep(0.1)


def stop_dnsmasq():
    # dnsmasq runs as this user after binding, so no sudo is needed to stop it.
    if HUB.dnsmasq_pid is None:
        return
    with contextlib.suppress(ProcessLookupError, PermissionError):
        os.kill(HUB.dnsmasq_pid, signal.SIGTERM)


@contextlib.asynccontextmanager
async def lifespan(app: Litestar) -> AsyncIterator[None]:
    tasks = [asyncio.create_task(HUB.tick_transfers())]
    if RUN_DNSMASQ:
        tasks.append(asyncio.create_task(supervise_dnsmasq()))
    else:
        HUB.set_dnsmasq("off", "disabled (--no-dnsmasq)")
    HUB.add_log("server", f"serving {core.base_url(CFG)}, state {STATE.path}")
    try:
        yield
    finally:
        stop_dnsmasq()
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)


# --- Installer endpoints ----------------------------------------------------------


def client_of(request: Request) -> str:
    return request.client.host if request.client else "?"


def text(body: str, media_type: str = "text/plain; charset=utf-8") -> Response[str]:
    return Response(body, media_type=media_type)


INSTALLER = ["Installer"]
DASHBOARD = ["Dashboard"]


@get(
    "/boot.ipxe",
    sync_to_thread=False,
    tags=INSTALLER,
    summary="iPXE entry script",
    description="Chains to the installer for new MACs; returns `exit` (boot the USB stick) for installed nodes.",
)
def boot_ipxe(request: Request, mac: str = "") -> Response[str]:
    body, line = core.boot_ipxe(STATE, mac)
    HUB.add_log("install", f"{client_of(request)}: {line}")
    return text(body)


@get(
    "/pve/{path:path}",
    tags=INSTALLER,
    summary="Installer images",
    description="Kernel, initrd and ISO built by `build.sh`. Large files stream with a progress row in the dashboard.",
)
async def pve_file(request: Request, path: str) -> Stream:
    root = (core.HTTP_DIR / "pve").resolve()
    file = (root / path.lstrip("/")).resolve()
    if not file.is_relative_to(root) or not file.is_file():
        raise NotFoundException()
    size = file.stat().st_size
    if size < CHUNK:  # boot.ipxe and friends: not worth a progress row
        HUB.add_log("http", f"{client_of(request)} fetched {file.name}")
        return Stream(iter([file.read_bytes()]), media_type="application/octet-stream", headers={"Content-Length": str(size)})
    transfer = HUB.start_transfer(client_of(request), file.name, size)

    async def body() -> AsyncIterator[bytes]:
        ok = False
        try:
            with file.open("rb") as fh:
                while not HUB.closing and (chunk := await asyncio.to_thread(fh.read, CHUNK)):
                    yield chunk
                    transfer.sent += len(chunk)
            ok = transfer.sent == size
        finally:
            HUB.finish_transfer(transfer, ok)

    return Stream(body(), media_type="application/octet-stream", headers={"Content-Length": str(size)})


async def handle(request: Request, source: str, fn, *args) -> Response[str]:
    try:
        body, line = fn(*args)
    except Exception as exc:  # report to the client, keep serving others
        HUB.add_log(source, f"{request.url.path} from {client_of(request)} failed: {exc!r}", "error")
        return Response(f"error: {exc}\n", status_code=500, media_type="text/plain")
    HUB.add_log(source, line)
    HUB.nodes_changed()
    return text(body, "application/toml" if request.url.path == "/answer" else "text/plain; charset=utf-8")


@post(
    "/answer",
    status_code=200,
    tags=INSTALLER,
    summary="Answer file",
    description="The Proxmox auto-installer POSTs its system info; the response is `answer.toml`. Assigns the next hostname by MAC.",
)
async def answer(request: Request) -> Response[str]:
    info = json.loads(await request.body())
    return await handle(request, "install", core.answer, STATE, CFG, info)


@post(
    "/installed",
    status_code=200,
    tags=INSTALLER,
    summary="Post-install webhook",
    description="Marks the node installed and records its management IP and MAC for the DHCP reservation.",
)
async def installed(request: Request) -> Response[str]:
    info = json.loads(await request.body())
    return await handle(request, "install", core.installed, STATE, info)


@get("/first-boot", sync_to_thread=False, tags=INSTALLER, summary="First-boot script")
def first_boot(request: Request) -> Response[str]:
    HUB.add_log("install", f"{client_of(request)} fetched the first-boot script")
    return text(core.render_first_boot(CFG))


@post(
    "/first-boot-report",
    status_code=200,
    tags=INSTALLER,
    summary="First-boot report",
    description="Result lines from the first-boot script; marks the node ready.",
)
async def first_boot_report(request: Request, host: str = "") -> Response[str]:
    report = (await request.body()).decode(errors="replace")
    return await handle(request, "install", core.first_boot_report, STATE, host, report)


@get("/status", sync_to_thread=False, tags=INSTALLER, summary="Node state", description="Every node as JSON.")
def status() -> dict:
    return STATE.snapshot()


# --- Web UI (this Mac only) -----------------------------------------------------

LOCAL = {"127.0.0.1", "::1", CFG["SERVER_IP"]}


def local_only(connection: ASGIConnection, _: BaseRouteHandler) -> None:
    if not connection.client or connection.client.host not in LOCAL:
        raise PermissionDeniedException("The netboot UI is only served to this Mac.")


def html(body: str) -> Response[str]:
    return Response(body, media_type="text/html; charset=utf-8")


def same_origin(connection: ASGIConnection, _: BaseRouteHandler) -> None:
    """Dashboard writes: refuse requests a web page on another site makes through this Mac's browser."""
    origin = connection.headers.get("origin")
    cross = connection.headers.get("sec-fetch-site") == "cross-site"
    if cross or (origin and urlsplit(origin).netloc != connection.headers.get("host")):
        raise PermissionDeniedException("Cross-site request refused.")


@get("/", guards=[local_only], sync_to_thread=False, include_in_schema=False)
def index() -> Response[str]:
    return html(
        render(
            "index.html",
            **nodes_context(),
            **status_context(),
            transfers=HUB.transfer_list(),
            entries=list(HUB.log),
            domain=CFG["DOMAIN"],
            has_password=core.ROOT_PASSWORD.exists(),
        )
    )


@get("/ui/events", guards=[local_only], include_in_schema=False)
async def events() -> ServerSentEvent:
    queue: asyncio.Queue = asyncio.Queue(maxsize=500)
    HUB.subscribers.add(queue)

    async def stream() -> AsyncIterator[ServerSentEventMessage]:
        try:
            # Resync everything on (re)connect; the page may have missed events.
            yield ServerSentEventMessage(event="status", data=render("_status.html", **status_context()))
            yield ServerSentEventMessage(event="nodes", data=render("_nodes.html", **nodes_context()))
            yield ServerSentEventMessage(event="summary", data=render("_summary.html", **nodes_context()))
            yield ServerSentEventMessage(event="transfers", data=render("_transfers.html", transfers=HUB.transfer_list()))
            yield ServerSentEventMessage(event="log-reset", data=render("_log.html", entries=list(HUB.log)))
            while not HUB.closing:
                try:
                    item = await asyncio.wait_for(queue.get(), timeout=15)
                except TimeoutError:
                    yield ServerSentEventMessage(comment="keepalive")
                    continue
                if item is None:  # server shutting down
                    return
                event, data = item
                yield ServerSentEventMessage(event=event, data=data)
        finally:
            HUB.subscribers.discard(queue)

    return ServerSentEvent(stream())


@post(
    "/ui/nodes/{name:str}/forget",
    guards=[local_only, same_origin],
    status_code=200,
    sync_to_thread=False,
    tags=DASHBOARD,
    summary="Reinstall a node",
    description="Forgets the node so its next network boot reinstalls it (wipes its USB stick).",
)
def forget(name: str) -> Response[str]:
    ip = STATE.snapshot().get(name, {}).get("ip")
    if not STATE.forget(name):
        raise NotFoundException(f"unknown node {name}")
    if ip and core.KNOWN_HOSTS.exists():  # the reinstall brings a new host key
        subprocess.run(["ssh-keygen", "-R", ip, "-f", str(core.KNOWN_HOSTS)], capture_output=True, check=False)
    HUB.add_log("server", f"forgot {name}; its next network boot reinstalls it", "warn")
    HUB.nodes_changed()
    return text("ok\n")


@get("/ui/root-password", guards=[local_only], sync_to_thread=False, tags=DASHBOARD, summary="Root password")
def root_password() -> Response[str]:
    if not core.ROOT_PASSWORD.exists():
        raise NotFoundException("secrets/root-password missing; run ./build.sh")
    return text(core.ROOT_PASSWORD.read_text().strip())


@dataclass
class PasswordChange:
    password: str
    apply: bool = True


async def push_password(ip: str, hashed: str) -> str | None:
    """Set root's password hash on an installed node over SSH; returns an error or None."""
    core.KNOWN_HOSTS.parent.mkdir(parents=True, exist_ok=True)
    proc = await asyncio.create_subprocess_exec(
        "ssh", "-i", str(core.AUTOMATION_KEY),
        "-o", "IdentityAgent=none", "-o", "IdentitiesOnly=yes", "-o", "BatchMode=yes",
        "-o", "ConnectTimeout=5", "-o", f"UserKnownHostsFile={core.KNOWN_HOSTS}",
        "-o", "StrictHostKeyChecking=accept-new",
        f"root@{ip}", "chpasswd -e",
        stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
    )  # fmt: skip
    try:
        _, err = await asyncio.wait_for(proc.communicate(f"root:{hashed}\n".encode()), 20)
    except TimeoutError:
        proc.kill()
        return "timed out"
    if proc.returncode:
        lines = err.decode(errors="replace").strip().splitlines()
        return lines[-1] if lines else f"ssh exited {proc.returncode}"
    return None


@post(
    "/ui/root-password",
    guards=[local_only, same_origin],
    status_code=200,
    tags=DASHBOARD,
    summary="Change the root password",
    description="Stores it for future installs and, with `apply`, sets it on every installed node over SSH "
    "(automation key; only the SHA-512 crypt hash is sent).",
)
async def set_root_password(data: PasswordChange) -> Response[dict]:
    if problem := core.check_password(data.password):
        return Response({"error": problem}, status_code=400)
    hashed = await asyncio.to_thread(core.set_root_password, data.password)
    HUB.add_log("server", "root password changed; new installs use it")
    targets = [(n, h["ip"]) for n, h in sorted(STATE.snapshot().items()) if h.get("status") in core.INSTALLED and h.get("ip")]
    results = []
    if data.apply and targets:
        errors = await asyncio.gather(*(push_password(ip, hashed) for _, ip in targets))
        for (name, ip), error in zip(targets, errors):
            results.append({"name": name, "ip": ip, "ok": error is None, "error": error})
            if error:
                HUB.add_log("server", f"root password not set on {name} ({ip}): {error}", "warn")
            else:
                HUB.add_log("server", f"root password set on {name} ({ip})")
    return Response({"nodes": results})


app = Litestar(
    route_handlers=[
        boot_ipxe,
        pve_file,
        answer,
        installed,
        first_boot,
        first_boot_report,
        status,
        index,
        events,
        forget,
        root_password,
        set_root_password,
        create_static_files_router(  # no-cache: revalidate via ETag, so updates never run stale JS/CSS
            path="/static",
            directories=[SERVER_DIR / "static"],
            guards=[local_only],
            include_in_schema=False,
            cache_control=CacheControlHeader(no_cache=True),
        ),
    ],
    openapi_config=OpenAPIConfig(
        title="Proxmox netboot",
        version="1",
        description=(
            "Unattended Proxmox VE installs over the network. **Installer** endpoints are called by "
            "iPXE, the Proxmox auto-installer and each new node, and are open to the LAN. "
            "**Dashboard** endpoints and this page are served to this Mac only."
        ),
        servers=[Server(url=core.base_url(CFG))],
        render_plugins=[
            ScalarRenderPlugin(
                path="/",
                options={"showDeveloperTools": "never", "agent": {"disabled": True}, "hideClientButton": True},
            )
        ],
        openapi_router=Router(path="/api", guards=[local_only], include_in_schema=False, route_handlers=[]),
    ),
    lifespan=[lifespan],
)


def main():
    global STATE, RUN_DNSMASQ
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--state", type=Path, default=core.DEFAULT_STATE, help="node state file")
    parser.add_argument("--port", type=int, default=int(CFG["HTTP_PORT"]), help="HTTP port (iPXE has the build-time port baked in)")
    parser.add_argument("--no-dnsmasq", action="store_true", help="serve HTTP only")
    args = parser.parse_args()
    STATE = core.State(args.state.resolve(), CFG["HOST_PREFIX"])
    RUN_DNSMASQ = not args.no_dnsmasq
    print(f"Netboot UI: http://localhost:{args.port}/  (installer endpoints on {core.base_url(CFG)})", flush=True)
    asyncio.run(serve(args.port))


async def serve(port: int):
    # Embedded Granian: one process, and this loop owns Ctrl-C, so open UI streams and
    # installer downloads can be ended instead of holding the shutdown open.
    from granian.constants import Interfaces
    from granian.server.embed import Server

    server = Server(app, address="0.0.0.0", port=port, interface=Interfaces.ASGI, log_access=False)
    loop = asyncio.get_running_loop()
    first_signal: float | None = None

    def hard_exit():
        stop_dnsmasq()
        os._exit(130)

    def on_signal():
        nonlocal first_signal
        if first_signal is not None:
            if time.monotonic() - first_signal > 1:  # a deliberate second Ctrl-C
                hard_exit()
            return
        first_signal = time.monotonic()
        print("\nStopping (Ctrl-C again to force)...", flush=True)
        HUB.close()
        server.stop()
        loop.call_later(3, hard_exit)

    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, on_signal)
    await server.serve()


if __name__ == "__main__":
    main()
