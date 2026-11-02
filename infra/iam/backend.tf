# Same state bucket as envs/prod, separate prefix, so IAM has its own state.
# Applied locally by a project Owner, never by CI: whoever can apply IAM can
# grant themselves any role, so the CI deployer must not be able to.
terraform {
  backend "gcs" {
    bucket = "osrs-infra-tfstate"
    prefix = "iam"
  }
}
