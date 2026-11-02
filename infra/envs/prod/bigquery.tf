# Billing export dataset (OSRS-13). Terraform makes the empty dataset; the
# export itself is switched on by hand in the console, because Google has no
# API for it. Steps: ops/runbooks/billing-export.md.
#
# Location: regional, per ADR 0001. Regional datasets get no backfill: rows
# start from the day the export is enabled (multi-region US/EU would add the
# previous month). Accepted, since billing had barely started. Location can't
# be changed after creation.
#
# No `access` blocks on purpose. They are authoritative: listing any would make
# Terraform remove the owner grant Google adds for
# billing-export-bigquery@system.gserviceaccount.com, and the export would stop.
# Add readers later with google_bigquery_dataset_iam_member.
resource "google_bigquery_dataset" "billing_export" {
  project       = google_project.infra.project_id
  dataset_id    = "billing_export"
  friendly_name = "Cloud Billing export"
  description   = "Standard usage cost export, written by Google. Managed by Terraform; see ops/runbooks/billing-export.md."
  location      = var.region

  # No default table or partition expiry: billing history must never age out.
  # Never delete the tables with the dataset.
  delete_contents_on_destroy = false

  # Two locks against deletion. prevent_destroy (below) lives in config, so
  # deleting this block removes it too; PREVENT is saved in state and still
  # blocks the destroy after the block is gone.
  deletion_policy = "PREVENT"

  labels = {
    purpose    = "billing-export"
    managed-by = "terraform"
  }

  lifecycle {
    prevent_destroy = true
  }

  depends_on = [google_project_service.bigquery]
}
