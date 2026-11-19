# Who can open the app (OSRS-29, ADR 0006). IAP is switched on in
# envs/prod/cloud_run.tf; this file holds the two grants it needs:
#
#   browser --Google sign-in--> IAP: is the user in var.app_users?
#     --request signed as the IAP service agent--> Cloud Run: may it invoke?
#     --> app
#
# The users live here, not in envs/prod, because envs/prod plans are posted on
# public PRs. iam/ is planned and applied locally; the addresses come from the
# gitignored terraform.tfvars (see terraform.tfvars.example).
#
# The service and the IAP API are created in envs/prod: apply envs/prod first.

data "google_project" "infra" {
  project_id = var.infra_project_id
}

locals {
  # Google-managed identity IAP uses to call the service. Created when
  # iap.googleapis.com is turned on (envs/prod/apis.tf).
  iap_service_agent = "serviceAccount:service-${data.google_project.infra.number}@gcp-sa-iap.iam.gserviceaccount.com"
}

# Gate 1: let these users through the IAP sign-in, on this service only.
resource "google_iap_web_cloud_run_service_iam_member" "app_users" {
  for_each = toset(var.app_users)

  project                = var.infra_project_id
  location               = var.region
  cloud_run_service_name = var.cloud_run_service
  role                   = "roles/iap.httpsResourceAccessor"
  member                 = each.value
}

# Gate 2: Cloud Run still checks run.routes.invoke on every request. IAP
# forwards requests as its service agent, so the agent needs the invoker role.
# Users get no invoker role themselves: they can only come in through IAP.
resource "google_cloud_run_v2_service_iam_member" "iap_invoker" {
  project  = var.infra_project_id
  location = var.region
  name     = var.cloud_run_service
  role     = "roles/run.invoker"
  member   = local.iap_service_agent
}
