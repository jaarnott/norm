terraform {
  required_providers {
    random = {
      source  = "hashicorp/random"
      version = "~> 3.0"
    }
  }
}

variable "project_id" { type = string }
variable "region" { type = string }
variable "environment" { type = string }
variable "tier" {
  type    = string
  default = "db-f1-micro"
}
variable "ha_enabled" {
  type    = bool
  default = false
}
variable "backup_retention" {
  type    = number
  default = 7
}
variable "disk_size" {
  type    = number
  default = 10
}
variable "network_id" { type = string }

resource "random_password" "db_password" {
  length  = 32
  special = false
}

resource "google_sql_database_instance" "main" {
  name             = "norm-${var.environment}"
  database_version = "POSTGRES_16"
  region           = var.region
  project          = var.project_id

  settings {
    tier              = var.tier
    availability_type = var.ha_enabled ? "REGIONAL" : "ZONAL"
    disk_size         = var.disk_size
    disk_type         = "PD_SSD"
    disk_autoresize   = true

    ip_configuration {
      ipv4_enabled    = false
      private_network = var.network_id
    }

    backup_configuration {
      enabled                        = true
      point_in_time_recovery_enabled = true
      transaction_log_retention_days = var.backup_retention
      backup_retention_settings {
        retained_backups = var.backup_retention
      }
    }

    maintenance_window {
      day          = 7 # Sunday
      hour         = 3 # 3 AM
      update_track = "stable"
    }
  }

  deletion_protection = var.environment == "production"
}

resource "google_sql_database" "norm" {
  name     = "norm"
  instance = google_sql_database_instance.main.name
  project  = var.project_id
}

resource "google_sql_user" "norm" {
  name     = "norm"
  instance = google_sql_database_instance.main.name
  password = random_password.db_password.result
  project  = var.project_id
}

output "instance_name" { value = google_sql_database_instance.main.name }
output "connection_name" { value = google_sql_database_instance.main.connection_name }
output "connection_url" {
  value     = "postgresql://norm:${random_password.db_password.result}@/${google_sql_database.norm.name}?host=/cloudsql/${google_sql_database_instance.main.connection_name}"
  sensitive = true
}
output "db_password" {
  value     = random_password.db_password.result
  sensitive = true
}
