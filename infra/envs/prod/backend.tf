# Remote state in the bucket made by infra/bootstrap/create_state_bucket.sh.
# The GCS backend locks state itself (a .tflock object next to the state).
terraform {
  backend "gcs" {
    bucket = "osrs-infra-tfstate"
    prefix = "prod"
  }
}
