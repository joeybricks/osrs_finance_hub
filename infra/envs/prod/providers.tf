# Defaults for every google resource unless it sets its own project/region.
# Auth: your ADC login locally; Workload Identity Federation in CI (OSRS-12).
provider "google" {
  project = var.infra_project_id
  region  = var.region
}
