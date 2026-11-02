# Throwaway project for the live killswitch test (OSRS-18). Destroy it when
# the test is logged: runbook ops/runbooks/killswitch.md, "Live test".
#
#   osrs-killswitch-test-1102  (billing linked)
#    ├─ bucket + one tiny object   → readable while billing is on, refused after unlink
#    └─ role billingKillswitch     → killswitch-sa may read and unlink, nothing else
#
# Cost: one object of a few bytes in us-central1, inside the 5 GB Always Free
# tier, for about an hour. Effectively £0.

data "google_billing_account" "main" {
  display_name = var.billing_account_name
  open         = true
}

resource "google_project" "test" {
  name            = "osrs-killswitch-test"
  project_id      = var.test_project_id
  org_id          = var.org_id
  billing_account = data.google_billing_account.main.id

  # Provider default is PREVENT; this project exists to be destroyed.
  # Deleted projects sit in a 30-day recovery window, then go for good.
  deletion_policy = "DELETE"

  # auto_create_network stays at its default (true) on purpose: false makes
  # the provider switch on the Compute API to delete the default network, and
  # turning APIs on is Joe's step. Compute isn't on here, so no network appears.

  lifecycle {
    # The test unlinks and relinks billing outside Terraform. Without this the
    # next plan would try to "fix" the link back mid-test.
    ignore_changes = [billing_account]
  }
}

# Cloud Storage for the tiny resource. Usually on by default in new projects;
# declared so Terraform tracks it. Turning it on is Joe's apply.
resource "google_project_service" "storage" {
  project            = google_project.test.project_id
  service            = "storage.googleapis.com"
  disable_on_destroy = false
}

# IAM API: needed to define the custom role below. Free.
resource "google_project_service" "iam" {
  project            = google_project.test.project_id
  service            = "iam.googleapis.com"
  disable_on_destroy = false
}

# The tiny resource. GCS refuses reads once the project has no billing
# account, so reading this object before and after shows spend has stopped.
resource "google_storage_bucket" "canary" {
  project                     = google_project.test.project_id
  name                        = "${var.test_project_id}-canary"
  location                    = upper(var.region)
  uniform_bucket_level_access = true
  public_access_prevention    = "enforced"
  # Lets destroy remove the bucket with the object still in it.
  force_destroy = true
  # No 7-day soft-delete copy: nothing should outlive the destroy.
  soft_delete_policy {
    retention_duration_seconds = 0
  }

  depends_on = [google_project_service.storage]
}

resource "google_storage_bucket_object" "canary" {
  bucket  = google_storage_bucket.canary.name
  name    = "canary.txt"
  content = "billing is on\n"
}

# Same role as infra/iam/killswitch_sa.tf. A project-level custom role can only
# be granted in the project that defines it, so it is defined again here; it
# goes when the project is destroyed. (An org-level role would outlive the test
# and needs roles/iam.organizationRoleAdmin.)
resource "google_project_iam_custom_role" "billing_killswitch" {
  project     = google_project.test.project_id
  role_id     = "billingKillswitch"
  title       = "Billing killswitch"
  description = "Read and unlink the project's billing link. Cannot link it again or do anything else."
  permissions = [
    "resourcemanager.projects.deleteBillingAssignment",
    "resourcemanager.projects.get",
  ]

  depends_on = [google_project_service.iam]
}

resource "google_project_iam_member" "killswitch_unlink_billing" {
  project = google_project.test.project_id
  role    = google_project_iam_custom_role.billing_killswitch.id
  member  = "serviceAccount:killswitch-sa@${var.infra_project_id}.iam.gserviceaccount.com"
}
