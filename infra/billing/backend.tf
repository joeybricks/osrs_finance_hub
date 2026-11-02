# Same state bucket, separate prefix, so billing-account resources have their
# own state. Applied locally by an Owner who is also Billing Account
# Administrator, never by CI: the CI accounts have no access to the billing
# account at all (reading a budget needs roles/billing.viewer on the whole
# account, which includes payment details and spend).
terraform {
  backend "gcs" {
    bucket = "osrs-infra-tfstate"
    prefix = "billing"
  }
}
