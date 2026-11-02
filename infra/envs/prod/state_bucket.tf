# Terraform state bucket, created by infra/bootstrap/create_state_bucket.sh
# (ADR 0001) and imported here. Settings must match that script.
# The name is repeated in backend.tf, which cannot use variables.
resource "google_storage_bucket" "tfstate" {
  name          = "osrs-infra-tfstate"
  project       = google_project.infra.project_id
  location      = upper(var.region)
  storage_class = "STANDARD"

  uniform_bucket_level_access = true
  public_access_prevention    = "enforced"

  versioning {
    enabled = true
  }

  # Versioning already keeps old state; soft-deleted bytes are billed.
  soft_delete_policy {
    retention_duration_seconds = 0
  }

  lifecycle_rule {
    condition {
      days_since_noncurrent_time = 30
    }
    action {
      type = "Delete"
    }
  }

  lifecycle {
    prevent_destroy = true
  }
}
