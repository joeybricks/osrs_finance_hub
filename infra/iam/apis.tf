# APIs the IAM resources need. Both are free to enable.
# disable_on_destroy = false: removing a block here must never switch an API
# off underneath something that still uses it.

# Service accounts and custom roles.
resource "google_project_service" "iam" {
  project            = var.infra_project_id
  service            = "iam.googleapis.com"
  disable_on_destroy = false
}

# Project IAM policy reads/writes. Calls made as a service account bill quota
# to the SA's own project, so CI needs this on in osrs-infra.
resource "google_project_service" "cloudresourcemanager" {
  project            = var.infra_project_id
  service            = "cloudresourcemanager.googleapis.com"
  disable_on_destroy = false
}
