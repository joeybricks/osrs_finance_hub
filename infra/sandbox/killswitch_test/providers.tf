# Auth: your ADC login (gcloud auth application-default login). Local only.
#
# Quota for API calls made with user credentials goes to osrs-infra, as in
# infra/billing, so nothing here depends on APIs in the new project being on
# before it exists.
provider "google" {
  project               = var.infra_project_id
  region                = var.region
  billing_project       = var.infra_project_id
  user_project_override = true
}
