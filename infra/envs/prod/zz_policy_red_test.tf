# TEMPORARY: proves the free-tier policy and Infracost both go red on a paid
# resource (OSRS-19 and OSRS-20 done criteria). Plan only, never applied.
# This commit is reverted on the same PR.
resource "google_compute_instance" "policy_red_test" {
  project      = google_project.infra.project_id
  name         = "policy-red-test"
  machine_type = "e2-micro"
  zone         = "us-central1-a"

  boot_disk {
    initialize_params {
      image = "debian-cloud/debian-12"
    }
  }

  network_interface {
    network = "default"
  }
}
