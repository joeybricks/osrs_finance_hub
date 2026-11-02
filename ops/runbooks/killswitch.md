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
4. GitHub: Settings > Notifications > Actions: untick "Only notify for failed
   workflows" to hear about successful runs too. The result arrives by
   **email**; GitHub Mobile sends no push for workflow runs (checked
   2026-11-02), so the email is the notification channel.
5. From the phone: run it with dry run ticked. Expect `DRY-RUN`, billing `True`.

## Running it
| Want | Dry run | Confirm box | Result |
| --- | --- | --- | --- |
| See what would happen | ticked | (empty) | `DRY-RUN` |
| Stop spend | unticked | `UNLINK` | `UNLINKED` (or `NO-OP` if already off) |

Locally, as an Owner: `./ops/scripts/killswitch.sh --project osrs-infra --dry-run`.

## Restore billing (full steps in OSRS-21)
Needs **Billing Account User** on the billing account and **Owner** on the project.
1. Find out why spend ran away and fix it first (Terraform, then apply).
2. Console: Billing > Account management > link `osrs-infra`, or (with
   `PROJECT_ID` = `osrs-infra`):
   ```
   gcloud billing projects link PROJECT_ID \
     --billing-account="$(gcloud billing accounts list \
         --filter='displayName="My Billing Account"' \
         --format='value(name.basename())')"
   ```
   The ID is looked up by name, so it never needs typing. The command's
   output shows it: don't paste that anywhere public.
3. Check `gcloud billing projects describe osrs-infra --format='value(billingEnabled)'`
   prints `True`, then re-run `infra/iam/verify.sh` and a `terraform plan`.

## Live test in a throwaway project (OSRS-18)
Never run a real unlink against `osrs-infra` as a test. Note the time (UTC) at
each step; the timings go in the test log.

1. Merge the PR that adds `osrs-killswitch-test-1102` to the allow list and the
   workflow choices, and release `develop` to `master`.
2. Create the project (billing on, a canary object, the killswitch role):
   `cd infra/sandbox/killswitch_test && terraform plan && terraform apply`.
   Plan output shows the billing account ID: don't paste it anywhere public.
3. `./infra/iam/verify.sh`: the test-project section must pass.
4. Baseline: `gcloud storage cat gs://osrs-killswitch-test-1102-canary/canary.txt`
   prints `billing is on`.
5. Phone: Killswitch, project `osrs-killswitch-test-1102`, dry run ticked →
   `DRY-RUN`, billing `True`.
   Leave a minute or two between steps 6 and 9: don't relink until step 7
   has seen the read fail.
6. Phone: same, dry run unticked, `UNLINK` typed → `UNLINKED`.
7. Repeat step 4: expect a billing-disabled error. Retry every minute or so
   until it fails; note how long it took.
8. Phone: repeat step 6 → `NO-OP` (idempotent).
9. Restore: link billing again (command under "Restore billing", with
   `PROJECT_ID` = the test project), then repeat step 4 until it prints
   `billing is on`; note how long it took.
10. `terraform destroy` in `infra/sandbox/killswitch_test` (expect 7 to
    destroy; the project goes to `DELETE_REQUESTED` for 30 days), then a PR
    that removes the project from the allow list and workflow, and adds the
    result here.

### Running the test again
The sandbox root is kept, empty between tests. A deleted project ID can't be
reused, so pick a new dated ID (e.g. `osrs-killswitch-test-MMDD`) and set it in:
1. `infra/sandbox/killswitch_test/variables.tf` (`test_project_id`)
2. `ops/scripts/killswitch.sh` (`ALLOWED_PROJECTS`)
3. `.github/workflows/killswitch.yml` (`project` options)
4. `infra/iam/verify.sh` (`TEST_PROJECT`)

Then follow the steps above. Remove it from 2 and 3 after the destroy.

## Test log
- 2026-10-30: dry run locally against `osrs-infra` → `DRY-RUN`, billing `True`.
  Refusals checked: no project, unknown flag, `osrs-llm` (not allowed) → exit 2.
- 2026-11-02: OSRS-17 phone dry runs against `osrs-infra` (16:22, 16:28 UTC)
  → `DRY-RUN`, billing `True`. A run from `develop` (16:19) was refused by the
  branch check, as designed.
- 2026-11-02: **live test in `osrs-killswitch-test-1102`** (OSRS-18), all
  from the phone on `master`. Times UTC.

  | Step | Result | Time |
  | --- | --- | --- |
  | Real unlink (button) | `UNLINKED`, billing `True` → `False` | run started 17:23:12, result 17:23:28 |
  | Spend stops | canary read refused: `HTTPError 403: The billing account for the owning project is disabled in state absent` | 17:23:38 (10 s after unlink, 26 s after tap) |
  | Run again | `NO-OP`, billing `False` → `False` | 17:25:33 |
  | Relink by hand | billing `True` | 17:26:31 |
  | Service back | canary reads `billing is on` | 17:26:33 (2 s after relink) |
  | Destroy | 7 destroyed; project `DELETE_REQUESTED` | after 17:27 |

  Notes: an earlier real unlink (17:11:52, `UNLINKED`) was relinked within two
  minutes, before the canary was checked, so it was repeated. The button dry
  run on the test project (step 5) was skipped; dry runs were already proven
  on `osrs-infra`. Times are for an idle project with one object; real
  workloads may take longer to recover.
