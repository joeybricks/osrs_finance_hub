# Bring the hand-made resources (OSRS-8, OSRS-9) under Terraform.
# After the first apply these are no-ops; they can be removed later.

import {
  to = google_project.infra
  id = "projects/${var.infra_project_id}"
}

import {
  to = google_project.llm
  id = "projects/${var.llm_project_id}"
}

import {
  to = google_storage_bucket.tfstate
  id = "${var.infra_project_id}/osrs-infra-tfstate"
}
