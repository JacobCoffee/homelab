terraform {
  required_version = ">= 1.8"

  required_providers {
    kubernetes = {
      source  = "hashicorp/kubernetes"
      version = ">= 2.20"
    }
    helm = {
      source  = "hashicorp/helm"
      version = ">= 2.10"
    }
    kubectl = {
      source  = "alekc/kubectl"
      version = ">= 2.1"
    }
    random = {
      source  = "hashicorp/random"
      version = ">= 3.5"
    }
    null = {
      source  = "hashicorp/null"
      version = ">= 3.2"
    }
    local = {
      source  = "hashicorp/local"
      version = ">= 2.5"
    }
  }

  # Remote state in Cloudflare R2 (S3-compatible). Credentials come from the Ansible vault at
  # plan/apply time via tf.sh, never committed here. R2 requires skip_* lies because it isn't AWS
  # and doesn't support the checks the S3 backend normally runs against a real AWS region.
  backend "s3" {
    bucket                      = "homelab-tfstate"
    key                         = "cabotage/terraform.tfstate"
    region                      = "auto"
    endpoints                   = { s3 = "https://0346e28e1f2af422c32c0c37e9f7f363.r2.cloudflarestorage.com" }
    use_path_style              = true
    skip_credentials_validation = true
    skip_region_validation      = true
    skip_requesting_account_id  = true
    skip_s3_checksum            = true
    skip_metadata_api_check     = true
  }
}
