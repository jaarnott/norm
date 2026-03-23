terraform {
  required_version = ">= 1.5"

  required_providers {
    google = {
      source  = "hashicorp/google"
      version = "~> 5.0"
    }
  }

  backend "gcs" {
    # Configured via -backend-config flags or environments/*/backend.tf
    # Usage: terraform init -backend-config="bucket=norm-tfstate-491101" -backend-config="prefix=testing"
  }
}

provider "google" {
  project = var.project_id
  region  = var.region
}

# ── Enable required APIs ────────────────────────────────────────
resource "google_project_service" "apis" {
  for_each = toset([
    "run.googleapis.com",
    "sqladmin.googleapis.com",
    "secretmanager.googleapis.com",
    "vpcaccess.googleapis.com",
    "artifactregistry.googleapis.com",
    "compute.googleapis.com",
    "dns.googleapis.com",
    "certificatemanager.googleapis.com",
  ])
  service            = each.value
  disable_on_destroy = false
}

# ── Modules ─────────────────────────────────────────────────────
module "networking" {
  source      = "./modules/networking"
  project_id  = var.project_id
  region      = var.region
  environment = var.environment
}

module "database" {
  source           = "./modules/database"
  project_id       = var.project_id
  region           = var.region
  environment      = var.environment
  tier             = var.db_tier
  ha_enabled       = var.db_ha_enabled
  backup_retention = var.db_backup_retention
  disk_size        = var.db_disk_size
  network_id       = module.networking.network_id

  depends_on = [google_project_service.apis, module.networking]
}

module "secrets" {
  source      = "./modules/secrets"
  project_id  = var.project_id
  environment = var.environment

  depends_on = [google_project_service.apis]
}

module "storage" {
  source      = "./modules/storage"
  project_id  = var.project_id
  region      = var.region
  environment = var.environment

  depends_on = [google_project_service.apis]
}

module "cloud_run" {
  source      = "./modules/cloud-run"
  project_id  = var.project_id
  region      = var.region
  environment = var.environment

  api_image   = "${var.region}-docker.pkg.dev/${var.project_id}/norm/norm-api:latest"
  web_image   = "${var.region}-docker.pkg.dev/${var.project_id}/norm/norm-web:latest"

  api_min_instances = var.cloudrun_api_min
  api_max_instances = var.cloudrun_api_max
  web_min_instances = var.cloudrun_web_min
  web_max_instances = var.cloudrun_web_max

  api_cpu    = var.cloudrun_api_cpu
  api_memory = var.cloudrun_api_memory
  web_cpu    = var.cloudrun_web_cpu
  web_memory = var.cloudrun_web_memory

  network_id = module.networking.network_id
  subnet_id  = module.networking.subnet_id
  database_url              = module.database.connection_url
  cloud_sql_connection_name = module.database.connection_name
  secret_ids                = module.secrets.secret_ids

  depends_on = [google_project_service.apis]
}

module "dns" {
  source      = "./modules/dns"
  project_id  = var.project_id
  region      = var.region
  environment = var.environment
  domain      = var.domain

  api_service_name = module.cloud_run.api_service_name
  web_service_name = module.cloud_run.web_service_name

  depends_on = [google_project_service.apis]
}

# ── Outputs ─────────────────────────────────────────────────────
output "api_url" {
  value = module.cloud_run.api_url
}

output "web_url" {
  value = module.cloud_run.web_url
}

output "database_instance" {
  value = module.database.instance_name
}

output "registry_url" {
  value = module.storage.registry_url
}
