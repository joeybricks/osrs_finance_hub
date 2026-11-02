# One identity per job. A new service account has no permissions; each one's
# roles are in its own file (plan_sa.tf, deployer_sa.tf, killswitch_sa.tf).
# No keys are ever created: CI signs in via Workload Identity Federation (OSRS-12).

resource "google_service_account" "plan" {
  project      = var.infra_project_id
  account_id   = "plan-sa"
  display_name = "Terraform plan (read-only, PR CI)"
  description  = "Runs terraform plan on pull requests. Reads only; cannot change anything."

  depends_on = [google_project_service.iam]
}

resource "google_service_account" "deployer" {
  project      = var.infra_project_id
  account_id   = "deployer-sa"
  display_name = "Terraform apply (envs/prod, merge CI)"
  description  = "Applies envs/prod after merge. Writes only to services in use; no owner, billing or IAM admin."

  depends_on = [google_project_service.iam]
}

resource "google_service_account" "killswitch" {
  project      = var.infra_project_id
  account_id   = "killswitch-sa"
  display_name = "Billing killswitch"
  description  = "Unlinks billing from osrs-infra when the budget is exceeded (OSRS-16). Nothing else."

  depends_on = [google_project_service.iam]
}
