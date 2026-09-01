variable "project_id" {
  description = "GCP project for the dev box. Should NOT be a production project — client work lives on this machine."
  type        = string
}

variable "region" {
  description = "Region. Sydney is ~30ms from NZ; anywhere further is felt on every keystroke over SSH."
  type        = string
  default     = "australia-southeast1"
}

variable "zone" {
  type    = string
  default = "australia-southeast1-a"
}

variable "name" {
  description = "Instance name; also the SSH host alias."
  type        = string
  default     = "norm-dev"
}

variable "machine_type" {
  description = <<-EOT
    e2-standard-4 is 4 vCPU / 16 GB — the same shape as the Codespace it
    replaces, and measured usage there was 4.0 GB with several agents running.
    E2 gets no sustained-use discount (only N1/N2/N2D/C2/M1/M2 do), so the
    on-demand rate is the real rate: ~USD $0.19/hr in Sydney.
  EOT
  type        = string
  default     = "e2-standard-4"
}

variable "boot_disk_gb" {
  description = "Keeps billing while the VM is stopped, so it sets the floor cost (~USD $10-20/mo at 100 GB)."
  type        = number
  default     = 100
}

variable "idle_shutdown_minutes" {
  description = <<-EOT
    Shut down after this long with no logged-in session and no busy CPU.
    This is what makes the economics work: on-demand at ~217 h/month is about
    a third of always-on. Set to 0 to disable.
  EOT
  type        = number
  default     = 45
}

variable "db_projects" {
  description = <<-EOT
    Projects whose Cloud SQL instances this box may reach through
    cloud-sql-proxy. The VM's service account is granted cloudsql.client and
    cloudsql.instanceUser in each. Cross-project by design: the box is
    deliberately NOT inside the production project.
  EOT
  type        = list(string)
  default     = []
}

variable "owners" {
  description = "Principals who may SSH in and act as the VM's service account, e.g. [\"user:you@example.com\"]."
  type        = list(string)
  default     = []
}
