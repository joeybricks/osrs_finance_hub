variable "infra_project_id" {
  description = "Project with billing: holds the service accounts."
  type        = string
  default     = "osrs-infra"
}

variable "region" {
  description = "Single region for the whole project (ADR 0001)."
  type        = string
  default     = "us-central1"
}

variable "llm_project_id" {
  description = "Project with no billing account, for free-tier LLM work."
  type        = string
  default     = "osrs-llm"
}

variable "state_bucket" {
  description = "Terraform state bucket (infra/bootstrap, imported in envs/prod)."
  type        = string
  default     = "osrs-infra-tfstate"
}
