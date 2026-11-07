# Cabotage PaaS on the homelab cluster.
#
# Consumes the upstream cabotage/terraform module from git, pinned to the commit SHA of a draft
# PR branch (https://github.com/cabotage/terraform/pull/21) that adds four homelab-required,
# backward-compatible toggles (all default to current upstream behavior):
#   - enable_cert_manager        (false here: reuse the homelab's existing cert-manager 1.21)
#   - enable_coredns_hardening   (false here: CoreDNS is Talos-managed, don't let this module touch it)
#   - enable_letsencrypt_issuer  (false here: the homelab's own Flux-managed ClusterIssuer named
#                                  "letsencrypt" is used instead, see kubernetes/apps/cert-manager/)
#   - traefik_service_annotations (used to pin Traefik's LoadBalancer IP via Cilium LB-IPAM)
# Also includes a fifth commit on the same branch fixing a real upstream bug: the intermediate-CA
# signing scripts omitted subjectKeyIdentifier/authorityKeyIdentifier extensions, which modern
# OpenSSL (Python 3.14's ssl module) rejects with CERTIFICATE_VERIFY_FAILED: Missing Authority Key
# Identifier, and which breaks Vault PKI CRL building. Discovered live when cabotage-app couldn't
# TLS-connect to Vault.
#
# Once PR #21 merges to cabotage/terraform main, re-pin this `ref` to the merge commit on main.
#
# Renovate flags this ref as a pending major-version bump (it isn't a semver tag). Checked
# 2026-11-02: the latest tag, v1.9.3 (commit a1bca12), is an ancestor of this pin, not a
# descendant -- it predates the toggles/CA-fix commits above and has no newer release since.
# Re-pinning to v1.9.3 would regress this deployment. No bump available until PR #21 merges or a
# release ships past it; re-check then.

resource "kubernetes_namespace_v1" "traefik" {
  metadata {
    name = "traefik"
  }
}
module "cabotage" {
  source = "git::https://github.com/cabotage/terraform.git//modules/cabotage?ref=0af4603f636ad0583ac9ec64699a546f8b77ed87"

  cluster_identifier      = "homelab"
  kube_context            = local.kube_context
  cabotage_app_image      = "ghcr.io/cabotage/cabotage-app:latest"
  secrets_dir             = abspath("${path.module}/.secrets")
  forwarded_headers_cidrs = ["10.10.30.0/24", "10.244.0.0/16"]

  consul_datacenter    = "homelab"
  proxy_protocol_cidrs = []

  # --- Single-replica, minikube-style topology (hnk8s03 has only 8GB) ---
  consul_replicas          = 1
  consul_storage_size      = "2Gi"
  vault_replicas           = 1
  rustfs_replicas          = 1
  rustfs_disks_per_replica = 1 # FS mode, no erasure coding
  # Registry images + 30-day Postgres WAL/base backups (platform and tenants) + resident Loki/Mimir.
  rustfs_storage_size         = "50Gi"
  loki_standalone             = true
  mimir_standalone            = true
  cabotage_postgres_instances = 1

  # kopf (Python) OOMKilled at the upstream default 128Mi limit on this cluster; give it room.
  enrollment_operator_resources = {
    requests = { cpu = "25m", memory = "128Mi" }
    limits   = { cpu = "250m", memory = "320Mi" }
  }
  # Same kopf runtime: OOMKilled at the upstream 128Mi limit right after Tailscale was enabled.
  tailscale_operator_manager_resources = {
    requests = { cpu = "25m", memory = "128Mi" }
    limits   = { cpu = "250m", memory = "320Mi" }
  }
  registry_replicas            = 1
  loki_backend_replicas        = 1
  loki_read_replicas           = 1
  loki_write_replicas          = 1
  mimir_backend_replicas       = 1
  mimir_read_replicas          = 1
  mimir_write_replicas         = 1
  cabotage_app_web_replicas    = 1
  cabotage_app_worker_replicas = 1
  ca_admission_replicas        = 1

  # --- Vault unseal: dev auto-unseal sidecar (accepted risk, per decision) ---
  vault_dev_auto_unseal = true

  # --- Ingress / TLS: homelab's own Traefik LoadBalancer + cert-manager + DNS-01 issuer ---
  traefik_replicas      = 1
  traefik_aws_lb        = false
  traefik_load_balancer = true
  traefik_host_network  = false
  traefik_service_annotations = {
    "lbipam.cilium.io/ips" = "10.10.30.202"
  }
  node_cidr = "10.10.30.0/24"

  enable_cert_manager       = false # homelab's cert-manager 1.21 (Flux-managed) is reused
  enable_coredns_hardening  = false # CoreDNS is Talos-managed; don't let this module patch it
  enable_letsencrypt_issuer = false # homelab's own "letsencrypt" ClusterIssuer is used (DNS-01)
  enable_pebble_letsencrypt = false # irrelevant once enable_letsencrypt_issuer=false, kept false for clarity
  acme_email                = "admin@z7x.org"

  # --- Hostnames ---
  cabotage_app_hostname   = "cabotage.apps.z7x.org"
  cabotage_ingress_domain = "apps.z7x.org"
  registry_verify         = "/var/run/secrets/cabotage.io/ca.crt"

  # --- GitHub App (values come from the Ansible vault once the user creates the app; see README) ---
  github_app_id             = var.github_app_id
  github_oauth_only         = false
  github_oauth_allowed_orgs = ""

  security_confirmable = false

  # Closed instance: no signup form, and GitHub sign-in only logs in existing users (a new GitHub
  # account gets "registration is closed"; with GITHUB_OAUTH_ONLY=False that check applies). No
  # org requests. Cost estimates off: their rates are AWS on-demand prices, meaningless here.
  # Cluster switcher (admins only, the app default): this install plus the PSF ones; each finds
  # itself by request host. Discord notifications switch on once the vault holds the Discord app
  # (README "Discord"); the module has no Secret for these, so they ride in cabotage-config.
  cabotage_app_extra_config = merge(
    {
      CABOTAGE_SECURITY_REGISTERABLE         = "False"
      CABOTAGE_ORGANIZATION_REQUESTS_ENABLED = "False"
      CABOTAGE_CLUSTERS = jsonencode([
        { name = "homelab", url = "https://cabotage.apps.z7x.org", color = "emerald" },
        { name = "psfhosted", url = "https://cabotage.us-east-2.psfhosted.computer", region = "us-east-2", color = "blue" },
        { name = "pypi", url = "https://cabotage.us-east-2.pypi.io", region = "us-east-2", color = "rose" },
        { name = "psf.dev", url = "https://platform.psf.dev", region = "us-east-2", color = "teal" },
        { name = "pypi.dev", url = "https://platform.pypi.dev", region = "us-east-2", color = "amber" },
        { name = "prime", url = "https://preview.cabotage.io", color = "purple" },
      ])
    },
    var.discord_bot_token == "" ? {} : {
      CABOTAGE_DISCORD_CLIENT_ID     = var.discord_client_id
      CABOTAGE_DISCORD_CLIENT_SECRET = var.discord_client_secret
      CABOTAGE_DISCORD_BOT_TOKEN     = var.discord_bot_token
    },
  )

  # Tenant apps can be exposed on the homelab tailnet. The operator logs in with a static OAuth
  # client (tag:cabotage-operator, created via the Tailscale API; tags defined in
  # ansible/roles/tailscale_tailnet). No Funnel ingress: the UI stays tailnet-only.
  enable_tailscale              = true
  tailscale_operator_hostname   = "cabotage-operator"
  tailscale_oauth_client_id     = var.tailscale_oauth_client_id
  tailscale_oauth_client_secret = var.tailscale_oauth_client_secret
  # The module defaults to Ee's fork images, which are arm64-only (EKS Graviton); these nodes are
  # amd64. Upstream ships the same multi-tailnet support: at v1.98.9 its Tailnet, ProxyGroup and
  # ProxyGroupPolicy CRDs are field-for-field identical to the fork's ones the module applies.
  # Stay on the module's chart (1.94.2): charts from 1.96 bundle those CRDs and collide with the
  # module's copies. 1.98.x is the newest operator needing no CRD beyond them (1.102 adds
  # PeerRelay); the one RBAC gap it has is granted below.
  tailscale_operator_image     = "tailscale/k8s-operator"
  tailscale_operator_image_tag = "v1.98.9"
  tailscale_proxy_image        = "tailscale/tailscale"
  tailscale_proxy_image_tag    = "v1.98.9"

  depends_on = [kubernetes_namespace_v1.traefik]
}

# Upstream operators >= 1.96 manage ValidatingAdmissionPolicies for ProxyGroupPolicy; neither the
# 1.94.2 chart's RBAC nor the module's supplement ClusterRole grants that.
resource "kubernetes_cluster_role_v1" "tailscale_operator_vap" {
  metadata {
    name = "cabotage-tailscale-operator-vap"
  }
  rule {
    api_groups = ["admissionregistration.k8s.io"]
    resources  = ["validatingadmissionpolicies", "validatingadmissionpolicybindings"]
    verbs      = ["get", "list", "watch", "create", "update", "delete"]
  }
}

resource "kubernetes_cluster_role_binding_v1" "tailscale_operator_vap" {
  metadata {
    name = "cabotage-tailscale-operator-vap"
  }
  role_ref {
    api_group = "rbac.authorization.k8s.io"
    kind      = "ClusterRole"
    name      = kubernetes_cluster_role_v1.tailscale_operator_vap.metadata[0].name
  }
  subject {
    kind      = "ServiceAccount"
    name      = "operator" # the tailscale-operator chart's ServiceAccount
    namespace = "tailscale"
  }
  depends_on = [module.cabotage]
}

# The module's tailscale-namespace policies allow egress on 443/8443 to 0.0.0.0/0, which reaches
# EKS's external API endpoint. Here the kubernetes Service lands on the Talos control planes at
# :6443, which Cilium treats as the kube-apiserver entity, not a world CIDR -- so the operator
# (and the proxies it creates, which keep state in Secrets) hung before their first log line.
resource "kubernetes_manifest" "tailscale_allow_apiserver" {
  manifest = {
    apiVersion = "cilium.io/v2"
    kind       = "CiliumNetworkPolicy"
    metadata = {
      name      = "allow-egress-kube-apiserver"
      namespace = "tailscale"
    }
    spec = {
      endpointSelector = {}
      egress           = [{ toEntities = ["kube-apiserver"] }]
    }
  }
  depends_on = [module.cabotage]
}

# --- Redis startup probe tuning ---
#
# Root cause of redis/cabotage-0's ~1830-restart CrashLoopBackOff: the redis-operator's default
# liveness/readiness probes (exec redis-cli ping, delay=0s timeout=1s period=10s
# failureThreshold=3) give Redis roughly a 30s budget from the moment the container starts to
# answer PONG. On hnk8s04 (the memory-poor worker whose system disk -- and this PVC's
# proxmox-vmdata storage class -- was live-moved from the NVMe pool to the slower SATA pool on
# 2026-11-04) a cold start spends ~28s blocked loading the AOF (RDB base loads instantly with 0
# keys; the time goes into replaying/reading the incremental AOF file on that slower disk, per
# `kubectl logs --previous`: "DB loaded from base file ... 0.000 seconds" followed by a ~28s gap
# before the next log line). That reliably blows the ~30s probe budget every single restart,
# kubelet SIGTERMs the still-loading process ("Received shutdown signal during loading,
# scheduling shutdown"), and the cycle repeats forever. cabotage-app-worker and
# cabotage-app-worker-beat crashloop too, but only as a consequence: both fail immediately with
# `No route to host` connecting to cabotage.redis.svc.cluster.local:6379 because the redis
# Service has no Ready endpoints -- no separate fix needed there once redis stabilizes.
#
# The module doesn't expose probe tuning as a variable (manifests/redis/redis.yaml.tftpl has no
# readinessProbe/livenessProbe keys), so patch the Redis CR in place via server-side apply: a
# dedicated field manager owns only spec.readinessProbe/spec.livenessProbe, leaving every other
# field (including resources) under the module's own apply untouched and unconflicted regardless
# of apply order. The redis-operator auto-fills the exec command (redis-cli ... ping) for any
# Probe that has no Exec/HTTPGet/TCPSocket/GRPC set, so only the timing fields are needed.
resource "kubectl_manifest" "redis_probe_tuning" {
  yaml_body = yamlencode({
    apiVersion = "redis.redis.opstreelabs.in/v1beta2"
    kind       = "Redis"
    metadata = {
      name      = "cabotage"
      namespace = "redis"
    }
    spec = {
      # 30s initial delay covers a cold-start AOF load on the slow disk; timeout bumped from 1s
      # to 5s so a CPU-throttled redis-cli + TLS handshake under the 25m CPU limit doesn't itself
      # trip the probe; failureThreshold 6 over a 10s period adds another minute of tolerance
      # before kubelet gives up and restarts the container.
      readinessProbe = {
        initialDelaySeconds = 30
        timeoutSeconds      = 5
        periodSeconds       = 10
        failureThreshold    = 6
      }
      livenessProbe = {
        initialDelaySeconds = 30
        timeoutSeconds      = 5
        periodSeconds       = 10
        failureThreshold    = 6
      }
    }
  })

  server_side_apply = true
  field_manager      = "homelab-redis-probe-tuning"
  force_conflicts    = true

  depends_on = [module.cabotage]
}

# --- Redis zone pin (hnhv01) ---
#
# redis/cabotage-cabotage-0's PVC was provisioned in zone hnhv02 (proxmox-vmdata is
# WaitForFirstConsumer, so the zone is whichever Proxmox host the pod first landed on), which
# limits scheduling to hnk8s03 (control plane, etcd) or hnk8s04 (2 CPU/3GB, the slow-disk
# worker already blamed above for the AOF-replay crashloop). Neither is a good home: the first
# starves a control plane under the new PreferNoSchedule taint
# (ansible/roles/talos/templates/machine.yaml.j2) once something else wants the capacity, the
# second is where this exact Redis previously crashlooped on disk I/O. Pin the pod to the
# hnhv01 Proxmox host (hnk8s01/02/05, all NVMe-backed) via nodeSelector; the live PVC is
# migrated to that zone out-of-band (backup, delete, let WaitForFirstConsumer reprovision
# there, restore) alongside this change so the pod can actually schedule once applied.
resource "kubectl_manifest" "redis_zone_pin" {
  yaml_body = yamlencode({
    apiVersion = "redis.redis.opstreelabs.in/v1beta2"
    kind       = "Redis"
    metadata = {
      name      = "cabotage"
      namespace = "redis"
    }
    spec = {
      nodeSelector = {
        "topology.kubernetes.io/zone" = "hnhv01"
      }
    }
  })

  server_side_apply = true
  field_manager      = "homelab-redis-zone-pin"
  force_conflicts    = true

  depends_on = [module.cabotage]
}
