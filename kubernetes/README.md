# Kubernetes (Talos + Flux)

Talos nodes (`k8s_nodes` in `../ansible/inventory/group_vars/all/main.yml`: control planes
`hnk8s01`–`03` at 10.10.30.11–13, workers from `hnk8s04` at .14 on; API VIP 10.10.30.10) built by
[`../ansible`](../ansible/README.md#kubernetes-talos). Once `flux bootstrap` has run, the cluster
follows `main`: push a change here and Flux applies it (every hour, or right away with
`flux reconcile ks flux-system --with-source`).

From the repo root (`mise.toml` sets `KUBECONFIG`, `TALOSCONFIG` and the SOPS key path; the Mac
reaches 10.10.30.0/24 through the OPNsense pair's Tailscale subnet routes):

```sh
kubectl get nodes
talosctl -n 10.10.30.11 dashboard
flux get kustomizations
```

## Layout

| Path | Purpose |
|---|---|
| `talos/patches/cluster.yaml` | machine config shared by every node (no flannel/kube-proxy, control planes run workloads, Spegel containerd setting); per-node parts come from the Ansible template |
| `talos/schematic.yaml` | Image Factory extensions (QEMU guest agent) |
| `talos/secrets.sops.yaml` | cluster CA and keys, fully encrypted |
| `flux/cluster/flux-system/` | written by `flux bootstrap`; don't edit |
| `flux/cluster/apps.yaml` | `cluster-apps`: applies `apps/` with SOPS decryption and `${VAR}` substitution |
| `flux/cluster/cluster-settings.yaml` | non-secret variables (`CLOUDFLARE_TUNNEL_ID`, `ACME_EMAIL`, `R2_ENDPOINT`, `R2_BUCKET`, ...) |
| `apps/backup/cluster-secrets.sops.yaml` | secret counterpart to `cluster-settings.yaml`, same `substituteFrom` mechanism (R2 keys, shared VolSync restic password) -- lives under `apps/` (not `flux/cluster/`) so `cluster-apps`'s SOPS decryption applies to it |
| `components/volsync/` | reusable kustomize Component: restic-to-R2 `ReplicationSource` for an app's config PVC, see "Backups and restore" |
| `apps/<namespace>/<app>/ks.yaml` | one Flux Kustomization per app, with `dependsOn` ordering |
| `apps/<namespace>/<app>/app/` | the app's manifests / HelmRelease |

## What runs

| App | Role |
|---|---|
| Cilium | CNI, kube-proxy replacement, LoadBalancer IPs 10.10.30.200–250 announced over ARP |
| Envoy Gateway | Gateway API: `internal` at 10.10.30.200, `external` at 10.10.30.201 (behind the tunnel) |
| cert-manager | wildcard `*.z7x.org` from Let's Encrypt via Cloudflare DNS-01 (`z7x-org-tls`) |
| external-dns (AdGuard) | HTTPRoutes on `internal` become AdGuard rewrites, so lab and tailnet clients resolve them |
| external-dns (Cloudflare) | HTTPRoutes on `external` become proxied CNAMEs to the tunnel |
| cloudflared | tunnel `homelab`: Cloudflare → `external` gateway, no open ports at home |
| adguard-dot | `apps/network/adguard-dot`: cert-manager cert for `dns.z7x.org`/`*.dns.z7x.org` (AdGuard's own DoT/HTTPS TLS, separate from `z7x-org-tls`) plus a CronJob pushing it into both AdGuard instances -- see `fire-tv/DECISIONS.md` "DNS" |
| theme-catalog | `apps/network/theme-catalog`: public static site at `themes.z7x.org`, built from a pinned commit of the public `github.com/JacobCoffee/hearth` mirror |
| uptime-kuma | `apps/status/uptime-kuma` (namespace `status`) at `status.z7x.org`: public status page on both gateways, admin UI internal-only behind Pocket ID (`kuma.z7x.org` redirects there) -- see `fire-tv/DECISIONS.md` "Homelab services" |
| homepage | `apps/status/homepage`: start page at `start.z7x.org` (internal only): every app with an up/down dot, pod CPU/memory, Proxmox VM/LXC stats, the apps' own widgets, and news feeds. Edit `app/config/services.yaml`; changes reach the pod within a minute, no restart. Credentials are `app/secret.sops.yaml`, written by `ansible-playbook playbooks/site.yml --tags homepage` (`../ansible/roles/homepage`); after it changes, `kubectl -n status rollout restart deploy/homepage` |
| flux-web | `apps/flux-system/flux-web`: Flux web UI at `flux.z7x.org` (internal only), the Flux Operator chart's web server alone (`web.serverOnly`, operator CRDs installed, no operator). Signs in with Pocket ID itself and acts as you in Kubernetes: `lab-admins` is bound to `flux-web-admin` (read everything; reconcile/suspend/resume, restart workloads, delete pods). Upstream only supports the UI on Flux-Operator-managed clusters; this one is `flux bootstrap`. Capacitor was the first pick, but its in-cluster server won't start without a Gimlet beta license and the old free one doesn't work on Flux 2.8+ |
| airvpn-exit | `apps/vpn/airvpn-exit` (namespace `vpn`): Tailscale exit node `airvpn` (`tag:vpn-exit`). Pick it in the Tailscale menu and your internet traffic leaves through AirVPN (US). One pod: gluetun owns the network namespace and its kill switch, Tailscale (userspace, OAuth client `vault_vpn_exit_tailscale_oauth_client_*`) runs behind it, DNS is gluetun's DoT resolver. AirVPN device `vpn` (`vault_vpn_exit_wireguard_*`), separate from the torrent tunnel's key: one AirVPN key can only be connected once, so a second region running at the same time needs a second device |
| firetv-reapply | `apps/firetv/reapply` (namespace `firetv`): CronJob re-running the Fire TV debloat/tweaks after a reboot -- see `fire-tv/DECISIONS.md` |
| metrics-server, kubelet-csr-approver | `kubectl top`; approves kubelet serving certificates |
| Spegel | nodes share pulled images with each other |
| echo | smoke test at `echo.z7x.org` on the internal gateway |
| home-assistant | `home.z7x.org` on both gateways, backed by the HA VM (selector-less Service + EndpointSlice to 10.10.10.20:80) |
| media stack | Jellyfin (`media.z7x.org`), Seerr (`requests.z7x.org`), the *arrs, qBittorrent+gluetun, SABnzbd -- see "Media stack" below |
| Proxmox CSI plugin | `csi-proxmox`: provisions the `proxmox-vmdata` (default) and `proxmox-fast` StorageClasses -- see "Storage" below |
| VolSync, etcd-backup, opnsense-backup | `apps/backup/`: restic-to-R2 offsite backups -- see "Backups and restore" below |

## Publishing an app

Add an HTTPRoute with a `z7x.org` hostname and pick the gateway:

- `parentRefs: [{name: internal, namespace: network, sectionName: https}]` for lab/tailnet only.
- `parentRefs: [{name: external, namespace: network, sectionName: https}]` for the internet,
  through Cloudflare.
- Both (two routes, same hostname, like `home-assistant`): public through Cloudflare, while
  tailnet devices resolve the name through AdGuard (Tailscale split DNS for `z7x.org`) and go
  straight to the internal gateway.

DNS records and the certificate are handled for you.

Always set `hostnames`. A route without one inherits the listener's `*.z7x.org`, and
external-dns would publish that wildcard. In AdGuard that breaks the whole cluster: lab DHCP
hands out search domains such as `k8s.int.z7x.org`, so `github.com` is first tried as
`github.com.k8s.int.z7x.org`, matches the wildcard, and resolves to the gateway. Routes that
really have no hostname (the HTTP→HTTPS redirects) carry `dns.z7x.org/publish: "false"`,
which both external-dns instances skip.

## Secrets

Put them in `*.sops.yaml` files next to the app and list them in its `kustomization.yaml`;
`.sops.yaml` at the repo root encrypts only `data`/`stringData`:

```sh
sops -e -i apps/<ns>/<app>/app/secret.sops.yaml
```

Flux decrypts with the `flux-system/sops-age` secret (the same age key as
`~/.config/sops/age/keys.txt` and `vault_sops_age_key` in the Ansible vault). The Cloudflare
token in `cert-manager-issuers` and `external-dns-cloudflare` is the vault's
`cloudflare_api_token`; if you rotate it, update both files.

## Logins

You sign in with a passkey through Pocket ID (`https://id.z7x.org`, `apps/auth/pocket-id`):

- Grafana, Proxmox (realm `pocket-id`, Ansible `pve_oidc`), Jellyfin in the browser and the Flux
  web UI have their own "Sign in with Pocket ID".
- The rest of the internal apps have no SSO of their own, so Envoy's OIDC filter sits in front of
  them (`apps/network/lab-sso`) and their own logins are off: the *arrs, Bazarr, SABnzbd, Bindery,
  Cleanuparr, qBittorrent, Unpackerr, Uptime Kuma's admin UI (`status.z7x.org`) and the start page. Their pods
  only take traffic from the gateway, their own namespace, `status` and `observability`
  (CiliumNetworkPolicy `lab-sso-ingress`), so going to a pod directly can't skip the passkey.
  In-cluster callers keep using API keys; through the gateway every request needs the passkey
  session, so phone apps that call `sonarr.z7x.org/api` directly won't get through.
- Only Pocket ID's `lab-admins` group gets in. `../scripts/pocket-id.py` owns that group, the
  owner account, the OIDC clients and their secrets (`vault_pocket_id_<client>_secret`, copied
  into the sops Secrets that need them; commit what it lists). `--link` prints a one-time
  sign-in link (30 minutes) for adding a passkey on a new device or after losing one.
- The sign-in pages carry the Homelab theme, in two parts. `apps/auth/pocket-id/branding/` holds
  the images: the animated orbit of the lab's apps (the right-hand panel), the rack logo, the
  favicon and a logo per OIDC client. `branding/build.py` builds them from `branding/icons/`, and
  `scripts/pocket-id.py` uploads any that changed. The CSS is `app/theme/lab.css` plus its fonts;
  Caddy serves it at `id.z7x.org/_lab/`, and Envoy's Lua filter (`app/envoyextensionpolicy.yaml`)
  links it into every Pocket ID page. The CSS targets Pocket ID's own class names, so open
  `/login` after each Pocket ID bump. A rule that no longer matches leaves stock Pocket ID
  styling for that element.

`../scripts/lab-logins.py` keeps the apps' own logins off (`--check` only reports), and keeps the
shared login, `admin` and `vault_lab_admin_password`, on the accounts that still have a job:
qBittorrent's (anything outside the pod network), Grafana's local admin (break-glass next to
Pocket ID, and the Git Sync job's), and the Kuma, Bindery and Cleanuparr accounts the script
changes their settings with. It keeps qBittorrent's hash in `gluetun-secret`, Kuma's seed login
and Grafana's admin secret in step.

```sh
scripts/lab-logins.py        # apply; commit the files it lists
# rotate: the current password, then the new one
scripts/vault-set.py vault_lab_admin_previous_password vault_lab_admin_password && scripts/lab-logins.py
```

Not shared, on purpose: AdGuard (`admin` / `vault_adguard_password`; external-dns, the DoT cert
push, its exporter, Homepage and the Fire TV app all log in with it), OPNsense `root`
(`vault_opnsense_root_password`) and Proxmox `root@pam` (recovery accounts), and the personal
or GitHub logins of Jellyfin, Seerr (signs in with Jellyfin), Home Assistant and Cabotage.

`../scripts/bw-sync.py` puts all of them, plus AdGuard and OPNsense, into Bitwarden's **Homelab**
folder (one item per app with its URLs, so the extension autofills), copied from the vault; a
re-run only rewrites items that differ. Re-run it after any password change (`bw` is in `~/.local/bin`):

```fish
bw login                               # once
set -x BW_SESSION (bw unlock --raw)    # per shell
scripts/bw-sync.py
```

## Jellyfin SSO

`apps/media/jellyfin/app` installs
[K0lin/jellyfin-plugin-sso](https://github.com/K0lin/jellyfin-plugin-sso) (the maintained fork of
the archived `9p4/jellyfin-plugin-sso`; its `>= 5.1` releases target Jellyfin `>= 12.0`, matching
the server here) and configures a `pocket-id` OpenID provider: endpoint `https://id.z7x.org`,
client id `jellyfin` (secret `vault_pocket_id_jellyfin_secret`, provisioned by
`scripts/pocket-id.py`), scopes `openid profile email groups`, RBAC on with group `lab-admins`
both allowed to sign in and mapped to Jellyfin admin (`RoleClaim: groups`). The `jellyfin-sso`
Secret and `jellyfin-sso-install` Job do the work: add the plugin repository, install the plugin,
restart Jellyfin to load it, PUT its configuration, and add a "Sign in with Pocket ID" button to
the login page (`Settings > General > Branding`'s `LoginDisclaimer`/`CustomCss`, merged in rather
than overwritten). Local accounts and Quick Connect (the only sign-in path for apps/TVs; the
plugin is web-browser only) keep working untouched.

The Job needs a Jellyfin admin API key, which has no default -- create one in Jellyfin's
**Dashboard > API Keys**, then:

```sh
scripts/vault-set.py vault_jellyfin_api_key   # hidden prompt
scripts/jellyfin-sso-key.py                   # pushes it into secret.sops.yaml, commit the result
```

Flux re-applies the (otherwise immutable) Job on every script or Secret change because
`jellyfin`'s `ks.yaml` sets `force: true`. Re-run the Job manually with `kubectl -n media delete
job jellyfin-sso-install` if you change the provider config and want it reconciled sooner than the
next Flux apply.

## Jellyfin plugins and theme

The `jellyfin-plugins` Job (`apps/media/jellyfin/app/plugins.py`; it shares `jellyfin_api.py`
with the SSO Job) installs, from their repositories' Jellyfin 12 builds: Open Subtitles, Fanart,
Intro Skipper, InPlayerEpisodePreview (episode list in the player), File Transformation + Media
Bar, JavaScript Injector + Jellyfin Enhanced. TMDb ships with Jellyfin. It then restarts Jellyfin
once to load what's new. Editor's Choice is uninstalled (its banner stacked on Media Bar's;
`REMOVED_PLUGINS`). Through the JavaScript Injector it adds
KefinTweaks and jf-avatars, and at the top of the branding Custom CSS a marked block importing
ElegantFin, its Media Bar add-on and the `elegantfin-jf12` companion sheet for Jellyfin 12's
Modern layout. Every web script and stylesheet is pinned to a release tag in `plugins.py`; bump
them there. Movies and Shows get English subtitles from Open Subtitles once its login is in:

```sh
scripts/vault-set.py vault_opensubtitles_username vault_opensubtitles_password   # opensubtitles.com
scripts/media-secrets.py                                                         # commit what it lists
```

Not installed: Artisan Jelly (built against Jellyfin 10.10, doesn't load on 12). The plugins, scripts
and theme only change the web client and the mobile apps that render it; the Android TV app on
the Fire TV draws its own UI.

## Storage

`apps/storage/`: [proxmox-csi-plugin](https://github.com/sergelogvinov/proxmox-csi-plugin)
(`csi-proxmox` namespace, chart `oci://ghcr.io/sergelogvinov/charts/proxmox-csi-plugin`)
provisions Proxmox VM disks as PersistentVolumes -- no storage daemon runs in-cluster, the data
sits on whichever Proxmox node the volume lands on.

- **StorageClasses:** `proxmox-vmdata` (default; Proxmox storage `vmdata`, the cluster-wide ZFS
  mirror) and `proxmox-fast` (Proxmox storage `fast`, each node's local NVMe ZFS pool). Both are
  RWO, `WaitForFirstConsumer`, `ext4`: the first pod to use a PVC picks a node, the plugin
  allocates the disk there, and the resulting PV's `topology.kubernetes.io/zone` node affinity
  pins every future pod for that PVC to Proxmox nodes with that zone label -- no cross-node
  migration. `proxmox-fast` additionally requires the zone label since its storage is node-local;
  `proxmox-vmdata` is cluster-wide, so volumes can reattach on any node.
- **Credentials:** `ansible/roles/pve_csi` creates a `kubernetes-csi@pve` Proxmox user, a `CSI`
  role (`VM.Audit VM.Config.Disk Datastore.Allocate Datastore.AllocateSpace Datastore.Audit`) and
  an API token, then writes the token straight into `app/secret.sops.yaml` (never printed, never
  touches the Ansible vault). If that file is ever missing, the role rotates the token (Proxmox
  only shows a token's secret once) rather than failing.
- **Topology labels:** `ansible/roles/talos`'s `machine.yaml.j2` sets
  `topology.kubernetes.io/region` (the Proxmox cluster name, `homelab`) and
  `topology.kubernetes.io/zone` (the Proxmox node each Talos VM runs on, `k8s_nodes[].host`) via
  `KubeNodeConfig`. Every node that can run CSI-backed pods needs both labels.
- **VM requirements:** `VirtIO SCSI single` controller (already the Talos VM default) and disk
  hotplug, which Proxmox enables by default (`hotplug: network,disk,usb` unless overridden) --
  nothing extra to set.
- **Media NFS is separate:** the temporary `/media` export below is a plain NFS volume on a VM,
  not a Proxmox CSI StorageClass -- no provisioner, no PVC, just an NFS mount every media app
  uses directly.

### Media NFS (temporary)

`ansible/roles/media_nfs`: VM 401 `hnnfs01` (Debian 13 + cloud-init, 10.10.30.20 on the K8S
zone), standing in for a NAS until one exports the same layout. `nfs-kernel-server` exports
`/srv/media` to `10.10.30.0/24` (`rw,sync,no_subtree_check,no_root_squash`); apps mount it at
`/media` via `MEDIA_NFS_SERVER`/`MEDIA_NFS_PATH` in `cluster-settings.yaml`.

- **Layout:** TRaSH-style single tree so downloads and the library can be hardlinked instead of
  copied: `/srv/media/{downloads/{torrents,usenet}/{movies,tv,music,books},library/{movies,tv,music,books}}`,
  owned `1000:1000` mode `2775` (setgid, so new entries inherit the group).
- **Backups:** the root disk is covered by the nightly Proxmox vzdump job
  (`ansible/roles/pve_backup`); the 100 GB data disk is `backup=0` (media, not config -- nothing
  worth restoring from a Proxmox snapshot).

## Media stack

`apps/media/`: Jellyfin, Seerr (the project formerly called Jellyseerr), Sonarr, Radarr,
Lidarr, Prowlarr, Bazarr, [Bindery](https://github.com/vavallee/bindery) (Readarr replacement),
qBittorrent+gluetun, SABnzbd, Recyclarr, Unpackerr, FlareSolverr and Cleanuparr. One shared
`app-template` OCIRepository (`apps/media/app-template`) is the chart source for every
HelmRelease here.

- **Why Bindery, not Chaptarr/Bookshelf/LazyLibrarian:** Readarr is retired. Bindery is a single
  Go binary with no single point of failure for metadata (OpenLibrary first, Google
  Books/Hardcover/DNB/Audnex/Audible behind it), imports a dead `readarr.db` directly, and ships
  frequent releases (v1.46.1 as of 2026-11-01). Chaptarr (a Readarr fork) is also active but
  still beta with its own incompatible metadata pipeline; Bookshelf needs separate instances for
  ebooks and audiobooks; LazyLibrarian's GitHub mirror is stale (the live project is on GitLab).
- **Companions kept:** Recyclarr (TRaSH Guides quality profiles synced into Sonarr/Radarr by a
  CronJob, YAML in git -- chosen over Profilarr, which is a stateful GUI app with no clean
  GitOps story), Unpackerr (extracts rar/zip downloads for Sonarr/Radarr/Lidarr), FlareSolverr
  (Cloudflare/DDoS-Guard challenge solver for Prowlarr indexers that need it -- still actively
  released, kept over Byparr), Cleanuparr (removes stalled/blocked downloads, the maintained
  successor to Decluttarr).
- **Skipped:** Profilarr (overlaps with Recyclarr, GUI-only config), Huntarr (automatic
  missing/upgrade searching -- the user drives searches manually here), Byparr/Trawl (FlareSolverr
  itself is still maintained), Swaparr (bundled with Huntarr, narrower than Cleanuparr), Ombi
  (superseded by Seerr), Organizr/Homarr-style dashboards (the lab-wide start page is Homepage,
  `apps/status/homepage`).
- **API keys:** `apps/media/arr-api-keys` holds one `arr-api-keys` Secret with pre-generated keys for
  Sonarr/Radarr/Lidarr/Prowlarr, injected via `<APP>__AUTH__APIKEY` env vars (the Servarr config
  override convention) so Recyclarr and Unpackerr already know them, plus SABnzbd's (its image
  writes `SABNZBD__API_KEY` into `sabnzbd.ini` on start). Bazarr (Python, no such env
  override) and its "Settings" links to Sonarr/Radarr stay manual UI steps, along with the
  Jellyfin admin account.
- **Seerr:** Jellyfin at `jellyfin.media.svc.cluster.local:8096` (external `https://media.z7x.org`),
  Movies and Shows libraries enabled, Radarr/Sonarr as default servers with the 2160p-first
  profiles and `/media/library/{movies,tv}`. Settings live in `settings.json` on its PVC. Its
  setup wizard can strand you: once a media server is saved, `/setup` jumps straight to the
  libraries step, and without a session cookie every call there is a 401 ("Unable to load
  libraries"). Finish it with Seerr's own API key instead (`main.apiKey` in `settings.json`,
  `X-Api-Key`): `POST /api/v1/settings/jellyfin/library/sync`, `PUT .../library/<id>`
  `{"enabled":true}`, `POST /api/v1/settings/{radarr,sonarr}`, `POST /api/v1/settings/initialize`.
- **Download wiring (`apps/media/media-config`):** a Job (`app/configure.py`) puts TRaSH's SABnzbd
  settings in place (completed jobs in `/media/downloads/usenet/<category>`, in-progress in
  `.../usenet/incomplete`, categories movies/tv/music/books, the unwanted-extension blacklist
  that fails fake `.exe`/`.scr` releases) and the Newshosting server; qBittorrent's (save path
  `/media/downloads/torrents`, Automatic torrent management, the same four categories). In
  Sonarr/Radarr/Lidarr: both as download clients, `/media/library/<kind>` as root folder, and a
  Jellyfin connection that refreshes the library after imports. Prowlarr gets full-sync links to
  those three plus the NZBFinder and Anime Tosho (`feed.animetosho.net`) indexers. Downloads and
  library share one filesystem, so imports never copy: usenet ones are moves, torrent ones
  hardlinks (a seeding torrent and its library file are the same blocks). It runs once per
  change of the script or of the credentials, which live in the vault:

  ```sh
  scripts/vault-set.py vault_newshosting_username vault_newshosting_password vault_nzbfinder_api_key
  scripts/media-secrets.py   # copies them into app/secret.sops.yaml, bumps the Job; commit both
  ```

- **Quality:** Recyclarr syncs 2160p-first profiles: Sonarr `WEB-2160p (Combined)`, Radarr `UHD
  Bluray + WEB` with Bluray/WEB 1080p added below the 2160p qualities as a fallback (upgraded to
  2160p when one appears), and TRaSH's Jellyfin naming for both.
- **Shared media volume:** every app that touches files mounts the NFS export from
  `MEDIA_NFS_SERVER`/`MEDIA_NFS_PATH` (`cluster-settings.yaml`, owned by Storage) at `/media`,
  TRaSH-style (`/media/downloads/...`, `/media/library/...`) so imports hardlink instead of copy.
- **qBittorrent + gluetun:** one Pod (`apps/media/gluetun-qbittorrent`), so the two containers
  share a network namespace -- gluetun owns it, firewalls it (kill switch: only the WireGuard
  tunnel, DNS, and the cluster's pod/service subnets get through) and qBittorrent rides inside
  with no network config of its own. AirVPN, static port forwarding: gluetun's
  `FIREWALL_VPN_INPUT_PORTS` and qBittorrent's `--torrenting-port` both read the same
  `FORWARDED_PORT` secret key, so they can't drift apart. The WireGuard key, preshared key,
  address and port come from `vault_vpn_*` in the Ansible vault (the forwarded port is AirVPN
  Client Area → Ports; the rest is the Config Generator's Linux/WireGuard `.conf`). Rotating them
  means updating the vault and `sops kubernetes/apps/media/gluetun-qbittorrent/app/secret.sops.yaml`.
- **Node pinning:** config PVCs use `proxmox-vmdata` (RWO, `WaitForFirstConsumer`). The first pod
  for each app picks a node, Proxmox CSI provisions the volume on that node's local ZFS pool, and
  the resulting PV's node affinity pins every future pod for that app to the same node -- there's
  no cross-node migration for these volumes.
- **Cloudflare cache bypass:** `media.z7x.org` streams video through the Cloudflare tunnel, and
  Cloudflare caches `.mp4`/`.mkv` etc. by default, which risks violating its ToS for video
  delivery. `cloudflare_api_token` was granted `Zone > Cache Rules > Edit`, and
  `ansible/roles/cloudflare_cache_rules` (`ansible-playbook playbooks/site.yml --tags cloudflare`)
  idempotently manages a bypass-cache rule for `media.z7x.org` in the zone's
  `http_request_cache_settings` phase, matched by description so a second run changes nothing and
  any other rules in that phase are left alone.

## Backups and restore

Everything offsite goes to Cloudflare R2, bucket `homelab-backups` (account
`0346e28e1f2af422c32c0c37e9f7f363`), as restic repositories under one prefix per source so they
can share the bucket safely. Retention (`keep-daily 7, keep-weekly 4` everywhere) is tuned to
stay well under the 10 GB free tier. Credentials: `R2_ENDPOINT`/`R2_BUCKET` in
`cluster-settings.yaml`, `R2_ACCESS_KEY_ID`/`R2_SECRET_ACCESS_KEY`/`VOLSYNC_RESTIC_PASSWORD` in
`cluster-secrets.sops.yaml` -- both substituted into every backup app's manifests via
`postBuild.substituteFrom`. Each restic repository password is generated once and stored only in
SOPS (recoverable with the age key, `vault_sops_age_key` / `~/.config/sops/age/keys.txt`); losing
it means losing that repository's data, same as any restic repo.

Local Proxmox vzdump backups (full VM/CT images, no restic) are covered in
[`../ansible/README.md#backups`](../ansible/README.md#backups) -- they're the fast path back for
whole guests; R2 is the offsite path for the things a lost node/disk would otherwise need a full
rebuild for (cluster state, firewall config, app config).

### etcd (`apps/backup/etcd-backup`)

Nightly (03:00) CronJob: an init container (`ghcr.io/siderolabs/talosctl`) takes an etcd snapshot
via the Talos API, authenticated through [Talos API access from
Kubernetes](https://docs.siderolabs.com/kubernetes-guides/advanced-guides/talos-api-access-from-k8s)
(`talos/patches/cluster.yaml`'s `KubeTalosAPIAccessConfig`, role `os:etcd:backup`, namespace
`backup` only -- the `etcd-backup-talos` `ServiceAccount` CR in the app mounts the short-lived
talosconfig), then a restic container backs the snapshot file up to
`s3:${R2_ENDPOINT}/${R2_BUCKET}/etcd`.

```sh
export RESTIC_REPOSITORY="s3:$(mise exec -- kubectl get cm cluster-settings -n flux-system -o jsonpath='{.data.R2_ENDPOINT}')/homelab-backups/etcd"
export RESTIC_PASSWORD=$(mise exec -- sops -d --extract '["stringData"]["RESTIC_PASSWORD"]' kubernetes/apps/backup/etcd-backup/app/secret.sops.yaml)
export AWS_ACCESS_KEY_ID=$(ansible-vault view ansible/inventory/group_vars/all/vault.yml | yq '.vault_r2_access_key_id')
export AWS_SECRET_ACCESS_KEY=$(ansible-vault view ansible/inventory/group_vars/all/vault.yml | yq '.vault_r2_secret_access_key')
mise exec -- restic snapshots
mise exec -- restic restore latest --target ./etcd-restore
# Verify the restored file before trusting it (does NOT touch the live cluster):
etcdutl snapshot status ./etcd-restore/<path>/db.snapshot -w table
# Disaster recovery (replaces the live cluster's etcd -- only when actually needed):
#   talosctl -n <node> etcd snapshot <restored-file>  # copy it to the node first, then
#   talosctl -n <node> bootstrap --recover-from=<path-on-node>
```

### OPNsense (`apps/backup/opnsense-backup`)

Nightly (03:00) CronJob: fetches `config.xml` from both firewalls' core backup API
(`/api/core/backup/download/this`, same `vault_opnsense_api_key`/`vault_opnsense_api_secret` as
`ansible/roles/opnsense`, at each firewall's own K8S-zone address 10.10.30.2 / 10.10.30.3), then
restic-backs them up (`config-<address>.xml`) to `s3:${R2_ENDPOINT}/${R2_BUCKET}/opnsense`.

```sh
# Same pattern as etcd above, repository .../opnsense, password from
# kubernetes/apps/backup/opnsense-backup/app/secret.sops.yaml
mise exec -- restic -r "s3:$R2_ENDPOINT/homelab-backups/opnsense" restore latest --target ./opnsense-restore
# Restore: OPNsense webGUI -> System -> Configuration -> Backups -> Restore configuration,
# upload ./opnsense-restore/backup/config-<address>.xml for that firewall (or POST it to
# /api/core/backup/restore).
```

### App config PVCs (`components/volsync`)

A reusable kustomize Component, included by an app's own `kustomization.yaml` with three vars:
`APP` (must match the PVC's name), `VOLSYNC_CAPACITY` (size of restic's metadata cache volume),
and optionally `VOLSYNC_STORAGECLASS` (defaults to `proxmox-vmdata`). It adds a `${APP}-volsync-restic`
Secret and a nightly (04:00) `ReplicationSource` backing `${APP}`'s PVC up to
`s3:${R2_ENDPOINT}/${R2_BUCKET}/volsync/${APP}` with `copyMethod: Direct` (the Proxmox CSI driver
doesn't support clone/snapshot, so the mover reads the PVC directly; safe because volumes are
node-pinned and the mover lands on the same node). The including app's `ks.yaml` must list
`cluster-settings` and `cluster-secrets` in `postBuild.substituteFrom` alongside its own vars, and
VolSync (the controller, `apps/backup/volsync`) must be installed first. Media files (NFS) are
never in scope -- only PVCs opted into this component are backed up.

```sh
# Restore <app>'s PVC into a scratch PVC to inspect/diff before touching the real one:
cat <<EOF | mise exec -- kubectl apply -n <namespace> -f -
apiVersion: v1
kind: PersistentVolumeClaim
metadata: {name: <app>-restore-scratch}
spec: {accessModes: [ReadWriteOnce], resources: {requests: {storage: <size>}}}
---
apiVersion: volsync.backube/v1alpha1
kind: ReplicationDestination
metadata: {name: <app>-restore}
spec:
  trigger: {manual: restore-once}
  restic:
    repository: <app>-volsync-restic
    destinationPVC: <app>-restore-scratch
    copyMethod: Direct
EOF
mise exec -- kubectl diff -n <namespace> ... # compare the scratch PVC's contents to the live one
```

### Home Assistant

HAOS 2026.2+ ships a built-in **Cloudflare R2** backup agent (Settings -> Devices & services ->
Add Integration -> *Cloudflare R2*) -- no supervisor add-on, no network-mount detour needed. It
needs a UI step and credentials the agent running this task doesn't have (an HA login): once in
HA,

1. Settings -> Devices & services -> Add Integration -> **Cloudflare R2**.
2. Access key ID / Secret access key: reuse `vault_r2_access_key_id` /
   `vault_r2_secret_access_key` (`ansible-vault view ansible/inventory/group_vars/all/vault.yml`),
   or create a bucket-scoped token instead (Cloudflare dashboard -> R2 -> Manage API tokens ->
   Create User API token -> Object Read & Write, limited to `homelab-backups`) if you'd rather not
   reuse the account-wide one.
3. Bucket name: `homelab-backups`. Endpoint URL: `https://0346e28e1f2af422c32c0c37e9f7f363.r2.cloudflarestorage.com`.
   Folder prefix: `homeassistant`.
4. Settings -> System -> Backups -> add a schedule and pick the new Cloudflare R2 location as a
   target (in addition to local, so you still have a fast local restore path).

Restores are built in too: Settings -> System -> Backups -> pick the R2 backup -> Restore (or
from the onboarding screen on a freshly reinstalled HAOS).

## Dashboards (Grafana Git Sync)

`apps/observability/grafana-dashboards/app/dashboards/` is owned by Grafana
[Git Sync](https://grafana.com/docs/grafana/latest/as-code/git-sync/): Grafana polls `main` every
60s through the private GitHub App `grafana-z7x-git-sync` (installed on this repo only) and shows
the files under a **Homelab** folder, one subfolder per directory. Each directory's
`_folder.json` sets that folder's title and stable UID. Edit dashboards in either place: commit
JSON here, or save in the UI, which pushes a branch and opens a PR (nothing lands on `main`
without a merge). Chart-shipped dashboards (kube-prometheus-stack, the gnetId downloads in the
**Imported** folder) stay on the sidecar and file provider and are read-only.

The Kustomization's `grafana-git-sync` Job registers the Connection and Repository through
Grafana's provisioning API and fails if the first sync isn't healthy. Its secret
(`app/secret.sops.yaml`) is built from the vault keys `vault_grafana_github_app_id`,
`vault_grafana_github_app_installation_id` and `vault_grafana_github_app_private_key`, which
`../scripts/github-app.py grafana` creates. Most dashboard JSON is vendored from upstream
(grafana.com, the grafana/{mimir,loki,alloy} mixins, cilium/cilium, envoyproxy/gateway), with
datasource variables patched so it follows whichever Prometheus/Loki datasource is the default.
Never name a folder "General": that's Grafana's reserved root folder.

## Updates

Renovate ([`../.github/renovate.json5`](../.github/renovate.json5)) runs nightly over chart
versions, images, `mise.toml` tools and the annotated pins in the Ansible defaults (Talos,
Kubernetes, Cilium bootstrap). Patch, minor and digest updates under `kubernetes/`, `ansible/` and
`mise.toml` merge to main by themselves once the release is 3 days old, and Flux deploys them.
Every HelmRelease rolls back a failed upgrade (`flux/cluster/apps.yaml` patches that into all app
Kustomizations, so they mustn't set `spec.patches` themselves), and FluxResourceNotReady alerts
Discord. Majors (label `major`) and Talos/Kubernetes (label `talos`) open a PR straight away
and never merge on their own: GitHub notifies you, read the release notes, merge. The Fire TV
app's dependencies and the Cabotage module (held until a release past v1.9.3, see
`../terraform/cabotage/README.md`) come as PRs too.

The `*arr` images (`ghcr.io/home-operations/*`) are built from Servarr's **develop** branch, so a
new major is often a pre-release upstream, and its database migration can't be undone: copy
`/config/*.db` out of the pod before merging. Uptime Kuma's seed Job and `scripts/lab-logins.py`
use `uptime-kuma-api2`; the original `uptime-kuma-api` can't talk to Kuma 2.

A merged Talos or Kubernetes bump only changes config for new installs; upgrade the running
nodes one at a time, then Kubernetes:

```sh
talosctl -n 10.10.30.11 upgrade --image factory.talos.dev/installer/<schematic>:<version>   # then .12, .13, then the workers (.14, ...)
talosctl -n 10.10.30.11 upgrade-k8s --to <version>
```

## Cabotage PaaS

`terraform/cabotage/` (repo root, outside this `kubernetes/` tree) runs the
[Cabotage](https://github.com/cabotage/cabotage-app) PaaS via the upstream `cabotage/terraform`
module — Terraform-managed, not Flux. See
[`../terraform/cabotage/README.md`](../terraform/cabotage/README.md) for how to run it, where
secrets live, GitHub App setup, and how to deploy an app. This `kubernetes/` tree only owns the
pieces that integrate it with the rest of the cluster: the `letsencrypt` ClusterIssuer
(`apps/cert-manager/cert-manager-issuers/`), the public GitHub-webhook-only HTTPRoute
(`apps/cabotage/cabotage-ingress/`), and the read-only Cabotage Loki/Mimir Grafana datasources
(`apps/observability/kube-prometheus-stack/`). DNS for `*.apps.z7x.org` is a static AdGuard
rewrite (`../ansible/roles/adguard/`), not external-dns.

