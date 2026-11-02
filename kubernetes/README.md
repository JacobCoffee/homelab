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
| uptime-kuma | `apps/status/uptime-kuma` (namespace `status`): admin UI internal-only at `kuma.z7x.org`, public read-only status API at `status.z7x.org` -- see `fire-tv/DECISIONS.md` "Homelab services" |
| homepage | `apps/status/homepage`: start page at `start.z7x.org` (internal only): every app with an up/down dot, pod CPU/memory, Proxmox VM/LXC stats, the apps' own widgets, and news feeds. Edit `app/config/services.yaml`; changes reach the pod within a minute, no restart. Credentials are `app/secret.sops.yaml`, written by `ansible-playbook playbooks/site.yml --tags homepage` (`../ansible/roles/homepage`); after it changes, `kubectl -n status rollout restart deploy/homepage` |
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

Every app with its own login uses the same one: `admin` and `vault_lab_admin_password` from the
Ansible vault. That's Sonarr, Radarr, Lidarr, Prowlarr, Bazarr, SABnzbd, Bindery, Cleanuparr,
qBittorrent, Unpackerr, Uptime Kuma and Grafana. `../scripts/lab-logins.py` applies it through
each app's API and keeps the secrets that hand it over on restart in step (Unpackerr's
`UNPACKERR_UI_LOGIN` in `arr-api-keys`, qBittorrent's hash in `gluetun-secret`, Kuma's seed login,
Grafana's admin secret); `--check` only reports. The *arrs require the login everywhere
(`AUTH__REQUIRED: Enabled`): every request reaches them from the gateway's pod IP, so "not for
local addresses" meant no login at all.

```sh
scripts/lab-logins.py        # apply; commit the files it lists, then restart Unpackerr
# rotate: the current password, then the new one
scripts/vault-set.py vault_lab_admin_previous_password vault_lab_admin_password && scripts/lab-logins.py
```

Not shared, on purpose: AdGuard (`admin` / `vault_adguard_password`; external-dns, the DoT cert
push, its exporter, Homepage and the Fire TV app all log in with it), OPNsense `root`
(`vault_opnsense_root_password`) and Proxmox `root@pam` (recovery accounts), and the personal
or GitHub logins of Jellyfin, Seerr (signs in with Jellyfin), Home Assistant and Cabotage.

`../scripts/bw-sync.py` puts all of them, plus AdGuard and OPNsense, into Bitwarden's **Homelab**
folder (one item per app with its URLs, so the extension autofills), copying from the vault and
only rewrites items that differ; re-run it after any password change (`bw` is in `~/.local/bin`):

```fish
bw login                               # once
set -x BW_SESSION (bw unlock --raw)    # per shell
scripts/bw-sync.py
```

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
  override) and the Prowlarr "Apps"/Bazarr "Settings" links to Sonarr/Radarr have no declarative
  equivalent upstream -- those stay manual UI steps, along with indexer/Usenet provider accounts,
  the Jellyfin admin account, and Seerr's first-run wizard (which needs that Jellyfin account).
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

Renovate ([`../.github/renovate.json5`](../.github/renovate.json5)) opens PRs on weekends for
chart versions, images, `mise.toml` tools and the annotated pins in the Ansible defaults
(Talos, Kubernetes, Cilium bootstrap). Merging a chart/image PR is the deploy: Flux applies it.
Majors, and Talos/Kubernetes, wait for approval on the Dependency Dashboard issue.

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

