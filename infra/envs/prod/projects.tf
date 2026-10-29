# Both projects were created by hand in OSRS-8 and imported here (imports.tf).

resource "google_project" "infra" {
  name       = "osrs-infra"
  project_id = var.infra_project_id
  org_id     = var.org_id

  lifecycle {
    prevent_destroy = true
    # Billing is linked by hand. Ignored so the killswitch (OSRS-16) can unlink
    # it in an emergency without the next apply quietly re-linking it.
    ignore_changes = [billing_account]
  }
}

resource "google_project" "llm" {
  name       = "osrs-llm"
  project_id = var.llm_project_id
  # No billing_account on purpose: this project must never have billing. If
  # anyone links one, the next plan shows Terraform removing it.

  lifecycle {
    prevent_destroy = true
  }
}
