# Dev box — deliberately a SEPARATE root module with its own state.
#
# The main module in ../ manages production, staging and testing, and its
# config has drifted from live before (see infra/COST-CUTS-2026-08.md — a
# naive apply there wanted to replace the database). Keeping the dev box in
# its own state means `apply` here can never touch any of that.

terraform {
  required_version = ">= 1.5"

  required_providers {
    google = {
      source  = "hashicorp/google"
      version = "~> 5.0"
    }
  }

  # State is local by default so this can be stood up before any bucket
  # exists. To share it, add:
  #   backend "gcs" { bucket = "..." , prefix = "dev-vm" }
}

provider "google" {
  project = var.project_id
  region  = var.region

  # Quota and API-enablement checks follow the credentials' OWN project unless
  # told otherwise. Applying this from a service account that lives in another
  # project (norm-production-491101) therefore failed on the first API that was
  # enabled here but not there — iam.googleapis.com — with a misleading "API
  # not enabled" naming a project number that is not this one.
  #
  # Sending X-Goog-User-Project instead keeps every check against the project
  # being built, so nothing has to be switched on in production to build a dev
  # box. Requires serviceusage.services.use here, which project owners have.
  billing_project       = var.project_id
  user_project_override = true
}
