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

# Numeric IDs, not names: a renamed, deleted-and-recreated or look-alike repo
# gets a new ID, so it can never match. Public values, not secrets.
# Find them with: gh api repos/OWNER/REPO --jq '.id, .owner.id'
variable "github_repository_id" {
  description = "GitHub repository ID of joeybricks/osrs_finance_hub."
  type        = string
  default     = "1438251307"
}

variable "github_owner_id" {
  description = "GitHub user ID of the repository owner (joeybricks)."
  type        = string
  default     = "84076451"
}
