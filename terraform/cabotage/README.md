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

Four small, backward-compatible toggles (all default `true`, preserving current upstream
behavior) were added to `modules/cabotage` on a branch, in a **draft PR**:

- PR: https://github.com/cabotage/terraform/pull/21
- Branch: `homelab-cert-manager-traefik-toggles`, HEAD commit
  `9d9e4869b50d803c5ee187ca6f4107187a4e3364` — this is what `main.tf`'s module `source` is pinned
  to (`?ref=<sha>`). **Once the PR merges, re-pin `ref` to the merge commit on `main`.**
- `enable_cert_manager` (bool, default `true`) — skip `helm_release.cert_manager` to reuse an
  external cert-manager install (CSI driver still installs either way).
- `enable_coredns_hardening` (bool, default `true`) — skip the module's CoreDNS PTR-disabling
  patch.
- `enable_letsencrypt_issuer` (bool, default `true`) — skip the module's own `letsencrypt`
  ClusterIssuer entirely.
- `traefik_service_annotations` (map(string), default `{}`) — extra annotations merged onto the
  Traefik `LoadBalancer` Service (used here for `lbipam.cilium.io/ips`).

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

## GitHub App — manual step

Cabotage needs **one GitHub App** for webhook-driven builds and OAuth login. Creating it requires
your GitHub account, so this is a manual step:

1. GitHub → Settings → Developer settings → GitHub Apps → New GitHub App.
2. **Name**: `Cabotage - homelab` (or similar; must be globally unique).
3. **Homepage URL**: `https://cabotage.apps.z7x.org`
4. **Callback URL**: `https://cabotage.apps.z7x.org/auth/github/callback` (also works as the Setup
   URL — Cabotage branches on `state`). Internal/tailnet-only, fine for a browser OAuth redirect.
5. **Webhook URL**: `https://cabotage.z7x.org/github/hooks` (the one public path, see above).
6. **Webhook secret**: already generated and stored in the vault as
   `vault_cabotage_github_webhook_secret` — run
   `ansible-vault view ansible/inventory/group_vars/all/vault.yml | yq '.vault_cabotage_github_webhook_secret'`
   and paste it into GitHub's "Webhook secret" field (don't generate a new one).
7. **Permissions**: Repository — Contents (read), Metadata (read), Commit statuses (read/write),
   Checks (read/write), Pull requests (read), Webhooks (read); Account — Email addresses (read, if
   using GitHub OAuth login).
8. **Subscribe to events**: Push, Pull request, Release, Check run, Check suite, Status.
9. **Where can this GitHub App be installed?**: Only on this account (or your org), unless you
   want it public.
10. Create the app. Generate a **private key** (downloads a `.pem`). Note the **App ID** and
    generate a **client secret** (OAuth tab).
11. Store the four values in the vault (never print them back out; this prompts interactively
    and writes directly -- avoids `ansible-vault edit`, which opens `$EDITOR` and can silently
    fail to save). Works from any shell, including fish:
    ```sh
    bash terraform/cabotage/set-github-app-secrets.sh
    # prompts for: App ID, OAuth Client ID, OAuth Client secret (hidden), then paste the full
    # .pem private key and press Ctrl-D on its own line.
    ```
    Tell the integrator once this is done -- re-apply and the admin SQL step are handled from
    here.
12. Re-apply so the module picks up `github_app_id` and `tf.sh` materializes the new secret files:
    ```sh
    cd terraform/cabotage
    ./tf.sh apply
    ```
13. Install the GitHub App on the org/account whose repos you want to deploy from.

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
