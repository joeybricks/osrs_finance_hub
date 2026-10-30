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

# Pub/Sub: the budget publishes its status to a topic (pubsub.tf), which the
# killswitch (OSRS-16) and phone alerts (OSRS-15) read. Free tier: 10 GiB/month;
# budget messages are a few KB a day.
resource "google_project_service" "pubsub" {
  project            = google_project.infra.project_id
  service            = "pubsub.googleapis.com"
  disable_on_destroy = false
}

# Cloud Billing Budget API: creates and reads google_billing_budget (budget.tf).
# Free. Calls bill quota to this project, so it must be on here even though the
# budget itself lives on the billing account.
resource "google_project_service" "billingbudgets" {
  project            = google_project.infra.project_id
  service            = "billingbudgets.googleapis.com"
  disable_on_destroy = false
}
