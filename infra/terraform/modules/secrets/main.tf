variable "project_id" { type = string }
variable "environment" { type = string }

locals {
  secret_names = [
    "JWT_SECRET",
    "DATABASE_URL",
    "ANTHROPIC_API_KEY",
    "STRIPE_SECRET_KEY",
    "STRIPE_WEBHOOK_SECRET",
    "SENTRY_DSN",
  ]
}

resource "google_secret_manager_secret" "secrets" {
  for_each  = toset(local.secret_names)
  secret_id = each.value
  project   = var.project_id

  replication {
    auto {}
  }
}

output "secret_ids" {
  value = { for name, secret in google_secret_manager_secret.secrets : name => secret.secret_id }
}
