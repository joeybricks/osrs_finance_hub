# killswitch-sa: one job only, unlinking billing from osrs-infra when the
# budget is exceeded (the function itself is OSRS-16).
#
# Custom role with a single permission. Not roles/billing.projectManager:
# that also has createBillingAssignment, so a buggy or hijacked killswitch
# could re-link billing. This one can only switch billing off.
# osrs-infra only: osrs-llm never has billing.
#
# resourcemanager.projects.get: the script (ops/scripts/killswitch.sh) reads
# the billing link first (getBillingInfo needs it), so a dry run can say what
# it would do and a second run is a no-op. It reads project metadata only:
# name, number, labels. No IAM policy, no data in any service.

resource "google_project_iam_custom_role" "billing_killswitch" {
  project     = var.infra_project_id
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
  project = var.infra_project_id
  role    = google_project_iam_custom_role.billing_killswitch.id
  member  = "serviceAccount:${google_service_account.killswitch.email}"
}
