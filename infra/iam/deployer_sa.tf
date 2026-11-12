# deployer-sa: runs `terraform apply` on envs/prod after merge (CI, OSRS-12),
# and ships the app (OSRS-27): pushes the image and deploys it to Cloud Run.
#
# Write access only to the state bucket, the image repo and Cloud Run.
# Deliberately cannot: change IAM anywhere (so it cannot make the service
# public), enable APIs (that is Joe's step), act as any account except
# runtime-sa, touch billing, or update/delete either project (project changes
# in envs/prod hit a 403 and are applied locally by an Owner).
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

# Read-only, so a future CI apply can refresh the billing export dataset and
# the APIs declared in envs/prod. No BigQuery write role on purpose:
# bigquery.datasets.update can also edit the dataset's access list, i.e. grant
# access. Dataset changes are applied locally by an Owner, like project changes.
resource "google_project_iam_member" "deployer_bigquery_metadata" {
  project = var.infra_project_id
  role    = "roles/bigquery.metadataViewer"
  member  = local.deployer_member
}

resource "google_project_iam_member" "deployer_service_usage" {
  project = var.infra_project_id
  role    = "roles/serviceusage.serviceUsageViewer"
  member  = local.deployer_member
}

# Read the budget alerts topic, as for plan-sa. Topic changes are applied
# locally by an Owner for now: no Pub/Sub write role yet.
resource "google_project_iam_member" "deployer_pubsub_viewer" {
  project = var.infra_project_id
  role    = "roles/pubsub.viewer"
  member  = local.deployer_member
}

# Read the Artifact Registry repo so a future CI apply can refresh it. No admin
# role: it includes repositories.setIamPolicy, which could grant access. The
# repo is created locally by an Owner; image push is granted below, scoped to
# the one repo.
resource "google_project_iam_member" "deployer_artifactregistry_reader" {
  project = var.infra_project_id
  role    = "roles/artifactregistry.reader"
  member  = local.deployer_member
}

# --- Shipping the app (OSRS-27) ---------------------------------------------

# Push images to the app's repo only: upload and create/move tags. writer has
# no versions.delete (old images go via the repo's cleanup policy) and no
# setIamPolicy. Bound on the repo itself, not the project.
resource "google_artifact_registry_repository_iam_member" "deployer_image_push" {
  project    = var.infra_project_id
  location   = var.region
  repository = var.artifact_registry_repository
  role       = "roles/artifactregistry.writer"
  member     = local.deployer_member
}

# Deploy new revisions to Cloud Run. developer, not admin: admin adds
# run.services.setIamPolicy, which would let the deployer grant allUsers the
# invoker role and make the app public (undoing OSRS-29). developer can still
# create and delete services; they hold no data.
# Project-level for now because the service does not exist yet; OSRS-28
# narrows this to the one service once it does.
resource "google_project_iam_member" "deployer_run_developer" {
  project = var.infra_project_id
  role    = "roles/run.developer"
  member  = local.deployer_member
}

# Launch the service as runtime-sa ("actAs"). Cloud Run requires the deployer
# to hold this on the account the service runs as. Bound on runtime-sa alone:
# at project level it would cover every account, so the deployer could act as
# killswitch-sa, plan-sa or itself and pick up their access.
resource "google_service_account_iam_member" "deployer_act_as_runtime" {
  service_account_id = google_service_account.runtime.name
  role               = "roles/iam.serviceAccountUser"
  member             = local.deployer_member
}
