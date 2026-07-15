# ── Automated-task scheduler ────────────────────────────────────
# Cloud Scheduler drives execution of AutomatedTasks by calling the API's
# /internal/run-due-tasks endpoint on a fixed cadence. The endpoint atomically
# claims and runs any task whose next_run_at is due. This replaces the old
# in-process APScheduler, which was unreliable under gunicorn workers + Cloud
# Run autoscaling.
#
# Auth: the endpoint is gated by a shared secret sent as a request header. The
# secret is internal-only (nothing outside this config needs to know it), so we
# generate it here and store it in Secret Manager. Cloud Run reads it as the
# SCHEDULER_SECRET env var (see the cloud-run module) and Cloud Scheduler sends
# it as the X-Scheduler-Secret header.

resource "random_password" "scheduler_secret" {
  length  = 40
  special = false
}

resource "google_secret_manager_secret_version" "scheduler_secret" {
  secret      = "projects/${var.project_id}/secrets/SCHEDULER_SECRET"
  secret_data = random_password.scheduler_secret.result

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
      "X-Scheduler-Secret" = random_password.scheduler_secret.result
      "Content-Type"       = "application/json"
    }
  }

  depends_on = [
    google_project_service.apis,
    google_secret_manager_secret_version.scheduler_secret,
    module.cloud_run,
  ]
}
