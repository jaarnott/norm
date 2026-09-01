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
  # Every 5 minutes, not every minute. The endpoint only claims tasks that are
  # already due, so the cadence is the worst-case lateness of a task, not its
  # accuracy — and at one task a day, a per-minute poll was 43,200 invocations
  # a month to do 30 seconds of work. A task now fires up to 5 minutes late;
  # tighten this if a sub-5-minute schedule is ever needed.
  schedule  = "*/5 * * * *"
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
# Every run performs a REAL refresh-token redemption for every connector row
# (force=True in refresh_all_tokens), and each successful redemption makes
# LoadedHub mint a fresh full-lifetime refresh token. That rotation is the only
# thing that resets the refresh token's lifetime — and LoadedHub's refresh
# tokens for our client live only ~24 HOURS (Aug-2026 incident: a fresh grant
# was dead 26.4h after mint; access tokens live ~14 days, which hid this).
#
# So this cadence IS the connection's lifeline: it must stay comfortably under
# the ~24h refresh lifetime or every venue needs a manual reconnect. 15 minutes
# gives ~96x margin and makes a dead grant visible in the logs within minutes
# of a reconnect rather than hours.
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
  schedule  = "*/15 * * * *" # every 15 minutes — must beat the ~24h refresh lifetime
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
