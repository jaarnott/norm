# ── APIs ────────────────────────────────────────────────────────
resource "google_project_service" "apis" {
  for_each = toset([
    # cloudresourcemanager and serviceusage are what Terraform itself calls.
    # With user_project_override they must be on THIS project, and enabling
    # them is chicken-and-egg — so a brand new project needs them switched on
    # once by hand before the first apply (see README).
    "cloudresourcemanager.googleapis.com",
    "serviceusage.googleapis.com",
    "compute.googleapis.com",
    "iap.googleapis.com",
    "oslogin.googleapis.com",
    "iam.googleapis.com",
    "sqladmin.googleapis.com",
  ])
  project = var.project_id
  service = each.value

  # Never turn an API off on destroy — other things in the project may rely
  # on it, and disabling is far more disruptive than leaving it enabled.
  disable_on_destroy = false
}

# ── Network ─────────────────────────────────────────────────────
resource "google_compute_network" "dev" {
  name                    = "${var.name}-vpc"
  project                 = var.project_id
  auto_create_subnetworks = false
  depends_on              = [google_project_service.apis]
}

resource "google_compute_subnetwork" "dev" {
  name          = "${var.name}-subnet"
  project       = var.project_id
  region        = var.region
  network       = google_compute_network.dev.id
  ip_cidr_range = "10.20.0.0/24"

  # So SSH sessions and package installs are debuggable without an external IP.
  private_ip_google_access = true
}

# The VM has no external IP, which means no outbound internet either — and
# without that the startup script cannot install a single package. Cloud NAT
# is what gives it egress while staying unreachable from the internet.
resource "google_compute_router" "dev" {
  name    = "${var.name}-router"
  project = var.project_id
  region  = var.region
  network = google_compute_network.dev.id
}

resource "google_compute_router_nat" "dev" {
  name                               = "${var.name}-nat"
  project                            = var.project_id
  region                             = var.region
  router                             = google_compute_router.dev.name
  nat_ip_allocate_option             = "AUTO_ONLY"
  source_subnetwork_ip_ranges_to_nat = "ALL_SUBNETWORKS_ALL_IP_RANGES"

  log_config {
    enable = false
    filter = "ERRORS_ONLY"
  }
}

# SSH arrives only through Google's IAP forwarders. 35.235.240.0/20 is IAP's
# documented range; there is no port 22 open to the internet.
resource "google_compute_firewall" "iap_ssh" {
  name    = "${var.name}-allow-iap-ssh"
  project = var.project_id
  network = google_compute_network.dev.name

  allow {
    protocol = "tcp"
    ports    = ["22"]
  }

  source_ranges = ["35.235.240.0/20"]
  target_tags   = ["${var.name}-ssh"]
}

# ── Identity ────────────────────────────────────────────────────
# Attached to the VM, so credentials come from the metadata server: short
# lived, auto-rotating, and revocable centrally. This is the reason to run the
# box on GCE at all — no norm-config-sa.json sitting on disk.
resource "google_service_account" "dev" {
  account_id   = "${var.name}-vm"
  display_name = "Dev box ${var.name}"
  project      = var.project_id
  depends_on   = [google_project_service.apis]
}

# Reaching Cloud SQL in ANOTHER project (Norm's). cloudsql.client authorises
# the proxy; instanceUser is what IAM database auth checks.
resource "google_project_iam_member" "sql_client" {
  for_each = toset(var.db_projects)
  project  = each.value
  role     = "roles/cloudsql.client"
  member   = "serviceAccount:${google_service_account.dev.email}"
}

resource "google_project_iam_member" "sql_instance_user" {
  for_each = toset(var.db_projects)
  project  = each.value
  role     = "roles/cloudsql.instanceUser"
  member   = "serviceAccount:${google_service_account.dev.email}"
}

# ── Who may get in ──────────────────────────────────────────────
resource "google_project_iam_member" "owner_iap" {
  for_each = toset(var.owners)
  project  = var.project_id
  role     = "roles/iap.tunnelResourceAccessor"
  member   = each.value
}

resource "google_project_iam_member" "owner_oslogin" {
  for_each = toset(var.owners)
  project  = var.project_id
  role     = "roles/compute.osAdminLogin"
  member   = each.value
}

# Needed to start and stop the box — which you do daily, given it shuts itself
# down when idle.
resource "google_project_iam_member" "owner_compute" {
  for_each = toset(var.owners)
  project  = var.project_id
  role     = "roles/compute.instanceAdmin.v1"
  member   = each.value
}

# Attaching a VM to a service account counts as acting as it.
resource "google_service_account_iam_member" "owner_actas" {
  for_each           = toset(var.owners)
  service_account_id = google_service_account.dev.name
  role               = "roles/iam.serviceAccountUser"
  member             = each.value
}

# ── The box ─────────────────────────────────────────────────────
resource "google_compute_instance" "dev" {
  name         = var.name
  project      = var.project_id
  zone         = var.zone
  machine_type = var.machine_type
  tags         = ["${var.name}-ssh"]

  boot_disk {
    initialize_params {
      image = "ubuntu-os-cloud/ubuntu-2404-lts-amd64"
      size  = var.boot_disk_gb
      type  = "pd-balanced"
    }
  }

  network_interface {
    subnetwork = google_compute_subnetwork.dev.id
    # No access_config block: no external IP, by design.
  }

  service_account {
    email  = google_service_account.dev.email
    scopes = ["cloud-platform"]
  }

  metadata = {
    enable-oslogin = "TRUE"
    startup-script = templatefile("${path.module}/startup.sh", {
      idle_shutdown_minutes = var.idle_shutdown_minutes
    })
  }

  shielded_instance_config {
    enable_secure_boot          = true
    enable_vtpm                 = true
    enable_integrity_monitoring = true
  }

  # A stopped box is the normal resting state, not drift — Terraform must not
  # start it again on the next apply.
  desired_status = "RUNNING"

  lifecycle {
    ignore_changes = [desired_status]
  }

  allow_stopping_for_update = true
  depends_on                = [google_compute_router_nat.dev]
}
