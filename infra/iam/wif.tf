# Workload Identity Federation: GitHub Actions signs in to GCP with no keys.
#
#   GitHub job --OIDC token--> STS (checks the provider below) --federated token-->
#   IAM Credentials (checks workloadIdentityUser on the SA) --1h token--> gcloud/terraform
#
# Two gates:
# 1. The provider's attribute_condition decides which tokens are accepted at
#    all: only this repository, by numeric ID.
# 2. Each service account's workloadIdentityUser binding decides which of
#    those accepted runs may act as that account.
#
# Lives in iam/ (applied locally by an Owner) because whoever can edit this
# can widen who is trusted.
#
# A deleted pool or provider is kept for 30 days and its ID cannot be reused
# in that time, so rename with care.

resource "google_iam_workload_identity_pool" "github" {
  project                   = var.infra_project_id
  workload_identity_pool_id = "github"
  display_name              = "GitHub Actions"
  description               = "Identities from GitHub Actions runs of joeybricks/osrs_finance_hub."

  depends_on = [google_project_service.iam]
}

resource "google_iam_workload_identity_pool_provider" "github_actions" {
  project                            = var.infra_project_id
  workload_identity_pool_id          = google_iam_workload_identity_pool.github.workload_identity_pool_id
  workload_identity_pool_provider_id = "github-actions"
  display_name                       = "GitHub Actions OIDC"
  description                        = "GitHub's OIDC issuer, limited to one repository by ID."

  oidc {
    issuer_uri = "https://token.actions.githubusercontent.com"
  }

  # Claims copied from GitHub's token into the Google identity (CEL).
  # - google.subject: e.g. repo:joeybricks/osrs_finance_hub:pull_request; shows in audit logs.
  # - event_name: what triggered the run; plan-sa trusts pull_request.
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

  # Gate 1: reject every token not from this repository and owner.
  attribute_condition = "assertion.repository_id == '${var.github_repository_id}' && assertion.repository_owner_id == '${var.github_owner_id}'"
}

# Gate 2: which runs may act as which account. principalSet = every identity
# in the pool with that attribute value.
locals {
  wif_principal_set = "principalSet://iam.googleapis.com/${google_iam_workload_identity_pool.github.name}"
}

# Pull request runs plan with read-only plan-sa. Fork PRs get no OIDC token.
resource "google_service_account_iam_member" "plan_wif" {
  service_account_id = google_service_account.plan.name
  role               = "roles/iam.workloadIdentityUser"
  member             = "${local.wif_principal_set}/attribute.event_name/pull_request"
}

# Only jobs in the `production` environment on master can apply.
resource "google_service_account_iam_member" "deployer_wif" {
  service_account_id = google_service_account.deployer.name
  role               = "roles/iam.workloadIdentityUser"
  member             = "${local.wif_principal_set}/attribute.env_on_master/production"
}

# Only jobs in the `killswitch` environment on master can unlink billing (OSRS-17).
resource "google_service_account_iam_member" "killswitch_wif" {
  service_account_id = google_service_account.killswitch.name
  role               = "roles/iam.workloadIdentityUser"
  member             = "${local.wif_principal_set}/attribute.env_on_master/killswitch"
}

output "wif_provider" {
  description = "Full provider name for google-github-actions/auth's workload_identity_provider input."
  value       = google_iam_workload_identity_pool_provider.github_actions.name
}
