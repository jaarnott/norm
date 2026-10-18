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
    "SCHEDULER_SECRET",
    # Fernet key(s) for connector OAuth tokens at rest (app/db/encrypted.py),
    # comma-separated, newest first. One per environment; never in the
    # shared config DB. Set a version BEFORE the API maps it (an empty
    # secret fails the revision), then run scripts/encrypt_connection_tokens.py.
    "TOKEN_ENCRYPTION_KEYS",
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
