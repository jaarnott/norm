variable "project_id" { type = string }
variable "region" { type = string }
variable "environment" { type = string }
variable "domain" { type = string }
variable "api_service_name" { type = string }
variable "web_service_name" { type = string }

# ---------- Serverless NEGs ----------

resource "google_compute_region_network_endpoint_group" "api_neg" {
  name                  = "norm-${var.environment}-api-neg"
  region                = var.region
  project               = var.project_id
  network_endpoint_type = "SERVERLESS"

  cloud_run {
    service = var.api_service_name
  }
}

resource "google_compute_region_network_endpoint_group" "web_neg" {
  name                  = "norm-${var.environment}-web-neg"
  region                = var.region
  project               = var.project_id
  network_endpoint_type = "SERVERLESS"

  cloud_run {
    service = var.web_service_name
  }
}

# ---------- Backend Services ----------

resource "google_compute_backend_service" "api" {
  name    = "norm-${var.environment}-api-backend"
  project = var.project_id

  protocol              = "HTTPS"
  load_balancing_scheme = "EXTERNAL_MANAGED"

  backend {
    group = google_compute_region_network_endpoint_group.api_neg.id
  }
}

resource "google_compute_backend_service" "web" {
  name    = "norm-${var.environment}-web-backend"
  project = var.project_id

  protocol              = "HTTPS"
  load_balancing_scheme = "EXTERNAL_MANAGED"

  backend {
    group = google_compute_region_network_endpoint_group.web_neg.id
  }
}

# ---------- URL Map ----------

resource "google_compute_url_map" "main" {
  name    = "norm-${var.environment}-url-map"
  project = var.project_id

  default_service = google_compute_backend_service.web.id

  host_rule {
    hosts        = [var.domain]
    path_matcher = "main"
  }

  path_matcher {
    name            = "main"
    default_service = google_compute_backend_service.web.id

    path_rule {
      paths   = ["/api/*"]
      service = google_compute_backend_service.api.id
    }
  }
}

# ---------- SSL Certificate ----------

resource "google_compute_managed_ssl_certificate" "main" {
  name    = "norm-${var.environment}-ssl-cert"
  project = var.project_id

  managed {
    domains = [var.domain]
  }
}

# ---------- HTTPS Proxy + Forwarding Rule ----------

resource "google_compute_target_https_proxy" "main" {
  name    = "norm-${var.environment}-https-proxy"
  project = var.project_id
  url_map = google_compute_url_map.main.id

  ssl_certificates = [google_compute_managed_ssl_certificate.main.id]
}

resource "google_compute_global_forwarding_rule" "https" {
  name       = "norm-${var.environment}-https-rule"
  project    = var.project_id
  target     = google_compute_target_https_proxy.main.id
  port_range = "443"

  load_balancing_scheme = "EXTERNAL_MANAGED"
}

# ---------- HTTP-to-HTTPS Redirect ----------

resource "google_compute_url_map" "redirect" {
  name    = "norm-${var.environment}-http-redirect"
  project = var.project_id

  default_url_redirect {
    https_redirect         = true
    redirect_response_code = "MOVED_PERMANENTLY_DEFAULT"
    strip_query            = false
  }
}

resource "google_compute_target_http_proxy" "redirect" {
  name    = "norm-${var.environment}-http-proxy"
  project = var.project_id
  url_map = google_compute_url_map.redirect.id
}

resource "google_compute_global_forwarding_rule" "http" {
  name       = "norm-${var.environment}-http-rule"
  project    = var.project_id
  target     = google_compute_target_http_proxy.redirect.id
  port_range = "80"

  load_balancing_scheme = "EXTERNAL_MANAGED"
}

# ---------- Outputs ----------

output "load_balancer_ip" {
  value = google_compute_global_forwarding_rule.https.ip_address
}
