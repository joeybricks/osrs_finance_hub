# Auth: your ADC login (gcloud auth application-default login). Local only.
provider "google" {
  project = var.infra_project_id
  region  = var.region
}
