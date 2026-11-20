# Runbook: billing killswitch (OSRS-16, OSRS-17, OSRS-21)

Unlinks billing from a project so spend stops. Run by hand from the GitHub
mobile app when a budget alert (runbook: [budget-alerts.md](budget-alerts.md))
says spend is out of control. It is not triggered automatically.

**If it has already fired, start at [The killswitch has fired](#the-killswitch-has-fired).**
The design and its limits are in [ADR 0005](../../docs/adr/0005-cost-guardrails.md).

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
               └─ job summary; GitHub emails you about the run
```

killswitch-sa can only read the project and remove its billing link
(`infra/iam/killswitch_sa.tf`). It cannot link billing again, so restoring is
always a person's job.

## What unlinking does
- Paid usage stops. Billing-only services (BigQuery, Pub/Sub, the budget
  topic) stop working; resources are kept, nothing is deleted.
- The project is not shut down, and free-tier APIs keep answering.
- The budget emails still come: they live on the billing account.
- The billing export (BigQuery, in `osrs-infra`) stops writing while billing
  is off. Google does not backfill the gap.

Measured on an idle project (OSRS-18): reads were refused about 10 s after
the unlink and worked again about 2 s after the relink.

## Before you start (new to this repo)
You need, as a person (no service account can do the restore):

| What | Why |
| --- | --- |
| A Google account that is **Owner** of `osrs-infra` and **Billing Account User** (or Administrator) on the billing account. Today that is the billing admin's account named in [budget-alerts.md](budget-alerts.md) | linking billing needs both sides |
| `gcloud` installed and signed in: `gcloud auth login`, then `gcloud auth application-default login` for Terraform | every command below |
| This repo cloned, on an up-to-date `develop` or `master`; Terraform 1.17 | the fix and the checks |
| The GitHub run that fired (Actions > Killswitch), or the email about it | tells you who, when and which project |

The billing account's display name is `My Billing Account` (the default in
`infra/billing/variables.tf`). If a command below finds nothing by that name,
`gcloud billing accounts list` shows the real one. Its ID appears in command
output: this repo is public, so never paste it into a PR, an issue or a note.

## The killswitch has fired
You know because GitHub emailed you about a Killswitch run, or because
`gcloud billing projects describe osrs-infra` says `billingEnabled: False`.
Do these in order; do not relink first.

1. **Read the run.** Actions > Killswitch > the run > Summary. Note the
   result (`UNLINKED`, `NO-OP`, `DRY-RUN`, `REFUSED`, `FAILED`), the project,
   who triggered it and the time (UTC). `DRY-RUN` and `REFUSED` changed
   nothing: stop here. `FAILED` means the state is unknown: check it in
   step 2 and read the job log.
2. **Confirm the state.**
   ```bash
   gcloud billing projects describe osrs-infra --format='value(billingEnabled)'
   ```
   `False` means billing is off and spend has stopped. `True` with a run
   that says `UNLINKED` means someone has already relinked: find out who.
3. **Find what was spending.** The budget email names the amount, not the
   service. In order of speed:
   - Console → Billing → Reports, filter project `osrs-infra`, group by
     service. Works while billing is off.
   - The export query in [billing-export.md](billing-export.md) ("Cost by
     service, last 7 days"). BigQuery refuses queries while billing is off,
     so this one waits until after the relink.
   - Console → the service itself (Cloud Run, Compute Engine, Storage) and
     Console → IAM & Admin → Asset Inventory for a list of everything in the
     project. Compare with `infra/envs/prod`: anything not in Terraform was
     made by hand. `terraform plan` itself fails while billing is off (it
     reads the state bucket and every resource), so it waits for the relink.
   - Was it this project at all? The budget only covers `osrs-infra`. Check
     every project on the billing account: Console → Billing → Account
     management.
4. **Fix the cause in Terraform, not the console.** Remove or shrink the
   resource in `infra/envs/prod`, open a PR, let `plan.yml` show the policy
   and Infracost verdicts, merge. If the resource was made by hand, delete it
   by hand and note that in the PR. If you cannot fix it today, leave billing
   off: nothing is lost by waiting.
5. **Relink billing** ([Restore billing](#restore-billing) below). Deleting
   most resources through the API also needs billing on, so steps 5 and 6
   are one sitting: PR merged, `develop` pulled, terminal in
   `infra/envs/prod`, then relink and apply within the same minute. The
   runaway resource bills again for that minute; expect pence, not pounds.
6. **Apply the fix** from `develop` or `master`: `cd infra/envs/prod &&
   terraform plan && terraform apply`. The plan should show only your fix;
   anything else was changed by hand while billing was off.
7. **Watch for a day.** Console → Billing → Reports the next morning, then
   the export query. A crossed budget threshold may not email again this
   month (Google does not document re-alerting), so check by hand instead of
   waiting for a second email.
8. **Log it** in the [test log](#test-log) at the bottom of this file: date,
   result, cause, time off, what changed.

## Restore billing
Needs **Billing Account User** on the billing account and **Owner** on the
project. Billing can be off for as long as you like; link it only when the
cause is fixed or understood.

1. Link. Either Console → Billing → Account management → `osrs-infra` →
   Change billing → pick the account, or, with `PROJECT_ID` = `osrs-infra`:
   ```bash
   gcloud billing projects link PROJECT_ID \
     --billing-account="$(gcloud billing accounts list \
         --filter='displayName="My Billing Account"' \
         --format='value(name.basename())')"
   ```
   The ID is looked up by name, so it never needs typing. The command's
   output shows it: don't paste that anywhere public.
2. Check it took, and that paid services answer again (seconds on an idle
   project; a busy one may take longer):
   ```bash
   gcloud billing projects describe osrs-infra --format='value(billingEnabled)'   # True
   gcloud storage ls gs://osrs-infra-tfstate                                       # lists state objects
   ```
3. Check nothing else changed while billing was off:
   ```bash
   cd infra/iam && ./verify.sh        # every check PASS: roles and WIF intact
   cd ../envs/prod && terraform plan  # expect "No changes" (or only your fix)
   ```
   Terraform ignores the billing link on `osrs-infra`
   (`envs/prod/projects.tf`), so the plan does not fight you either way.
4. Check the budget still points at the topic and the export resumed: the
   next morning, Console → Billing → Budgets & alerts, and `bq ls
   osrs-infra:billing_export` (a day's gap in the export is expected).
5. If the link command is refused: you lack a role (ask the billing admin),
   the account is closed (`gcloud billing accounts list` shows `open`), or
   the project is in `DELETE_REQUESTED` (restore it in Console → IAM & Admin
   → Manage resources first).

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

Pick branch `master` in the Run workflow dialog; any other branch is refused
before sign-in. Locally, as an Owner:
`./ops/scripts/killswitch.sh --project osrs-infra --dry-run`.

If the button itself is broken (workflow red before the Killswitch step,
WIF refused), unlink by hand: Console → Billing → Account management →
`osrs-infra` → Disable billing. Same effect, same restore.

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
  minutes, before the canary was read, so it was repeated. The button dry
  run on the test project (step 5) was skipped; dry runs were already proven
  on `osrs-infra`. Times are for an idle project with one object; real
  workloads may take longer to recover.
- Real events on `osrs-infra`: none yet. Add them here (date, result, cause,
  time off, fix).
