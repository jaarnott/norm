terraform {
  backend "gcs" {
    bucket = "norm-terraform-state"
    prefix = "testing"
  }
}
