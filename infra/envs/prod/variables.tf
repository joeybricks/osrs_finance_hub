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
