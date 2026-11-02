variable "github_app_id" {
  description = <<-EOT
    GitHub App ID for Cabotage webhook-driven builds + OAuth login, from
    `vault_cabotage_github_app_id` (scripts/github-app.py cabotage); tf.sh exports it.
  EOT
  type    = string
  default = ""
}

variable "tailscale_oauth_client_id" {
  description = "Tailscale OAuth client (tag:cabotage-operator) for the operator; vault_cabotage_tailscale_oauth_client_id via tf.sh."
  type        = string
  default     = ""
}

variable "tailscale_oauth_client_secret" {
  description = "Secret for tailscale_oauth_client_id; vault_cabotage_tailscale_oauth_client_secret via tf.sh."
  type        = string
  default     = ""
  sensitive   = true
}

variable "discord_client_id" {
  description = "Discord app client ID for Cabotage notifications; vault_cabotage_discord_client_id via tf.sh."
  type        = string
  default     = ""
}

variable "discord_client_secret" {
  description = "Discord app client secret; vault_cabotage_discord_client_secret via tf.sh."
  type        = string
  default     = ""
  sensitive   = true
}

variable "discord_bot_token" {
  description = "Discord bot token; vault_cabotage_discord_bot_token via tf.sh. Empty leaves Discord off."
  type        = string
  default     = ""
  sensitive   = true
}
