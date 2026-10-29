# Remote state in the bucket made by infra/bootstrap/create_state_bucket.sh.
# The GCS backend locks state itself (a .tflock object next to the state).
# OSRS-10 pins exact Terraform and provider versions.
terraform {
  required_version = ">= 1.6"

  backend "gcs" {
    bucket = "osrs-infra-tfstate"
    prefix = "prod"
  }
}
