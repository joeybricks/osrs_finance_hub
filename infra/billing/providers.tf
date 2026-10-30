# Auth: your ADC login (gcloud auth application-default login). Local only.
#
# The Budgets API refuses calls from user credentials unless they name a quota
# project ("API requires a quota project"). user_project_override sends
# billing_project as that quota project on every request; it needs the Budgets
# API on in osrs-infra (envs/prod apis.tf) and serviceusage.services.use there,
# which a project Owner has.
provider "google" {
  project               = var.infra_project_id
  region                = var.region
  billing_project       = var.infra_project_id
  user_project_override = true
}
