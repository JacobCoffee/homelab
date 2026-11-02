# Kubernetes (Talos + Flux)

Three Talos nodes (`hnk8s01`–`03`, 10.10.30.11–13, API VIP 10.10.30.10) built by
[`../ansible`](../ansible/README.md#kubernetes-talos). Once `flux bootstrap` has run, the cluster
follows `main`: push a change here and Flux applies it (every hour, or right away with
`flux reconcile ks flux-system --with-source`).

From the repo root (`mise.toml` sets `KUBECONFIG`, `TALOSCONFIG` and the SOPS key path; the Mac
reaches 10.10.30.0/24 through OPNsense's Tailscale subnet route):

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
| metrics-server, kubelet-csr-approver | `kubectl top`; approves kubelet serving certificates |
| Spegel | nodes share pulled images with each other |
| echo | smoke test at `echo.z7x.org` on the internal gateway |
| home-assistant | `home.z7x.org` on both gateways, backed by the HA VM (selector-less Service + EndpointSlice to 10.10.10.20:80) |
| media stack | Jellyfin (`media.z7x.org`), Seerr (`requests.z7x.org`), the *arrs, qBittorrent+gluetun, SABnzbd -- see "Media stack" below |
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
  (superseded by Seerr), Organizr/Homarr-style dashboards (not requested).
- **API keys:** `apps/media/secrets` holds one `arr-api-keys` Secret with pre-generated keys for
  Sonarr/Radarr/Lidarr/Prowlarr, injected via `<APP>__AUTH__APIKEY` env vars (the Servarr config
  override convention) so Recyclarr and Unpackerr already know them. Bazarr (Python, no such env
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
  `FORWARDED_PORT` secret key, so they can't drift apart. `secret.sops.yaml` has placeholder
  values until `vault_vpn_wireguard_private_key`, `vault_vpn_wireguard_preshared_key`,
  `vault_vpn_wireguard_addresses` and `vault_vpn_forwarded_port` exist in the Ansible vault (the
  AirVPN account doesn't exist yet) -- this is the only Kustomization under `media/` waiting on a
  secret; everything else applies and runs without it.
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

Nightly (03:00) CronJob: fetches `config.xml` from OPNsense's core backup API
(`/api/core/backup/download/this`, same `vault_opnsense_api_key`/`vault_opnsense_api_secret` as
`ansible/roles/opnsense`, reachable at `10.10.10.1` from the K8S zone), then restic-backs it up to
`s3:${R2_ENDPOINT}/${R2_BUCKET}/opnsense`.

```sh
# Same pattern as etcd above, repository .../opnsense, password from
# kubernetes/apps/backup/opnsense-backup/app/secret.sops.yaml
mise exec -- restic -r "s3:$R2_ENDPOINT/homelab-backups/opnsense" restore latest --target ./opnsense-restore
# Restore: OPNsense webGUI -> System -> Configuration -> Backups -> Restore configuration,
# upload ./opnsense-restore/config.xml (or POST it to /api/core/backup/restore).
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

## Updates

Renovate ([`../.github/renovate.json5`](../.github/renovate.json5)) opens PRs on weekends for
chart versions, images, `mise.toml` tools and the annotated pins in the Ansible defaults
(Talos, Kubernetes, Cilium bootstrap). Merging a chart/image PR is the deploy: Flux applies it.
Majors, and Talos/Kubernetes, wait for approval on the Dependency Dashboard issue.

A merged Talos or Kubernetes bump only changes config for new installs; upgrade the running
nodes one at a time, then Kubernetes:

```sh
talosctl -n 10.10.30.11 upgrade --image factory.talos.dev/installer/<schematic>:<version>   # then .12, .13
talosctl -n 10.10.30.11 upgrade-k8s --to <version>
```
