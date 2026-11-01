// Netboot UI: live updates over server-sent events; the server renders every fragment.
const $ = (sel, root = document) => root.querySelector(sel);
const $$ = (sel, root = document) => [...root.querySelectorAll(sel)];

// --- Relative times -----------------------------------------------------------------

const rtf = new Intl.RelativeTimeFormat("en", { numeric: "auto", style: "short" });
function relative(iso) {
  const then = new Date(iso);
  if (Number.isNaN(then.getTime())) return "";
  const s = Math.round((then - Date.now()) / 1000);
  if (Math.abs(s) < 45) return "just now";
  const m = Math.round(s / 60);
  if (Math.abs(m) < 60) return rtf.format(m, "minute");
  const h = Math.round(m / 60);
  if (Math.abs(h) < 24) return rtf.format(h, "hour");
  return rtf.format(Math.round(h / 24), "day");
}
function refreshTimes(root = document) {
  for (const t of $$("time[data-ts]", root)) {
    t.textContent = relative(t.dataset.ts) || t.textContent;
    t.title = new Date(t.dataset.ts).toLocaleString();
  }
}
setInterval(refreshTimes, 30_000);

// --- Activity log ----------------------------------------------------------------

const log = $("#log");
const jump = $("#jump");
const MAX_LINES = 1000;
let pinned = true;

const atBottom = () => log.scrollHeight - log.scrollTop - log.clientHeight < 32;
const toBottom = () => { log.scrollTop = log.scrollHeight; };
log.addEventListener("scroll", () => { pinned = atBottom(); jump.hidden = pinned; });
jump.addEventListener("click", () => { pinned = true; jump.hidden = true; toBottom(); });

function appendLog(html) {
  const tpl = document.createElement("template");
  tpl.innerHTML = html;
  for (const li of tpl.content.children) li.classList.add("is-new");
  log.append(tpl.content);
  while (log.children.length > MAX_LINES) log.firstElementChild.remove();
  if (pinned) toBottom();
}

for (const btn of $$(".filter")) {
  btn.addEventListener("click", () => {
    for (const b of $$(".filter")) b.setAttribute("aria-pressed", String(b === btn));
    log.dataset.filter = btn.dataset.filter;
    if (pinned) toBottom();
  });
}

// --- Node list: keep expanded rows open across live swaps -------------------------

const expanded = new Set();
function setExpanded(btn, open) {
  btn.setAttribute("aria-expanded", String(open));
  document.getElementById(btn.getAttribute("aria-controls")).hidden = !open;
}
function restoreExpanded() {
  for (const node of $$(".node")) {
    const btn = $("[data-toggle]", node);
    if (btn && expanded.has(node.dataset.node)) setExpanded(btn, true);
  }
}

// --- Clipboard (navigator.clipboard needs a secure context such as localhost) -------

async function copyText(text) {
  if (navigator.clipboard && window.isSecureContext) return navigator.clipboard.writeText(text);
  const area = Object.assign(document.createElement("textarea"), { value: text });
  area.style.position = "fixed";
  area.style.opacity = "0";
  document.body.append(area);
  area.select();
  document.execCommand("copy");
  area.remove();
}

function flash(btn, label) {
  const tip = btn.dataset.tooltip;
  btn.dataset.tooltip = label;
  btn.classList.add("is-done");
  setTimeout(() => { btn.dataset.tooltip = tip; btn.classList.remove("is-done"); }, 1400);
}

// --- Actions -----------------------------------------------------------------------

const dialog = $("#forget-dialog");

document.addEventListener("click", async (event) => {
  const toggle = event.target.closest("[data-toggle]");
  if (toggle) {
    const open = toggle.getAttribute("aria-expanded") !== "true";
    setExpanded(toggle, open);
    const name = toggle.closest(".node").dataset.node;
    open ? expanded.add(name) : expanded.delete(name);
    return;
  }

  const copy = event.target.closest("[data-copy]");
  if (copy) {
    await copyText(copy.dataset.copy);
    flash(copy, "Copied");
    return;
  }

  const copyUrl = event.target.closest("[data-copy-url]");
  if (copyUrl) {
    const res = await fetch(copyUrl.dataset.copyUrl);
    if (!res.ok) return flash(copyUrl, "Couldn't read it: run ./build.sh");
    await copyText(await res.text());
    flash(copyUrl, "Copied to clipboard");
    return;
  }

  const forget = event.target.closest("[data-forget]");
  if (forget) {
    dialog.dataset.node = forget.dataset.forget;
    for (const el of $$("[data-name]", dialog)) el.textContent = forget.dataset.forget;
    dialog.showModal();
    return;
  }

  const confirm = event.target.closest("[data-confirm-forget]");
  if (confirm) {
    confirm.disabled = true;
    const res = await fetch(`/ui/nodes/${encodeURIComponent(dialog.dataset.node)}/forget`, { method: "POST" });
    confirm.disabled = false;
    if (res.ok) dialog.close();
    else confirm.textContent = "Failed. Try again";
  }
});

// --- Live updates --------------------------------------------------------------------

const conn = $("#conn");
function setConn(state, label) {
  conn.dataset.state = state;
  conn.textContent = label;
}

function swap(id, html) {
  $(id).innerHTML = html;
}

const events = new EventSource("/ui/events");
events.addEventListener("open", () => setConn("live", "Live"));
events.addEventListener("error", () => setConn("reconnecting", "Reconnecting"));
events.addEventListener("status", (e) => swap("#status", e.data));
events.addEventListener("summary", (e) => swap("#summary", e.data));
events.addEventListener("transfers", (e) => swap("#transfers", e.data));
events.addEventListener("nodes", (e) => {
  swap("#nodes", e.data);
  restoreExpanded();
  refreshTimes($("#nodes"));
});
events.addEventListener("log", (e) => appendLog(e.data));
events.addEventListener("log-reset", (e) => {
  log.innerHTML = e.data;
  toBottom();
});

refreshTimes();
toBottom();
