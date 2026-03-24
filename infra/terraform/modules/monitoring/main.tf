variable "project_id" { type = string }
variable "environment" { type = string }
variable "alert_email" { type = string }
variable "api_service_name" { type = string }
variable "web_service_name" { type = string }
variable "database_instance" { type = string }

variable "enable_alerts" {
  description = "Only enable alert policies on staging + production"
  type        = bool
  default     = false
}

# ── Notification channel ────────────────────────────────────────

resource "google_monitoring_notification_channel" "email" {
  project      = var.project_id
  display_name = "Norm ${var.environment} alerts"
  type         = "email"
  labels = {
    email_address = var.alert_email
  }
}

# ── Uptime checks ──────────────────────────────────────────────

resource "google_monitoring_uptime_check_config" "api_health" {
  project      = var.project_id
  display_name = "norm-api-${var.environment} health"
  timeout      = "10s"
  period       = "60s"

  http_check {
    path         = "/health"
    port         = 443
    use_ssl      = true
    validate_ssl = true
  }

  monitored_resource {
    type = "uptime_url"
    labels = {
      project_id = var.project_id
      host       = "${var.api_service_name}-${data.google_project.current.number}.australia-southeast1.run.app"
    }
  }
}

resource "google_monitoring_uptime_check_config" "web_health" {
  project      = var.project_id
  display_name = "norm-web-${var.environment} health"
  timeout      = "10s"
  period       = "60s"

  http_check {
    path         = "/"
    port         = 443
    use_ssl      = true
    validate_ssl = true
  }

  monitored_resource {
    type = "uptime_url"
    labels = {
      project_id = var.project_id
      host       = "${var.web_service_name}-${data.google_project.current.number}.australia-southeast1.run.app"
    }
  }
}

data "google_project" "current" {
  project_id = var.project_id
}

# ── Alert: API error rate > 5% ─────────────────────────────────

resource "google_monitoring_alert_policy" "api_error_rate" {
  count        = var.enable_alerts ? 1 : 0
  project      = var.project_id
  display_name = "[${var.environment}] API error rate > 5%"
  combiner     = "OR"

  conditions {
    display_name = "Cloud Run 5xx error rate"
    condition_threshold {
      filter = <<-EOT
        resource.type = "cloud_run_revision"
        AND resource.labels.service_name = "${var.api_service_name}"
        AND metric.type = "run.googleapis.com/request_count"
        AND metric.labels.response_code_class = "5xx"
      EOT

      comparison      = "COMPARISON_GT"
      threshold_value = 5
      duration        = "300s"

      aggregations {
        alignment_period     = "300s"
        per_series_aligner   = "ALIGN_RATE"
        cross_series_reducer = "REDUCE_SUM"
      }
    }
  }

  notification_channels = [google_monitoring_notification_channel.email.id]

  alert_strategy {
    auto_close = "1800s"
  }
}

# ── Alert: API latency P95 > 10s ──────────────────────────────

resource "google_monitoring_alert_policy" "api_latency" {
  count        = var.enable_alerts ? 1 : 0
  project      = var.project_id
  display_name = "[${var.environment}] API latency P95 > 10s"
  combiner     = "OR"

  conditions {
    display_name = "Cloud Run request latency P95"
    condition_threshold {
      filter = <<-EOT
        resource.type = "cloud_run_revision"
        AND resource.labels.service_name = "${var.api_service_name}"
        AND metric.type = "run.googleapis.com/request_latencies"
      EOT

      comparison      = "COMPARISON_GT"
      threshold_value = 10000
      duration        = "300s"

      aggregations {
        alignment_period     = "300s"
        per_series_aligner   = "ALIGN_PERCENTILE_95"
        cross_series_reducer = "REDUCE_MAX"
      }
    }
  }

  notification_channels = [google_monitoring_notification_channel.email.id]

  alert_strategy {
    auto_close = "1800s"
  }
}

# ── Alert: Cloud SQL CPU > 80% ─────────────────────────────────

resource "google_monitoring_alert_policy" "db_cpu" {
  count        = var.enable_alerts ? 1 : 0
  project      = var.project_id
  display_name = "[${var.environment}] Cloud SQL CPU > 80%"
  combiner     = "OR"

  conditions {
    display_name = "Database CPU utilization"
    condition_threshold {
      filter = <<-EOT
        resource.type = "cloudsql_database"
        AND resource.labels.database_id = "${var.project_id}:${var.database_instance}"
        AND metric.type = "cloudsql.googleapis.com/database/cpu/utilization"
      EOT

      comparison      = "COMPARISON_GT"
      threshold_value = 0.8
      duration        = "300s"

      aggregations {
        alignment_period     = "300s"
        per_series_aligner   = "ALIGN_MEAN"
        cross_series_reducer = "REDUCE_MAX"
      }
    }
  }

  notification_channels = [google_monitoring_notification_channel.email.id]

  alert_strategy {
    auto_close = "1800s"
  }
}

# ── Alert: Cloud SQL disk > 80% ────────────────────────────────

resource "google_monitoring_alert_policy" "db_disk" {
  count        = var.enable_alerts ? 1 : 0
  project      = var.project_id
  display_name = "[${var.environment}] Cloud SQL disk usage > 80%"
  combiner     = "OR"

  conditions {
    display_name = "Database disk utilization"
    condition_threshold {
      filter = <<-EOT
        resource.type = "cloudsql_database"
        AND resource.labels.database_id = "${var.project_id}:${var.database_instance}"
        AND metric.type = "cloudsql.googleapis.com/database/disk/utilization"
      EOT

      comparison      = "COMPARISON_GT"
      threshold_value = 0.8
      duration        = "300s"

      aggregations {
        alignment_period     = "300s"
        per_series_aligner   = "ALIGN_MEAN"
        cross_series_reducer = "REDUCE_MAX"
      }
    }
  }

  notification_channels = [google_monitoring_notification_channel.email.id]

  alert_strategy {
    auto_close = "1800s"
  }
}

# ── Alert: Cloud Run instance count at max ─────────────────────

resource "google_monitoring_alert_policy" "api_max_instances" {
  count        = var.enable_alerts ? 1 : 0
  project      = var.project_id
  display_name = "[${var.environment}] API approaching max instances"
  combiner     = "OR"

  conditions {
    display_name = "Cloud Run instance count high"
    condition_threshold {
      filter = <<-EOT
        resource.type = "cloud_run_revision"
        AND resource.labels.service_name = "${var.api_service_name}"
        AND metric.type = "run.googleapis.com/container/instance_count"
      EOT

      comparison      = "COMPARISON_GT"
      threshold_value = 8
      duration        = "300s"

      aggregations {
        alignment_period     = "300s"
        per_series_aligner   = "ALIGN_MAX"
        cross_series_reducer = "REDUCE_SUM"
      }
    }
  }

  notification_channels = [google_monitoring_notification_channel.email.id]

  alert_strategy {
    auto_close = "1800s"
  }
}

# ── Alert: Uptime check failing ────────────────────────────────

resource "google_monitoring_alert_policy" "uptime_api" {
  project      = var.project_id
  display_name = "[${var.environment}] API uptime check failing"
  combiner     = "OR"

  conditions {
    display_name = "API health check failure"
    condition_threshold {
      filter = <<-EOT
        resource.type = "uptime_url"
        AND metric.type = "monitoring.googleapis.com/uptime_check/check_passed"
        AND metric.labels.check_id = "${google_monitoring_uptime_check_config.api_health.uptime_check_id}"
      EOT

      comparison      = "COMPARISON_GT"
      threshold_value = 1
      duration        = "300s"

      aggregations {
        alignment_period     = "60s"
        per_series_aligner   = "ALIGN_NEXT_OLDER"
        cross_series_reducer = "REDUCE_COUNT_FALSE"
        group_by_fields      = ["resource.label.project_id"]
      }
    }
  }

  notification_channels = [google_monitoring_notification_channel.email.id]

  alert_strategy {
    auto_close = "1800s"
  }
}
