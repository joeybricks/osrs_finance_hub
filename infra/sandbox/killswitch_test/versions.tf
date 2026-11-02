# Allowed versions. The exact provider build used is fixed by .terraform.lock.hcl.
terraform {
  # Patch releases of 1.17 only.
  required_version = "~> 1.17.0"

  required_providers {
    google = {
      source = "hashicorp/google"
      # Any 8.x from 8.9; never 9.0 (major versions can break).
      version = "~> 8.9"
    }
  }
}
