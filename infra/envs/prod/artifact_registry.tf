# Docker repository for the app's container images (OSRS-43). Cloud Run
# pulls from here (OSRS-28). Same region as everything else (ADR 0001), so
# pulls by Cloud Run are free.
#
# Free tier is 0.5 GB of storage. One image is ~170 MB, but versions share
# layers (the Python base is stored once), so the 5 kept below fit.
#
# Cleanup policies, run by Google roughly once a day:
#   KEEP   keep-recent-5      the 5 newest versions of each image
#   DELETE delete-untagged    untagged versions older than 7 days
#   DELETE delete-old-tagged  tagged versions older than 7 days
# KEEP beats DELETE, so a version is deleted only when it is past 7 days AND
# not among the 5 newest. A KEEP rule on its own deletes nothing.
resource "google_artifact_registry_repository" "images" {
  project       = google_project.infra.project_id
  location      = var.region
  repository_id = "osrs-finance-hub"
  description   = "Container images for osrs_finance_hub (Cloud Run)."
  format        = "DOCKER"

  # false = the policies really delete. true only logs what they would delete
  # (the free-tier policy in infra/policy rejects true).
  cleanup_policy_dry_run = false

  cleanup_policies {
    id     = "keep-recent-5"
    action = "KEEP"
    most_recent_versions {
      keep_count = 5
    }
  }

  cleanup_policies {
    id     = "delete-untagged"
    action = "DELETE"
    condition {
      tag_state  = "UNTAGGED"
      older_than = "604800s" # 7 days
    }
  }

  cleanup_policies {
    id     = "delete-old-tagged"
    action = "DELETE"
    condition {
      tag_state  = "TAGGED"
      older_than = "604800s" # 7 days
    }
  }

  # Automatic scanning bills per image pushed. Off explicitly, so turning on
  # the Container Scanning API later can't start charging for this repo.
  vulnerability_scanning_config {
    enablement_config = "DISABLED"
  }

  depends_on = [google_project_service.artifactregistry]
}
