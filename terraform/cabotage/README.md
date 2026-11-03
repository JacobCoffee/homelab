# Cabotage PaaS

Runs [Cabotage](https://github.com/cabotage/cabotage-app) on the homelab cluster via the upstream
[`cabotage/terraform`](https://github.com/cabotage/terraform) `modules/cabotage` module, consumed
directly from git the same way `terraform/minikube` does in that repo.

This is the homelab's first Terraform/OpenTofu usage — see [`tf.sh`](tf.sh) for how it's wired in.

## Topology

- Single replica everywhere (minikube-style): Consul, Vault, RustFS (plain FS mode, no erasure
  coding), standalone Loki/Mimir, one Postgres instance, one of everything else. Picked to fit on
  `hnk8s03` (8GB).
- Vault unseal: the module's dev auto-unseal sidecar (`vault_dev_auto_unseal = true`) — the unseal
  key sits in a plain Kubernetes Secret. Accepted risk for a homelab.
- Ingress: Traefik (the module's own, driven by `kubernetesIngressNGINX`) as a plain
  `LoadBalancer` Service pinned to Cilium LB-IPAM IP **`10.10.30.202`** via the
  `traefik_service_annotations` toggle (new in the PR below). The homelab's Envoy Gateway is
  **not** in the tenant/UI/registry path at all — only used for the public GitHub webhook (below).
- TLS: cert-manager is **not** installed by this module (`enable_cert_manager = false`) — it reuses
  the homelab's existing Flux-managed cert-manager 1.21. The cert-manager **CSI driver** (internal
  mTLS material) is still installed by the module. CoreDNS is untouched
  (`enable_coredns_hardening = false`, Talos manages it). The module's own `letsencrypt`
  ClusterIssuer is skipped (`enable_letsencrypt_issuer = false`); instead
  `kubernetes/apps/cert-manager/cert-manager-issuers/app/clusterissuer.yaml` carries a second
  ClusterIssuer literally named `letsencrypt` (DNS-01 via Cloudflare, scoped to `apps.z7x.org`,
  reusing the `cloudflare-api-token` secret) — this is the name Cabotage's tenant ingresses expect
  by default.
- DNS: `*.apps.z7x.org` is a static AdGuard rewrite to `10.10.30.202`
  (`ansible/roles/adguard/tasks/apps_rewrite.yml`, driven by
  `adguard_static_rewrites` in `ansible/roles/adguard/defaults/main.yml`) — Cabotage creates many
  dynamic tenant hostnames under this domain at runtime, so a wildcard is required (a per-route
  AdGuard entry via external-dns isn't possible here, unlike normal homelab apps).
- GitHub webhooks: published **only** at `https://cabotage.z7x.org/github/hooks` (one label under
  `z7x.org`, covered by the existing wildcard edge cert) via
  `kubernetes/apps/cabotage/cabotage-ingress/app/httproute.yaml` on the `external` Envoy Gateway —
  `PathPrefix /github/hooks` only, rewrites the `Host` header to `cabotage.apps.z7x.org` and
  forwards to Traefik. Everything else at `cabotage.z7x.org` 404s (no other rule exists). The
  Cabotage UI itself (`cabotage.apps.z7x.org`) stays internal/tailnet-only.
- Observability: Cabotage runs its own standalone Alloy/Loki/Mimir (module `monitoring.tf`), not
  wired into the homelab's Loki/Mimir. Two read-only Grafana datasources
  (`Cabotage Loki`/`Cabotage Mimir`) were added to
  `kubernetes/apps/observability/kube-prometheus-stack/app/helmrelease.yaml` pointing at Cabotage's
  in-cluster `resident-loki-read`/`resident-mimir-read` services with the `cabotage-infra`
  `X-Scope-OrgID`. **Known gap**: these use `tlsSkipVerify: true` — Cabotage's internal CA isn't
  plumbed into Grafana's trust store yet.

## Upstream changes

Five small commits were added to `modules/cabotage` on a branch, in a **draft PR** (still open as
of 2026-11-02):

- PR: https://github.com/cabotage/terraform/pull/21
- Branch: `homelab-cert-manager-traefik-toggles`, HEAD commit
  `0af4603f636ad0583ac9ec64699a546f8b77ed87` — this is what `main.tf`'s module `source` is pinned
  to (`?ref=<sha>`). **Once the PR merges, re-pin `ref` to the merge commit on `main`.**
- Four backward-compatible toggles (all default `true`, preserving current upstream behavior):
  - `enable_cert_manager` (bool, default `true`) — skip `helm_release.cert_manager` to reuse an
    external cert-manager install (CSI driver still installs either way).
  - `enable_coredns_hardening` (bool, default `true`) — skip the module's CoreDNS PTR-disabling
    patch.
  - `enable_letsencrypt_issuer` (bool, default `true`) — skip the module's own `letsencrypt`
    ClusterIssuer entirely.
  - `traefik_service_annotations` (map(string), default `{}`) — extra annotations merged onto the
    Traefik `LoadBalancer` Service (used here for `lbipam.cilium.io/ips`).
- A real upstream bug fix (3 commits): the intermediate-CA signing scripts omitted
  `subjectKeyIdentifier`/`authorityKeyIdentifier` extensions, which modern OpenSSL (Python 3.14's
  `ssl` module) rejects with `CERTIFICATE_VERIFY_FAILED: Missing Authority Key Identifier`, and
  which breaks Vault PKI CRL building. Discovered live when cabotage-app couldn't TLS-connect to
  Vault.

**Renovate "major update" note**: Renovate flags this `ref=<sha>` pin as a pending major-version
bump since it isn't a semver tag. Checked 2026-11-02: the latest upstream tag is `v1.9.3`
(commit `a1bca12`), which is an *ancestor* of our pinned commit, not a descendant — our branch is
built on top of `v1.9.3` plus one upstream docs commit, then adds the toggles and CA fix above.
Re-pinning to `v1.9.3` would regress this deployment (drop all four toggles, reintroduce the Vault
PKI CRL bug) for no upstream functional gain, since no release has shipped since. Left pinned to
the PR branch HEAD; re-check when cabotage/terraform cuts a release at or after PR #21 merges.


## Running it

```sh
cd terraform/cabotage
./tf.sh init
./tf.sh plan
./tf.sh apply
```

`tf.sh` pulls R2 state-backend credentials and GitHub App secrets from the **Ansible vault**
(`ansible/inventory/group_vars/all/vault.yml`) at run time and materializes the module's
`secrets_dir` (`.secrets/`, gitignored) before every run — nothing sensitive is ever committed.
State lives in Cloudflare R2, bucket `homelab-tfstate`, key `cabotage/terraform.tfstate`
(account `0346e28e1f2af422c32c0c37e9f7f363`, created via the R2 S3 API with
`vault_r2_access_key_id`/`vault_r2_secret_access_key`, which already exist in the vault for
backups). OpenTofu (not Terraform, to avoid the BSL) is pinned in `mise.toml`
(`opentofu = "1.9.4"`).

### First apply: Consul/Vault bootstrap order

Mirrors the upstream module's own bootstrap chain (see its README "Bootstrap order"). With
`vault_dev_auto_unseal = true` this is less manual than production (no KMS/recovery-key dance),
but Consul ACL bootstrap still has a dependency ordering quirk the module doesn't fully
automate (per `cabotage/infra`'s `CLUSTER_BOOTSTRAP.md` "known rough edges" — Consul's readiness
probe is ACL-gated, so the first `apply` may need a retry once `consul-0` comes up). Just
`./tf.sh apply` again if it times out waiting on Consul or Vault — it's idempotent.

### After apply: back up crown-jewel secrets

```sh
./sync-secrets-to-vault.sh
```

Copies `.secrets/{consul-management-token,vault-bootstrap-token,vault-unseal-key,vault-recovery-key}`
(whichever exist) into the Ansible vault as `vault_cabotage_consul_management_token`,
`vault_cabotage_vault_root_token`, `vault_cabotage_vault_unseal_key`,
`vault_cabotage_vault_recovery_key` — never printed, never committed outside the encrypted vault
file. Treat these as crown jewels. Run this any time `.secrets/` gains new tokens (e.g. after a
`terraform taint`/re-bootstrap).

### DNS rollout (idempotent)

```sh
cd ansible
ansible-playbook playbooks/site.yml --tags core -e opnsense_update=false
```

Re-run any time; the AdGuard rewrite task is idempotent (`changed=0` on repeat runs).

## GitHub App

Cabotage needs **one GitHub App** for webhook-driven builds and GitHub login. GitHub has no API
that creates apps, so `scripts/github-app.py` uses GitHub's manifest flow: it opens a page that
submits the app definition, you click **Create GitHub App**, and `gh api` trades the one-time code
for the app id, client id/secret, private key and webhook secret, written straight into the vault
(nothing pasted or printed):

```sh
scripts/github-app.py cabotage
cd terraform/cabotage && ./tf.sh apply   # module picks up github_app_id + the new secret files
```

The definition lives in the script (`APPS["cabotage"]`): app `cabotage-z7x`, private to your
account, homepage `https://cabotage.apps.z7x.org`, callback and setup URL
`https://cabotage.apps.z7x.org/auth/github/callback` (Cabotage branches on `state`), webhook
`https://cabotage.z7x.org/github/hooks` (the one public path, see above). Permissions and events
match what cabotage-app calls and handles: Contents read, Deployments write, Checks write, Pull
requests write, Commit statuses read, Administration read, Metadata read, Email addresses read;
events Push, Pull request, Deployment, Check suite, Repository (installation events always
arrive). Don't install it from GitHub; Cabotage's UI starts the install so it links to your
account.

## Optional features

| Feature | State | Where |
|---|---|---|
| Signup | off (`CABOTAGE_SECURITY_REGISTERABLE=False`): no signup form, and GitHub sign-in only logs in users who already exist | `cabotage_app_extra_config` in `main.tf` |
| Organization requests | off (`CABOTAGE_ORGANIZATION_REQUESTS_ENABLED=False`) | `cabotage_app_extra_config` in `main.tf` |
| Cluster switcher | on for admins: homelab plus the PSF installs and `prime` (`CABOTAGE_CLUSTERS`, colors matching the PSF switcher) | `cabotage_app_extra_config` in `main.tf` |
| Cost estimates | off: the rates are AWS on-demand prices, meaningless off AWS | `cabotage_app_extra_config` in `main.tf` |
| Backing Postgres/Redis, tenant Postgres backups (RustFS), web shell, network policies, alerting | on (module defaults) | module |
| Tailscale operator | on, device `cabotage-operator` (`tag:cabotage-operator`) on the homelab tailnet; no Funnel ingress | `enable_tailscale` in `main.tf` |
| Discord notifications | on once the vault has the Discord app (below) | `cabotage_app_extra_config` in `main.tf` |
| Slack notifications, Sentry, Docker Hub pulls, mail, legal pages | off | need an app/token/URL each |
| Karpenter, KMS unseal, S3 instead of RustFS, IRSA | off | AWS only |

Discord: create an application at <https://discord.com/developers/applications>. Under **OAuth2**,
add the redirect `https://cabotage.apps.z7x.org/integrations/discord/callback` and note the client
ID and secret. Under **Bot**, turn on **Requires OAuth2 Code Grant** (Cabotage's callback exchanges
the code it returns) and reset the token. Store the three values, then re-apply; orgs connect a
server from their settings page (the bot only gets Send Messages):

```sh
scripts/vault-set.py vault_cabotage_discord_client_id vault_cabotage_discord_client_secret vault_cabotage_discord_bot_token
cd terraform/cabotage && ./tf.sh apply
```

The module has no Secret for these, so they live in the `cabotage-config` ConfigMap.

Tailscale details, all homelab-side (no module patch):
- The operator's OAuth client (scopes `devices:core`, `auth_keys`, `services`; tag
  `tag:cabotage-operator`) was created through the Tailscale API; its id and secret are the vault
  keys `vault_cabotage_tailscale_oauth_client_id` / `_secret`, exported by `tf.sh`. Both tags are
  declared in `ansible/roles/tailscale_tailnet/templates/policy.hujson.j2`.
- Images are upstream `tailscale/k8s-operator` / `tailscale/tailscale` v1.98.9, not the module's
  default fork images (arm64-only; these nodes are amd64), on the module's chart 1.94.2. Charts
  from 1.96 bundle the Tailnet/ProxyGroupPolicy CRDs the module already applies, and operators from
  1.102 need the PeerRelay CRD, so bump the image only after checking both.
- `main.tf` adds two things the module lacks on Talos/Cilium: a ClusterRole for the operator's
  ValidatingAdmissionPolicies, and a CiliumNetworkPolicy letting the `tailscale` namespace reach
  the kube-apiserver (the module's policy only allows 443/8443 to world CIDRs, i.e. EKS).
- `tf.sh` labels the `tailscale` namespace PodSecurity `privileged` (the proxies need NET_ADMIN).

RustFS's data volume is 50Gi (`rustfs_storage_size`). Resizing later: expand the PVC
(`kubectl -n cabotage patch pvc data-0-rustfs-0 ...`, the storage class allows it), delete the
StatefulSet with `--cascade=orphan` (its `volumeClaimTemplates` are immutable), then `./tf.sh apply`
recreates it around the running pod.

## First login / first admin

1. Visit `https://cabotage.apps.z7x.org` (tailnet), sign in with GitHub. This creates your user row
   with `admin=false`.
2. Flip the flag:
   ```sh
   cd /Users/coffee/git/public/JacobCoffee/homelab
   PRIMARY=$(mise exec -- kubectl get cluster cabotage -n postgres -o jsonpath='{.status.currentPrimary}')
   mise exec -- kubectl exec -n postgres "$PRIMARY" -c postgres -- \
     psql -U postgres -d app -P pager=off -c "SELECT username, admin FROM users ORDER BY registered_at;"
   mise exec -- kubectl exec -n postgres "$PRIMARY" -c postgres -- \
     psql -U postgres -d app -P pager=off -c "UPDATE users SET admin = true WHERE username = '<your-github-username>';"
   ```
3. Re-login / refresh to pick up the admin role.

## Deploying your first app

Once logged in as admin: create an Organization → Project → Application in the UI, connect the
GitHub App to your repo, push — Cabotage builds via the registry at `registry.apps.z7x.org` and
deploys to a `<slug>.apps.z7x.org` hostname (auto-issued a `letsencrypt` cert via the DNS-01
issuer above). Internal-only, tailnet DNS.

## Verifying

```sh
mise exec -- kubectl get pods -n cabotage -n postgres -n redis -n cabotage-tenant-builds
curl https://cabotage.apps.z7x.org              # tailnet, valid LE cert
curl https://registry.apps.z7x.org/v2/          # 401 expected, valid LE cert
curl https://cabotage.z7x.org/github/hooks       # from the internet — any Cabotage response (400/401/405) for an unsigned POST
curl -i https://cabotage.z7x.org/                # from the internet — 404
cd terraform/cabotage && ./tf.sh plan            # no changes
```
