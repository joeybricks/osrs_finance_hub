# APIs the resources in this stack need. Turning an API on is Joe's apply.
# disable_on_destroy = false: removing a block here must never switch an API
# off underneath something that still uses it.

# BigQuery: holds the billing export dataset (bigquery.tf). Already on (Google
# enables it by default on new projects); declared so Terraform tracks it.
# Free to have on; storage and queries sit inside the free tier.
resource "google_project_service" "bigquery" {
  project            = google_project.infra.project_id
  service            = "bigquery.googleapis.com"
  disable_on_destroy = false
}
