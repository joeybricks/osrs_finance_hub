variable "infra_project_id" {
  description = "Project with billing; the budget watches it and its topic receives the alerts."
  type        = string
  default     = "osrs-infra"
}

variable "region" {
  description = "Single region for the whole project (ADR 0001)."
  type        = string
  default     = "us-central1"
}

# Looked up by name so the billing account ID stays out of this public repo.
# If the account is renamed in the console, change this too.
variable "billing_account_name" {
  description = "Display name of the Cloud Billing account osrs-infra is linked to."
  type        = string
  default     = "My Billing Account"
}

variable "budget_gbp" {
  description = "Monthly budget in GBP (whole pounds). The project-wide cost limit."
  type        = number
  default     = 2
}

variable "budget_topic" {
  description = "Pub/Sub topic for budget messages, made in envs/prod (pubsub.tf)."
  type        = string
  default     = "budget-alerts"
}
