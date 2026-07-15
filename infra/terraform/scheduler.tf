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
