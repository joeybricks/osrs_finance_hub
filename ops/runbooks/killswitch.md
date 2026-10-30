# Runbook: billing killswitch (OSRS-16, OSRS-17)

Unlinks billing from a project so spend stops. Run by hand from the GitHub
mobile app when a budget alert (runbook: [budget-alerts.md](budget-alerts.md))
says spend is out of control. It is not triggered automatically.

```
You (GitHub mobile app)
 └─ Actions > Killswitch > Run workflow   (.github/workflows/killswitch.yml)
     ├─ refuses unless branch = master, and (dry run OR typed UNLINK)
     ├─ OIDC token: repo + environment=killswitch + ref=master
     │    └─ WIF lets only that borrow killswitch-sa       (infra/iam/wif.tf)
     └─ ops/scripts/killswitch.sh --project P [--dry-run]
          ├─ refuses P unless in ALLOWED_PROJECTS
          ├─ reads billingEnabled; already off → NO-OP
          └─ unlinks, re-reads, reports UNLINKED or FAILED
               └─ job summary; GitHub notifies you about the run
```

killswitch-sa can only read the project and remove its billing link
(`infra/iam/killswitch_sa.tf`). It cannot link billing again, so restoring is
always a person's job.

## What unlinking does
- Paid usage stops. Billing-only services (BigQuery, Pub/Sub, the budget
  topic) stop working; resources are kept, nothing is deleted.
- The project is not shut down, and free-tier APIs keep answering.
- The budget emails still come: they live on the billing account.

## One-time setup (Joe, after merge)
1. `cd infra/iam && terraform apply && ./verify.sh`: adds
   `resourcemanager.projects.get` to the killswitch role (1 change in place).
2. GitHub: Settings > Environments > New environment `killswitch`. Deployment
   branches: **Selected branches**, add `master`. No secrets needed.
3. Release `develop` to `master`. The button only appears once the workflow
   is on the default branch (`develop`), and only works when run on `master`.
4. GitHub: Settings > Notifications > Actions: turn on GitHub Mobile, and untick
   "Only notify for failed workflows" to hear about successful runs too.
5. From the phone: run it with dry run ticked. Expect `DRY-RUN`, billing `True`.

## Running it
| Want | Dry run | Confirm box | Result |
| --- | --- | --- | --- |
| See what would happen | ticked | (empty) | `DRY-RUN` |
| Stop spend | unticked | `UNLINK` | `UNLINKED` (or `NO-OP` if already off) |

Locally, as an Owner: `./ops/scripts/killswitch.sh --project osrs-infra --dry-run`.

## Restore billing (outline; full steps and timings in OSRS-18 / OSRS-21)
Needs **Billing Account User** on the billing account and **Owner** on the project.
1. Find out why spend ran away and fix it first (Terraform, then apply).
2. Console: Billing > Account management > link `osrs-infra`, or
   `gcloud billing projects link osrs-infra --billing-account=<ID>`.
   Don't paste the billing account ID anywhere public.
3. Check `gcloud billing projects describe osrs-infra --format='value(billingEnabled)'`
   prints `True`, then re-run `infra/iam/verify.sh` and a `terraform plan`.

## Test log
- 2026-10-30: dry run locally against `osrs-infra` → `DRY-RUN`, billing `True`.
  Refusals checked: no project, unknown flag, `osrs-llm` (not allowed) → exit 2.
- Real unlink: pending, in a throwaway project (OSRS-18).
