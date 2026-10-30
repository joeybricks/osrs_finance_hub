# Runbook: budget and phone alerts (OSRS-14, OSRS-15)

A £2 monthly budget on `osrs-infra` emails you as spend climbs, and publishes
its status to a Pub/Sub topic for the killswitch (OSRS-16).

```
Cloud Billing account
 └─ budget "osrs-infra monthly" £2, cost before credits   (infra/billing/budget.tf)
     ├─ email to billing admins/users at 25/50/90/100% actual + 100% forecast
     │    └─ Gmail on your phone: push notification            (OSRS-15)
     └─ Pub/Sub topic budget-alerts, several times a day        (infra/envs/prod/pubsub.tf)
          ├─ subscription budget-alerts-audit: 7 days of messages, read by hand
          └─ killswitch function (OSRS-16, not built yet)
```

**A budget alerts, it never caps.** Spend carries on past 100%. Cost data lags
by hours, so an email can arrive after the money is spent.

## Why email for the phone
Budgets can only send to **email** Cloud Monitoring channels, so the Google
Cloud mobile app can't receive them directly. Anything else (Discord, ntfy)
needs a function reading the topic and remembering what it already sent,
because every message after a crossing still says the threshold is exceeded.
Joe chose email: no compute, no cost.

## Who can do it
- **Billing Account Administrator** on the billing account (creates the budget).
- **Owner** on `osrs-infra`: enables the APIs, and lets the Budgets API grant
  its service account publish rights on the topic (`pubsub.topics.setIamPolicy`).

## Order (after merge, from an up-to-date `develop`)
```bash
git switch develop && git pull
cd infra/iam       && terraform apply && ./verify.sh   # 2 add: pubsub.viewer for CI
cd ../envs/prod    && terraform apply                  # 4 add: 2 APIs, topic, subscription
cd ../billing      && terraform init && terraform apply  # 1 add: the budget
```
`envs/prod` must go first: the budget needs the Budgets API on and the topic in
place. The billing plan prints the billing account ID; don't paste it into a
PR or notes (public repo).

## Phone set-up (once)
1. Gmail app → Settings → your account (`joeclayton.renga@gmail.com`, the
   billing admin) → **Notifications: All**. With "High priority only", Gmail
   may stay silent.
2. When the first budget email arrives, make a filter on its sender: **Never
   send to spam**, **Always mark as important**, category **Primary**.
3. Optional: Google Cloud console → Billing → Budgets & alerts → the budget →
   confirm the email recipients.

## Check it worked
```bash
# Budget exists, with 5 thresholds. Quota project needed for user credentials.
gcloud billing budgets list --billing-account="$(gcloud billing projects describe osrs-infra \
  --format='value(billingAccountName.basename())')" --billing-project=osrs-infra \
  --format='table(displayName, amount.specifiedAmount.units, thresholdRules.len())'

# The Budgets API granted its service account publisher on the topic.
gcloud pubsub topics get-iam-policy budget-alerts --project=osrs-infra
#   expect roles/pubsub.publisher for a Google-managed account
#   (billing-budget-alert@system.gserviceaccount.com expected; note the actual one)

# Topic path end to end (synthetic message, no email).
./ops/scripts/budget-alert-test.sh

# Real budget messages: the first can take several hours. Shown, not acked.
gcloud pubsub subscriptions pull budget-alerts-audit --project=osrs-infra --limit=5
```

The email side can't be tested synthetically: the budget service only emails
when real spend crosses a threshold. The first real test is the 25% email
(50p). Until then, rely on the console showing you as a recipient.

## What each alert means
| Alert | Spend | Do |
| --- | --- | --- |
| 25% actual | 50p | Look at the billing export: which service? Expected? |
| 50% actual | £1 | Find the cause today; stop or delete the resource. |
| 90% actual | £1.80 | Stop the resource now. Consider unlinking billing by hand. |
| 100% actual | £2 | Over budget. Unlink billing (killswitch, OSRS-16, or by hand). |
| 100% forecast | trend | On course for £2 by month end: same as 50%. |

Unlink by hand: Console → Billing → Account management → `osrs-infra` →
Disable billing. This stops paid services. Terraform ignores the billing link
(`envs/prod/projects.tf`), so the next apply won't re-link it.

## Undo
- Budget: `terraform destroy` in `infra/billing` (alerts stop).
- Topic/subscription: remove from `envs/prod/pubsub.tf` and apply. Remove the
  budget's `all_updates_rule` first, or the budget points at a missing topic.

## Gotchas
- **Credits:** the budget counts cost **before** credits
  (`EXCLUDE_ALL_CREDITS`). With credits included, free-trial or promo credits
  hold net cost at £0 and nothing would ever fire.
- **Every Pub/Sub message looks alarming after a crossing.**
  `alertThresholdExceeded` stays set on every later message that month, so
  readers must remember what they already acted on.
- **Synthetic messages** carry `synthetic=true` and
  `billingAccountId=synthetic`. The killswitch must ignore them.
- **Quota project:** the Budgets API rejects user credentials with no quota
  project. `infra/billing/providers.tf` sets one; for `gcloud`, pass
  `--billing-project=osrs-infra`.
- CI never sees the budget: `infra/billing` is applied only by you.
