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

resource "kubernetes_namespace_v1" "traefik" {
  metadata {
    name = "traefik"
  }
}
module "cabotage" {
  source = "git::https://github.com/cabotage/terraform.git//modules/cabotage?ref=40f5ed1222aed532153cca72365a02fee193066f"

  cluster_identifier       = "homelab"
  kube_context             = local.kube_context
  cabotage_app_image       = "ghcr.io/cabotage/cabotage-app:latest"
  secrets_dir              = abspath("${path.module}/.secrets")
  forwarded_headers_cidrs  = ["10.10.30.0/24", "10.244.0.0/16"]

  consul_datacenter = "homelab"
  proxy_protocol_cidrs     = []

  # --- Single-replica, minikube-style topology (hnk8s03 has only 8GB) ---
  consul_replicas      = 1
  consul_storage_size  = "2Gi"
  vault_replicas       = 1
  rustfs_replicas      = 1
  rustfs_disks_per_replica = 1 # FS mode, no erasure coding
  loki_standalone      = true
  mimir_standalone     = true
  cabotage_postgres_instances = 1

  # kopf (Python) OOMKilled at the upstream default 128Mi limit on this cluster; give it room.
  enrollment_operator_resources = {
    requests = { cpu = "25m", memory = "128Mi" }
    limits   = { cpu = "250m", memory = "320Mi" }
  }
  registry_replicas    = 1
  loki_backend_replicas  = 1
  loki_read_replicas    = 1
  loki_write_replicas   = 1
  mimir_backend_replicas = 1
  mimir_read_replicas   = 1
  mimir_write_replicas  = 1
  cabotage_app_web_replicas         = 1
  cabotage_app_worker_replicas      = 1
  ca_admission_replicas             = 1

  # --- Vault unseal: dev auto-unseal sidecar (accepted risk, per decision) ---
  vault_dev_auto_unseal = true

  # --- Ingress / TLS: homelab's own Traefik LoadBalancer + cert-manager + DNS-01 issuer ---
  traefik_replicas        = 1
  traefik_aws_lb          = false
  traefik_load_balancer   = true
  traefik_host_network    = false
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

  depends_on = [kubernetes_namespace_v1.traefik]
}
