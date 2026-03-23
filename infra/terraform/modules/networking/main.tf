variable "project_id" { type = string }
variable "region" { type = string }
variable "environment" { type = string }

resource "google_compute_network" "vpc" {
  name                    = "norm-${var.environment}-vpc"
  auto_create_subnetworks = false
  project                 = var.project_id
}

resource "google_compute_subnetwork" "subnet" {
  name          = "norm-${var.environment}-subnet"
  ip_cidr_range = "10.0.0.0/24"
  region        = var.region
  network       = google_compute_network.vpc.id
  project       = var.project_id
}

resource "google_vpc_access_connector" "connector" {
  name          = "norm-${var.environment}-vpc"
  region        = var.region
  project       = var.project_id
  ip_cidr_range = "10.8.0.0/28"
  network       = google_compute_network.vpc.name
}

# Allow Cloud SQL private access
resource "google_compute_global_address" "private_ip" {
  name          = "norm-${var.environment}-private-ip"
  purpose       = "VPC_PEERING"
  address_type  = "INTERNAL"
  prefix_length = 16
  network       = google_compute_network.vpc.id
  project       = var.project_id
}

resource "google_service_networking_connection" "private_vpc" {
  network                 = google_compute_network.vpc.id
  service                 = "servicenetworking.googleapis.com"
  reserved_peering_ranges = [google_compute_global_address.private_ip.name]
}

output "network_id" { value = google_compute_network.vpc.id }
output "subnet_id" { value = google_compute_subnetwork.subnet.id }
output "vpc_connector_id" { value = google_vpc_access_connector.connector.id }
