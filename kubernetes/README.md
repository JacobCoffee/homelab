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

## Publishing an app

Add an HTTPRoute with a `z7x.org` hostname and pick the gateway:

- `parentRefs: [{name: internal, namespace: network, sectionName: https}]` for lab/tailnet only.
- `parentRefs: [{name: external, namespace: network, sectionName: https}]` for the internet,
  through Cloudflare.

DNS records and the certificate are handled for you.

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
