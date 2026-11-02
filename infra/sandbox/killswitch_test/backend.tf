# Same state bucket, own prefix. Applied locally by Joe (Owner, Organization
# Project Creator, Billing Account User), never by CI: creating a project and
# linking billing are exactly what the CI accounts must not be able to do.
terraform {
  backend "gcs" {
    bucket = "osrs-infra-tfstate"
    prefix = "sandbox/killswitch-test"
  }
}
