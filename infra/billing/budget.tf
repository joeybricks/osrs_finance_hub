# Monthly budget on osrs-infra with tiered alerts (OSRS-14).
#
# A budget alerts, it never caps: spend carries on past 100%. Cost data also
# lags by hours, so an alert can arrive after the money is spent. Stopping
# spend is the killswitch's job (OSRS-16), triggered from the same topic.
#
# Where alerts go:
#   - Email to billing account admins and users (default recipients). This is
#     the phone alert (OSRS-15): Gmail push notifications. Budgets can only
#     send to email Monitoring channels, so there is no mobile-app route.
#   - The Pub/Sub topic, several times a day whatever the spend (for OSRS-16).
#
# Apply order: envs/prod first (Budget API on, topic exists), then this root.
# Runbook: ops/runbooks/budget-alerts.md.

data "google_billing_account" "main" {
  display_name = var.billing_account_name
  open         = true
}

data "google_project" "infra" {
  project_id = var.infra_project_id
}

locals {
  # Share of the budget that triggers an email, on actual spend so far.
  actual_thresholds = [0.25, 0.5, 0.9, 1.0]
}

resource "google_billing_budget" "infra" {
  billing_account = data.google_billing_account.main.id
  display_name    = "osrs-infra monthly"

  budget_filter {
    # Budgets filter by project number, not ID.
    projects        = ["projects/${data.google_project.infra.number}"]
    calendar_period = "MONTH"

    # Count cost before credits. With the default (include credits), free-trial
    # or promo credits keep net cost at 0 and no alert would ever fire, even
    # while resources run up real charges against the credit.
    credit_types_treatment = "EXCLUDE_ALL_CREDITS"
  }

  amount {
    specified_amount {
      # Must match the billing account's currency (GBP), or the API rejects it.
      currency_code = "GBP"
      units         = tostring(var.budget_gbp)
    }
  }

  dynamic "threshold_rules" {
    for_each = local.actual_thresholds
    content {
      threshold_percent = threshold_rules.value
      spend_basis       = "CURRENT_SPEND"
    }
  }

  # Forecast to pass 100% by month end: the earliest warning available.
  threshold_rules {
    threshold_percent = 1.0
    spend_basis       = "FORECASTED_SPEND"
  }

  all_updates_rule {
    pubsub_topic   = "projects/${var.infra_project_id}/topics/${var.budget_topic}"
    schema_version = "1.0"

    # Keep the default emails: they are the phone alert (OSRS-15).
    disable_default_iam_recipients = false
  }
}
