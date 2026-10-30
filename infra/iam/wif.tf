# Workload Identity Federation (WIF): lets GitHub Actions sign in to GCP
# without any key file.
#
#   GitHub job --OIDC token--> STS (checks the provider below) --federated token-->
#   IAM Credentials (checks workloadIdentityUser on the SA) --1h token--> gcloud/terraform
#
# Three questions, answered in three places:
# 1. Who are you?          The pool and provider in this file. GitHub hands the
#                          job a signed token saying "I am a run of repo X";
#                          the provider checks it and turns it into an identity.
# 2. Who may you become?   The workloadIdentityUser bindings in this file. They
#                          say which runs may borrow which service account.
# 3. What can you do?      Not here: the roles each service account holds
#                          (plan_sa.tf, deployer_sa.tf, killswitch_sa.tf).
# WIF adds no permissions of its own. It only lets a run borrow an account for
# an hour; that account's roles decide what the run can actually do.
#
# Lives in iam/ (applied locally by an Owner) because whoever can edit this
# can widen who is trusted.
#
# A deleted pool or provider is kept for 30 days and its ID cannot be reused
# in that time, so rename with care.

# ---------------------------------------------------------------------------
# 1. Who are you? (signing in)
# ---------------------------------------------------------------------------

# The pool: a container for identities that come from outside Google, here
# GitHub Actions runs. It grants nothing by itself.
resource "google_iam_workload_identity_pool" "github" {
  project                   = var.infra_project_id
  workload_identity_pool_id = "github"
  display_name              = "GitHub Actions"
  description               = "Identities from GitHub Actions runs of joeybricks/osrs_finance_hub."

  depends_on = [google_project_service.iam]
}

# The provider: the rules for which outside tokens are let into the pool, and
# what Google remembers about each one.
resource "google_iam_workload_identity_pool_provider" "github_actions" {
  project                            = var.infra_project_id
  workload_identity_pool_id          = google_iam_workload_identity_pool.github.workload_identity_pool_id
  workload_identity_pool_provider_id = "github-actions"
  display_name                       = "GitHub Actions OIDC"
  description                        = "GitHub's OIDC issuer, limited to one repository by ID."

  # Trust tokens signed by GitHub. STS downloads GitHub's public keys from
  # this address to check the signature, so a forged token is rejected.
  oidc {
    issuer_uri = "https://token.actions.githubusercontent.com"
  }

  # Which facts ("claims") to copy from GitHub's token onto the Google
  # identity. Step 2 below can only match on facts copied here.
  # Written in CEL, Google's small expression language.
  # - google.subject: the run's name in audit logs,
  #   e.g. repo:joeybricks/osrs_finance_hub:pull_request.
  # - event_name: what started the run (pull_request, push, ...).
  #   plan-sa is bound to pull_request.
  # - env_on_master: the GitHub Environment name, but only when the run is on
  #   master; otherwise "none". GCP itself checks the branch, so a loosened
  #   environment setting on GitHub's side is not enough to deploy.
  #   Jobs without an environment have no `environment` claim, hence has().
  attribute_mapping = {
    "google.subject"          = "assertion.sub"
    "attribute.repository_id" = "assertion.repository_id"
    "attribute.event_name"    = "assertion.event_name"
    "attribute.env_on_master" = "assertion.ref == 'refs/heads/master' && has(assertion.environment) ? assertion.environment : 'none'"
  }

  # The front door: turn away every token that is not from this repository
  # and owner. Every other GitHub repo in the world stops here. Numeric IDs,
  # because a name can be taken over after a rename or delete; an ID cannot.
  attribute_condition = "assertion.repository_id == '${var.github_repository_id}' && assertion.repository_owner_id == '${var.github_owner_id}'"
}

# ---------------------------------------------------------------------------
# 2. Who may you become? (borrowing a service account)
# ---------------------------------------------------------------------------
#
# Each binding grants roles/iam.workloadIdentityUser ON a service account TO a
# group of runs. The service account is the thing being protected here: this
# does not give plan-sa any permission, it gives PR runs permission to use
# plan-sa.
#
# principalSet = "every identity in the pool with this attribute value". The
# attribute names (event_name, env_on_master) come from attribute_mapping above.
locals {
  wif_principal_set = "principalSet://iam.googleapis.com/${google_iam_workload_identity_pool.github.name}"
}

# Any pull request run may borrow read-only plan-sa (plan.yml).
# Fork PRs get no OIDC token from GitHub, so they never reach this.
resource "google_service_account_iam_member" "plan_wif" {
  service_account_id = google_service_account.plan.name
  role               = "roles/iam.workloadIdentityUser"
  member             = "${local.wif_principal_set}/attribute.event_name/pull_request"
}

# Only jobs in the `production` environment, on master, may borrow deployer-sa.
resource "google_service_account_iam_member" "deployer_wif" {
  service_account_id = google_service_account.deployer.name
  role               = "roles/iam.workloadIdentityUser"
  member             = "${local.wif_principal_set}/attribute.env_on_master/production"
}

# Only jobs in the `killswitch` environment, on master, may borrow
# killswitch-sa (OSRS-17).
resource "google_service_account_iam_member" "killswitch_wif" {
  service_account_id = google_service_account.killswitch.name
  role               = "roles/iam.workloadIdentityUser"
  member             = "${local.wif_principal_set}/attribute.env_on_master/killswitch"
}

# The provider's full name, pasted into workflows as
# google-github-actions/auth's `workload_identity_provider` input.
output "wif_provider" {
  description = "Full provider name for google-github-actions/auth's workload_identity_provider input."
  value       = google_iam_workload_identity_pool_provider.github_actions.name
}
