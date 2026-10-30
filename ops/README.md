# ops

Operational scripts, checks and runbooks: cost digests, manual steps and
housekeeping.

## Runbooks
Manual steps that can't be done through Terraform.

- [Billing export to BigQuery](runbooks/billing-export.md) (OSRS-13)
- [Budget and phone alerts](runbooks/budget-alerts.md) (OSRS-14, OSRS-15)

## Scripts
- [`scripts/budget-alert-test.sh`](scripts/budget-alert-test.sh): synthetic
  budget message through the `budget-alerts` topic (OSRS-15).
