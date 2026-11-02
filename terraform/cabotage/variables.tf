variable "github_app_id" {
  description = <<-EOT
    GitHub App ID for Cabotage webhook-driven builds + OAuth login. Empty until the user creates
    the GitHub App (manual step, see README.md) and sets `vault_cabotage_github_app_id` in the
    Ansible vault; tf.sh passes it through as -var.
  EOT
  type    = string
  default = ""
}
