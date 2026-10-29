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
  description = "Organization joeclayton-renga-org. osrs-infra sits in it; osrs-llm has no parent."
  type        = string
  default     = "984053129458"
}
