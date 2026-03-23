variable "project_id" { type = string }
variable "region" { type = string }
variable "environment" { type = string }

resource "google_artifact_registry_repository" "docker" {
  location      = var.region
  repository_id = "norm"
  format        = "DOCKER"
  project       = var.project_id
  description   = "Docker images for Norm ${var.environment}"
}

resource "google_storage_bucket" "test_artifacts" {
  name          = "norm-${var.environment}-test-artifacts"
  location      = var.region
  project       = var.project_id
  force_destroy = true

  uniform_bucket_level_access = true

  lifecycle_rule {
    condition {
      age = 30
    }
    action {
      type = "Delete"
    }
  }
}

output "registry_url" {
  value = "${var.region}-docker.pkg.dev/${var.project_id}/${google_artifact_registry_repository.docker.repository_id}"
}
