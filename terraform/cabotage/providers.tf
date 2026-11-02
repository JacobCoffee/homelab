locals {
  kubeconfig_path = abspath("${path.module}/../../kubernetes/kubeconfig")
  kube_context    = "admin@homelab"
}

provider "kubernetes" {
  config_path    = local.kubeconfig_path
  config_context = local.kube_context
}

provider "helm" {
  kubernetes = {
    config_path    = local.kubeconfig_path
    config_context = local.kube_context
  }
}

provider "kubectl" {
  config_path    = local.kubeconfig_path
  config_context = local.kube_context
  load_config_file = true
}
