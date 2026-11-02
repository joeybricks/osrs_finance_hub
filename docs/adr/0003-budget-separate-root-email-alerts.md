# 0003. Budget in its own Owner-applied root; email as the phone alert

- **Status:** Accepted
- **Date:** 2026-10-30
- **Tickets:** OSRS-14, OSRS-15 (epic OSRS-7)

## Context

The £2 budget (`google_billing_budget`) is a resource on the **billing
account**, not on a project. The billing account has no parent: it sits
outside the org `984053129458`.

- For CI's `plan-sa` to refresh a budget in `envs/prod`, it would need
  `roles/billing.viewer` on the whole billing account. That role includes
  `billing.accounts.getPaymentInfo`, spend and pricing.
- A narrower custom role isn't possible. Billing-account custom roles must be
  created in the org that owns the account, and `billing.budgets.get` is only
  at TESTING support for custom roles.
- OSRS-15 asked for alerts on the phone. Budgets can only send to **email**
  Cloud Monitoring channels; mobile app, SMS and Slack aren't supported. Any
  other push route needs code reading the Pub/Sub topic and keeping state,
  because every message after a crossing still reports the threshold.

## Decision

- New root `infra/billing/` (state prefix `billing/`) holds the budget. It is
  planned and applied locally by the billing admin, like `infra/iam/`. CI
  accounts get no billing-account access; `verify.sh` checks this.
- The Pub/Sub topic and an audit subscription stay in `envs/prod`, visible to CI
  (`roles/pubsub.viewer`). The budget refers to the topic by name.
- Phone alerts are the budget's default emails to billing admins, with Gmail
  push notifications (Joe's choice). No relay function.

## Consequences

- No PR plan for budget changes; run `terraform plan` in `infra/billing`
  locally. Apply order: `iam` → `envs/prod` → `billing`.
- The billing account ID stays out of the repo (looked up by display name),
  but appears in local plan output and in state.
- The email path can't be tested synthetically; only real spend triggers it.
  The topic path is tested with `ops/scripts/budget-alert-test.sh`.
- Moving the billing account into the org later would allow a custom
  read-only role and moving the budget back under CI plan.
- Cost: budgets, Pub/Sub at this volume and email are free.
