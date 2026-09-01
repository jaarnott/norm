output "instance_name" {
  value = google_compute_instance.dev.name
}

output "zone" {
  value = google_compute_instance.dev.zone
}

output "service_account" {
  description = "Attached to the VM — grant this, rather than distributing a key file."
  value       = google_service_account.dev.email
}

output "ssh_command" {
  description = "SSH via IAP. The box has no external IP; this is the only way in."
  value       = "gcloud compute ssh ${google_compute_instance.dev.name} --project=${var.project_id} --zone=${var.zone} --tunnel-through-iap"
}

output "start_command" {
  value = "gcloud compute instances start ${google_compute_instance.dev.name} --project=${var.project_id} --zone=${var.zone}"
}

output "monthly_cost_note" {
  description = "E2 gets no sustained-use discount, so on-demand is the real rate."
  value       = "e2-standard-4 Sydney ~USD $0.19/hr: ~$139/mo always-on, ~$41/mo at 217h, plus ~$10-20/mo for the ${var.boot_disk_gb}GB disk (billed while stopped)."
}
