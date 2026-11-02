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
| `flux/cluster/cluster-settings.yaml` | non-secret variables (`CLOUDFLARE_TUNNEL_ID`, `ACME_EMAIL`) |
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
  delivery. `cloudflare_api_token` only has DNS + Tunnel edit, not Cache Rules, so the bypass rule
  for `media.z7x.org` is not yet created -- add `Zone > Cache Rules > Edit` to that token (or
  create the rule by hand in the dashboard: Caching → Cache Rules → bypass cache when hostname
  equals `media.z7x.org`).

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
