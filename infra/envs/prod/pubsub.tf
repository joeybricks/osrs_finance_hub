# Budget notifications (OSRS-14). The budget (budget.tf) publishes to this topic
# several times a day, whether or not a threshold has been crossed, so every
# reader must check the message's alertThresholdExceeded field itself.
#
# No publisher grant here on purpose. When an Owner creates a budget that names
# this topic, the Budgets API grants Google's budget service account
# roles/pubsub.publisher on it (the creator needs pubsub.topics.setIamPolicy).
# Managing that binding here would also make CI need topics.getIamPolicy.
# The runbook checks the grant: ops/runbooks/budget-alerts.md.
#
# Phone alerts (OSRS-15) are email: the budget's default email to billing
# admins, with Gmail push notifications on. Budgets can only send to email
# Monitoring channels, so nothing on the phone reads this topic yet; the
# killswitch (OSRS-16) will.
resource "google_pubsub_topic" "budget_alerts" {
  project = google_project.infra.project_id
  name    = "budget-alerts"

  labels = {
    purpose    = "budget-alerts"
    managed-by = "terraform"
  }

  depends_on = [google_project_service.pubsub]
}

# Pull subscription that keeps the last 7 days of budget messages, so they can
# be read by hand: the synthetic test (ops/scripts/budget-alert-test.sh) and
# checking that real budget messages are arriving. Nothing pulls it
# automatically; messages just expire after 7 days. Free: storage for unacked
# messages up to 7 days is not billed separately, and volume is tiny.
resource "google_pubsub_subscription" "budget_alerts_audit" {
  project = google_project.infra.project_id
  name    = "budget-alerts-audit"
  topic   = google_pubsub_topic.budget_alerts.id

  message_retention_duration = "604800s" # 7 days, the maximum
  ack_deadline_seconds       = 60

  # Default is to delete a subscription after 31 days with no pulls. This one
  # is read rarely, so it must never expire.
  expiration_policy {
    ttl = ""
  }

  labels = {
    purpose    = "budget-alerts"
    managed-by = "terraform"
  }
}
