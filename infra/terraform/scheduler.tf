# ── Cloud Scheduler jobs ────────────────────────────────────────
#
# These are fully environment-parameterised, but as of writing they exist in
# **production only**. Testing and staging run the same code — which no longer
# has an in-process scheduler — so until these are applied there, automated
# tasks in those environments never fire and their OAuth tokens are never kept
# alive.
#
# To apply to testing/staging (needs credentials for those projects, which the
# github-deploy SA does not have):
#
#   1. Ensure a SCHEDULER_SECRET secret exists in the target project with a
#      value — the data source below reads it, and the API rejects every
#      request when it's unset (fail-closed). The secrets module creates the
#      container; the value must be added separately:
#        openssl rand -hex 32 | gcloud secrets versions add SCHEDULER_SECRET \
#          --data-file=- --project=norm-testing
#   2. terraform init -reconfigure -backend-config="bucket=norm-tfstate-491101" \
#        -backend-config="prefix=testing"
#   3. terraform plan -var-file=environments/testing/terraform.tfvars
#   4. **Read the plan before applying.** Production's config had drifted far
#      from live and a naive apply would have replaced the database and stripped
#      Cloud Run env vars. Expect only the three jobs below to be created; if the
#      plan wants to change or replace anything else, stop and reconcile first
#      (see the lifecycle ignore_changes in modules/cloud-run/main.tf).
#
# ── Automated-task scheduler ────────────────────────────────────
# Cloud Scheduler drives execution of AutomatedTasks by calling the API's
# /internal/run-due-tasks endpoint on a fixed cadence. The endpoint atomically
# claims and runs any task whose next_run_at is due. This replaces the old
# in-process APScheduler, which was unreliable under gunicorn workers + Cloud
# Run autoscaling.
#
# Auth: the endpoint is gated by a shared secret sent as a request header. The
# SCHEDULER_SECRET secret + its value are created out-of-band (Secret Manager);
# we read the current version here to configure the Cloud Scheduler header so
# Terraform never rotates it. The secret container itself is declared in the
# secrets module.

data "google_secret_manager_secret_version" "scheduler_secret" {
  secret  = "SCHEDULER_SECRET"
  project = var.project_id
  version = "latest"

  depends_on = [module.secrets]
}

resource "google_cloud_scheduler_job" "run_due_tasks" {
  name      = "norm-run-due-tasks-${var.environment}"
  project   = var.project_id
  region    = var.region
  schedule  = "* * * * *" # every minute
  time_zone = "Etc/UTC"

  attempt_deadline = "60s"

  retry_config {
    retry_count = 1
  }

  http_target {
    http_method = "POST"
    uri         = "${module.cloud_run.api_url}/internal/run-due-tasks"

    headers = {
      "X-Scheduler-Secret" = data.google_secret_manager_secret_version.scheduler_secret.secret_data
      "Content-Type"       = "application/json"
    }
  }

  depends_on = [
    google_project_service.apis,
    module.cloud_run,
  ]
}

# ── OAuth token keep-alive ──────────────────────────────────────
# LoadedHub rotates refresh tokens, and a rotation is the only thing that resets
# the refresh token's lifetime. Lazy refresh only happens when a task actually
# calls the connector, so an idle connector's refresh token silently expires and
# locks us out (requiring a manual reconnect). This keeps tokens alive on a
# cadence, independent of whether any task ran.
#
# Every 6h rather than every minute: the endpoint is a no-op for tokens that are
# still valid, and the short access-token lifetime sets the real rotation cadence.
# ── Config drift check ──────────────────────────────────────────
# Connector specs, agent prompts and model selections live in the database and
# are edited through the Settings UI. CI cannot see any of it (it runs against a
# throwaway Postgres with zero rows), and a bad edit lands with no code change
# and no deploy — so there is nothing to review and nothing to fail.
#
# Every incident so far lived in that blind spot: a retired model id sitting in
# connector_configs, a consolidator left on a deleted executor's format. This is
# the only thing that watches for it. Daily is enough — config changes at human
# pace, and failures are latent rather than urgent.
resource "google_cloud_scheduler_job" "validate_config" {
  name      = "norm-validate-config-${var.environment}"
  project   = var.project_id
  region    = var.region
  schedule  = "0 19 * * *" # daily, 07:00 NZ
  time_zone = "Etc/UTC"

  attempt_deadline = "120s"

  retry_config {
    retry_count = 1
  }

  http_target {
    http_method = "POST"
    uri         = "${module.cloud_run.api_url}/internal/validate-config"

    headers = {
      "X-Scheduler-Secret" = data.google_secret_manager_secret_version.scheduler_secret.secret_data
      "Content-Type"       = "application/json"
    }
  }

  depends_on = [
    google_project_service.apis,
    module.cloud_run,
  ]
}

resource "google_cloud_scheduler_job" "refresh_tokens" {
  name      = "norm-refresh-tokens-${var.environment}"
  project   = var.project_id
  region    = var.region
  schedule  = "0 */6 * * *" # every 6 hours
  time_zone = "Etc/UTC"

  attempt_deadline = "180s"

  retry_config {
    retry_count = 1
  }

  http_target {
    http_method = "POST"
    uri         = "${module.cloud_run.api_url}/internal/refresh-tokens"

    headers = {
      "X-Scheduler-Secret" = data.google_secret_manager_secret_version.scheduler_secret.secret_data
      "Content-Type"       = "application/json"
    }
  }

  depends_on = [
    google_project_service.apis,
    module.cloud_run,
  ]
}
