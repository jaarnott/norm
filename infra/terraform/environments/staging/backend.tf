terraform {
  backend "gcs" {
    bucket = "norm-tfstate-491101"
    prefix = "staging"
  }
}
