# killswitch-sa: one job only, unlinking billing from osrs-infra when the
# budget is exceeded (the function itself is OSRS-16).
#
# Custom role with a single permission. Not roles/billing.projectManager:
# that also has createBillingAssignment, so a buggy or hijacked killswitch
# could re-link billing. This one can only switch billing off.
# osrs-infra only: osrs-llm never has billing.
#
# If the OSRS-16 function needs to read the billing link before unlinking
# (getBillingInfo needs resourcemanager.projects.get), add it here then.

resource "google_project_iam_custom_role" "billing_killswitch" {
  project     = var.infra_project_id
  role_id     = "billingKillswitch"
  title       = "Billing killswitch"
  description = "Unlink billing from the project. Cannot link it again or do anything else."
  permissions = [
    "resourcemanager.projects.deleteBillingAssignment",
  ]

  depends_on = [google_project_service.iam]
}

resource "google_project_iam_member" "killswitch_unlink_billing" {
  project = var.infra_project_id
  role    = google_project_iam_custom_role.billing_killswitch.id
  member  = "serviceAccount:${google_service_account.killswitch.email}"
}
