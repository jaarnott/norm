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
variable "read_replica_enabled" {
  type    = bool
  default = false
}
variable "cross_region_backup" {
  type    = string
  default = ""
  description = "If set, enables cross-region backup to this location (e.g. australia-southeast2)"
}

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
      transaction_log_retention_days = min(var.backup_retention, 7)
      backup_retention_settings {
        retained_backups = var.backup_retention
      }
      location = var.cross_region_backup != "" ? var.cross_region_backup : null
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

# ── Read replica (production only) ────────────────────────────────
resource "google_sql_database_instance" "read_replica" {
  count                = var.read_replica_enabled ? 1 : 0
  name                 = "norm-${var.environment}-replica"
  master_instance_name = google_sql_database_instance.main.name
  database_version     = "POSTGRES_16"
  region               = var.region
  project              = var.project_id

  replica_configuration {
    failover_target = false
  }

  settings {
    tier            = var.tier
    disk_autoresize = true
    disk_type       = "PD_SSD"

    ip_configuration {
      ipv4_enabled    = false
      private_network = var.network_id
    }
  }

  deletion_protection = false
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
output "read_replica_connection_name" {
  value = var.read_replica_enabled ? google_sql_database_instance.read_replica[0].connection_name : ""
}
