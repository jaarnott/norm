# ── Environment ─────────────────────────────────────────────────
variable "environment" {
  description = "Environment name (testing, staging, production)"
  type        = string
}

variable "project_id" {
  description = "GCP project ID"
  type        = string
}

variable "region" {
  description = "GCP region"
  type        = string
  default     = "australia-southeast1"
}

variable "domain" {
  description = "Domain name for this environment"
  type        = string
}

# ── Database ────────────────────────────────────────────────────
variable "db_tier" {
  description = "Cloud SQL machine tier"
  type        = string
  default     = "db-f1-micro"
}

variable "db_ha_enabled" {
  description = "Enable Cloud SQL high availability"
  type        = bool
  default     = false
}

variable "db_backup_retention" {
  description = "Number of days to retain backups"
  type        = number
  default     = 7
}

variable "db_disk_size" {
  description = "Initial disk size in GB"
  type        = number
  default     = 10
}

variable "db_read_replica_enabled" {
  description = "Enable a read replica"
  type        = bool
  default     = false
}

variable "db_cross_region_backup" {
  description = "Cross-region backup location (e.g. australia-southeast2). Empty to disable."
  type        = string
  default     = ""
}

# ── Cloud Run ───────────────────────────────────────────────────
variable "cloudrun_api_min" {
  description = "Minimum API instances"
  type        = number
  default     = 0
}

variable "cloudrun_api_max" {
  description = "Maximum API instances"
  type        = number
  default     = 2
}

variable "cloudrun_web_min" {
  description = "Minimum Web instances"
  type        = number
  default     = 0
}

variable "cloudrun_web_max" {
  description = "Maximum Web instances"
  type        = number
  default     = 2
}

variable "cloudrun_api_cpu" {
  description = "API CPU limit"
  type        = string
  default     = "2"
}

variable "cloudrun_api_memory" {
  description = "API memory limit"
  type        = string
  default     = "2Gi"
}

variable "cloudrun_web_cpu" {
  description = "Web CPU limit"
  type        = string
  default     = "1"
}

variable "cloudrun_web_memory" {
  description = "Web memory limit"
  type        = string
  default     = "512Mi"
}
