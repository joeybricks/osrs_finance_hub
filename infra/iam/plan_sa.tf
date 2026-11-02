# plan-sa: read-only, for `terraform plan` on pull requests (envs/prod).
#
# Hand-picked roles, not roles/viewer: viewer has ~6,000 permissions, some of
# which can run billable calls (e.g. aiplatform.endpoints.predict). These cover
# exactly what envs/prod holds today. When envs/prod gains a service, add its
# viewer role here; a missing one shows up as a 403 in the PR plan.
#
# Locking: plan normally writes a .tflock object to the state bucket, which
# this account cannot do. CI must run `terraform plan -lock=false` (OSRS-12).
# Safe because plan never writes state.
#
# All grants use *_iam_member: adds one member to one role and leaves every
# other binding alone (unlike *_iam_policy / *_iam_binding).

locals {
  plan_member = "serviceAccount:${google_service_account.plan.email}"
}

# Read both projects and their IAM policies (google_project.infra / .llm).
resource "google_project_iam_member" "plan_browser" {
  for_each = toset([var.infra_project_id, var.llm_project_id])

  project = each.value
  role    = "roles/browser"
  member  = local.plan_member
}

# Read the state files. Bucket-level, so nothing else in the project is visible.
resource "google_storage_bucket_iam_member" "plan_state_objects" {
  bucket = var.state_bucket
  role   = "roles/storage.objectViewer"
  member = local.plan_member
}

# Read the bucket's own settings, to refresh google_storage_bucket.tfstate.
resource "google_storage_bucket_iam_member" "plan_state_bucket" {
  bucket = var.state_bucket
  role   = "roles/storage.legacyBucketReader"
  member = local.plan_member
}

# See BigQuery datasets and table schemas (bigquery.tf in envs/prod), but not
# read rows: metadataViewer has no bigquery.tables.getData, so CI never sees
# billing data. Project-level because the dataset is created after this stack.
resource "google_project_iam_member" "plan_bigquery_metadata" {
  project = var.infra_project_id
  role    = "roles/bigquery.metadataViewer"
  member  = local.plan_member
}

# See which APIs are on, to refresh google_project_service in envs/prod.
# Read only: cannot enable or disable anything.
resource "google_project_iam_member" "plan_service_usage" {
  project = var.infra_project_id
  role    = "roles/serviceusage.serviceUsageViewer"
  member  = local.plan_member
}

# Read the budget alerts topic (envs/prod pubsub.tf). pubsub.viewer is
# topics.get/list and subscriptions.get/list only: no publish, no pull of
# messages, no IAM reads.
resource "google_project_iam_member" "plan_pubsub_viewer" {
  project = var.infra_project_id
  role    = "roles/pubsub.viewer"
  member  = local.plan_member
}
