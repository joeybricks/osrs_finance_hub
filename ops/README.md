# ops

Operational scripts and runbooks: manual steps, incident procedures and
housekeeping.

## Runbooks
Manual steps that can't be done through Terraform.

- [Billing export to BigQuery](runbooks/billing-export.md) (OSRS-13): the
  console step Terraform can't do, and the cost-by-service query.
- [Budget and phone alerts](runbooks/budget-alerts.md) (OSRS-14, OSRS-15,
  OSRS-21): set-up, what each threshold means, **a budget alert fired**.
- [Billing killswitch](runbooks/killswitch.md) (OSRS-16 to OSRS-18, OSRS-21):
  how to press it, **the killswitch has fired**, **restore billing**, live test.

Why the guardrails look like this, and what they can't prevent:
[ADR 0005](../docs/adr/0005-cost-guardrails.md).

## Scripts
- [`scripts/budget-alert-test.sh`](scripts/budget-alert-test.sh): synthetic
  budget message through the `budget-alerts` topic (OSRS-15).
- [`scripts/killswitch.sh`](scripts/killswitch.sh): unlink billing from an
  allow-listed project; `--dry-run` reports only (OSRS-16). Button:
  `.github/workflows/killswitch.yml` (OSRS-17).
- [`scripts/smoke-container.sh`](scripts/smoke-container.sh): build and run
  the app container with `compose.yaml`, check `/health`, a scan, the
  User-Agent and that only the startup prime hit the wiki; `--keep` leaves it
  running for a browser check (OSRS-42).
