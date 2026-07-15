variable "project_id" { type = string }
variable "region" { type = string }
variable "environment" { type = string }
variable "api_image" { type = string }
variable "web_image" { type = string }
variable "api_min_instances" {
  type    = number
  default = 0
}
variable "api_max_instances" {
  type    = number
  default = 10
}
variable "web_min_instances" {
  type    = number
  default = 0
}
variable "web_max_instances" {
  type    = number
  default = 10
}
variable "api_cpu" {
  type    = string
  default = "1"
}
variable "api_memory" {
  type    = string
  default = "512Mi"
}
variable "web_cpu" {
  type    = string
  default = "1"
}
variable "web_memory" {
  type    = string
  default = "512Mi"
}
variable "vpc_connector_id" {
  type    = string
  default = ""
}
variable "subnet_id" {
  type    = string
  default = ""
}
variable "network_id" {
  type    = string
  default = ""
}
variable "database_url" {
  type      = string
  sensitive = true
}
variable "secret_ids" {
  type = map(string)
}
variable "cloud_sql_connection_name" {
  type    = string
  default = ""
}

# ---------- API Service ----------

resource "google_cloud_run_v2_service" "api" {
  name     = "norm-api-${var.environment}"
  location = var.region
  project  = var.project_id

  template {
    scaling {
      min_instance_count = var.api_min_instances
      max_instance_count = var.api_max_instances
    }

    vpc_access {
      network_interfaces {
        network    = var.network_id
        subnetwork = var.subnet_id
      }
      egress = "PRIVATE_RANGES_ONLY"
    }

    volumes {
      name = "cloudsql"
      cloud_sql_instance {
        instances = [var.cloud_sql_connection_name]
      }
    }

    containers {
      image = var.api_image

      resources {
        limits = {
          cpu    = var.api_cpu
          memory = var.api_memory
        }
        # CPU must stay allocated when no request is in flight: scheduled tasks
        # execute in a background thread after /internal/run-due-tasks returns,
        # and CPU throttling would freeze that thread mid-run.
        cpu_idle = false
      }

      volume_mounts {
        name       = "cloudsql"
        mount_path = "/cloudsql"
      }

      env {
        name  = "ENVIRONMENT"
        value = var.environment
      }

      env {
        name  = "CORS_ALLOWED_ORIGINS"
        value = "https://${var.environment == "production" ? "" : "${var.environment}."}bettercallnorm.com"
      }

      env {
        name = "DATABASE_URL"
        value_source {
          secret_key_ref {
            secret  = var.secret_ids["DATABASE_URL"]
            version = "latest"
          }
        }
      }

      env {
        name = "JWT_SECRET"
        value_source {
          secret_key_ref {
            secret  = var.secret_ids["JWT_SECRET"]
            version = "latest"
          }
        }
      }

      env {
        name = "ANTHROPIC_API_KEY"
        value_source {
          secret_key_ref {
            secret  = var.secret_ids["ANTHROPIC_API_KEY"]
            version = "latest"
          }
        }
      }

      env {
        name = "STRIPE_SECRET_KEY"
        value_source {
          secret_key_ref {
            secret  = var.secret_ids["STRIPE_SECRET_KEY"]
            version = "latest"
          }
        }
      }

      env {
        name = "STRIPE_WEBHOOK_SECRET"
        value_source {
          secret_key_ref {
            secret  = var.secret_ids["STRIPE_WEBHOOK_SECRET"]
            version = "latest"
          }
        }
      }

      env {
        name = "SCHEDULER_SECRET"
        value_source {
          secret_key_ref {
            secret  = var.secret_ids["SCHEDULER_SECRET"]
            version = "latest"
          }
        }
      }

      ports {
        container_port = 8000
      }
    }

    timeout = "300s"
  }

  # The image and runtime env vars are managed out-of-band by the deploy
  # pipeline and operational gcloud updates, not by Terraform. Without this,
  # an apply would revert the running image and strip env vars (e.g.
  # CONFIG_DATABASE_URL) that Terraform doesn't declare, breaking the service.
  lifecycle {
    ignore_changes = [
      client,
      client_version,
      template[0].containers[0].image,
      template[0].containers[0].env,
      # The norm-config Cloud SQL instance is mounted operationally (it lives
      # outside this stack), so Terraform must not manage the volume set.
      template[0].volumes,
    ]
  }
}

resource "google_cloud_run_v2_service_iam_member" "api_public" {
  project  = var.project_id
  location = var.region
  name     = google_cloud_run_v2_service.api.name
  role     = "roles/run.invoker"
  member   = "allUsers"
}

# ---------- Web Service ----------

resource "google_cloud_run_v2_service" "web" {
  name     = "norm-web-${var.environment}"
  location = var.region
  project  = var.project_id

  template {
    scaling {
      min_instance_count = var.web_min_instances
      max_instance_count = var.web_max_instances
    }

    containers {
      image = var.web_image

      resources {
        limits = {
          cpu    = var.web_cpu
          memory = var.web_memory
        }
      }

      ports {
        container_port = 3000
      }
    }
  }

  # Image + env are managed by the deploy pipeline, not Terraform (see the API
  # service above for rationale).
  lifecycle {
    ignore_changes = [
      client,
      client_version,
      template[0].containers[0].image,
      template[0].containers[0].env,
    ]
  }
}

resource "google_cloud_run_v2_service_iam_member" "web_public" {
  project  = var.project_id
  location = var.region
  name     = google_cloud_run_v2_service.web.name
  role     = "roles/run.invoker"
  member   = "allUsers"
}

# ---------- Outputs ----------

output "api_url" { value = google_cloud_run_v2_service.api.uri }
output "web_url" { value = google_cloud_run_v2_service.web.uri }
output "api_service_name" { value = google_cloud_run_v2_service.api.name }
output "web_service_name" { value = google_cloud_run_v2_service.web.name }
