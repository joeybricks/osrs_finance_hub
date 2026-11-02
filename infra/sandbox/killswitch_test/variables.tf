variable "test_project_id" {
  description = "Throwaway project for the live killswitch test. Must match ALLOWED_PROJECTS in ops/scripts/killswitch.sh and the workflow's project choices."
  type        = string
  # Dated so a rerun can pick a new one: a deleted project ID can't be reused.
  default = "osrs-killswitch-test-1102"
}

variable "infra_project_id" {
  description = "Project that holds killswitch-sa and pays quota for these API calls."
  type        = string
  default     = "osrs-infra"
}

variable "org_id" {
  description = "Organization joeclayton-renga-org; parent of every project."
  type        = string
  default     = "984053129458"
}

variable "region" {
  description = "Single region for the whole project (ADR 0001): inside GCS Always Free."
  type        = string
  default     = "us-central1"
}

# Looked up by name so the billing account ID stays out of this public repo.
variable "billing_account_name" {
  description = "Display name of the Cloud Billing account to link the test project to."
  type        = string
  default     = "My Billing Account"
}
