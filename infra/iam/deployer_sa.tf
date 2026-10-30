# deployer-sa: runs `terraform apply` on envs/prod after merge (CI, OSRS-12).
#
# Write access only to what envs/prod manages today: the state bucket.
# Deliberately cannot: change IAM anywhere, enable APIs (that is Joe's step),
# touch billing, or update/delete either project (project changes in
# envs/prod hit a 403 and are applied locally by an Owner).
# When envs/prod gains a service, add that service's narrowest write role here
# and its viewer role to plan_sa.tf.

locals {
  deployer_member = "serviceAccount:${google_service_account.deployer.email}"
}

# Read both projects so apply can refresh google_project.infra / .llm.
resource "google_project_iam_member" "deployer_browser" {
  for_each = toset([var.infra_project_id, var.llm_project_id])

  project = each.value
  role    = "roles/browser"
  member  = local.deployer_member
}

# Read and list all state. Unconditional because objects.list is checked
# against the bucket, not each object, so a prefix condition would block it.
resource "google_storage_bucket_iam_member" "deployer_state_read" {
  bucket = var.state_bucket
  role   = "roles/storage.objectViewer"
  member = local.deployer_member
}

# Write prod state and its .tflock lock object, and nothing else: the
# condition keeps the deployer away from iam/ state, which only an Owner writes.
resource "google_storage_bucket_iam_member" "deployer_state_write" {
  bucket = var.state_bucket
  role   = "roles/storage.objectUser"
  member = local.deployer_member

  condition {
    title       = "prod-state-only"
    description = "Only objects under prod/ (envs/prod state and lock)."
    expression  = "resource.name.startsWith(\"projects/_/buckets/${var.state_bucket}/objects/prod/\")"
  }
}

# Change the bucket's settings (versioning, lifecycle) for state_bucket.tf.
# Custom role because every predefined role with storage.buckets.update also
# has storage.buckets.setIamPolicy, which would let the deployer grant access.
resource "google_project_iam_custom_role" "tfstate_bucket_editor" {
  project     = var.infra_project_id
  role_id     = "tfStateBucketEditor"
  title       = "Terraform state bucket editor"
  description = "Read and update bucket settings. No IAM, no delete. Used by deployer-sa on the state bucket."
  permissions = [
    "storage.buckets.get",
    "storage.buckets.update",
  ]

  depends_on = [google_project_service.iam]
}

resource "google_storage_bucket_iam_member" "deployer_state_bucket" {
  bucket = var.state_bucket
  role   = google_project_iam_custom_role.tfstate_bucket_editor.id
  member = local.deployer_member
}
