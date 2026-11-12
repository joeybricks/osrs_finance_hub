variable "infra_project_id" {
  description = "Project with billing: state bucket and app infrastructure."
  type        = string
  default     = "osrs-infra"
}

variable "region" {
  description = "Single region for the whole project (ADR 0001): inside GCS Always Free."
  type        = string
  default     = "us-central1"
}

variable "llm_project_id" {
  description = "Project with no billing account, for free-tier LLM work."
  type        = string
  default     = "osrs-llm"
}

variable "org_id" {
  description = "Organization joeclayton-renga-org; parent of both projects."
  type        = string
  default     = "984053129458"
}

# Created in iam/ (service_accounts.tf), so referenced here by account ID.
variable "runtime_service_account_id" {
  description = "Account ID of the identity Cloud Run runs the app as (iam/)."
  type        = string
  default     = "runtime-sa"
}

locals {
  runtime_sa_email = "${var.runtime_service_account_id}@${var.infra_project_id}.iam.gserviceaccount.com"
}
