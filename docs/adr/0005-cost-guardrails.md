# 0005. Cost guardrails: two projects, a budget, a killswitch and a PR gate

- **Status:** Accepted
- **Date:** 2026-11-06
- **Tickets:** OSRS-8, OSRS-13 to OSRS-21 (epic OSRS-7)

## Context

The whole project runs on a £2 monthly budget and a public repo. One wrong
Terraform attribute (a VM, a multi-region bucket, a Cloud Run service that
never scales to zero) bills from the first minute, and Google Cloud has no
general hard cap. An alerts-only budget sends emails. A "spend cap" budget
(checked 2026-11-06) pauses one service in one project, only for Gemini API,
Vertex AI, Cloud Run and Cloud Run functions, and leaves persistent
resources such as VMs and storage billing. Cost data also lags by hours, so
by the time an alert arrives the money may already be spent. Nothing here is
a production system; the aim is that a mistake costs pence, not pounds.

## Decision

Four layers, each covering a gap in the one before:

```
prevent   PR plan → free-tier policy (Conftest) → Infracost = $0      infra/policy, plan.yml
          only Terraform creates resources; no keys (ADR 0002)
   ↓ a paid resource gets through anyway (local apply, console, a free tier that changed)
detect    £2 budget on osrs-infra: email at 25/50/90/100% + forecast     infra/billing (ADR 0003)
          billing export to BigQuery for "which service?"                 ops/runbooks/billing-export.md
   ↓ spend is running away
stop      killswitch button: unlink billing from osrs-infra, from a phone  ops/scripts/killswitch.sh
   ↓ the cause is fixed
recover   relink billing by hand, verify, log it                          ops/runbooks/killswitch.md
```

- **Two projects.** `osrs-infra` is the only project with a billing account.
  `osrs-llm` is for the LLM work and uses free-tier models only, so it has no
  billing account yet, and Terraform declares it that way: a plan shows any
  link someone adds by hand. Whatever runs there can only use what works
  unbilled, so an experiment can never cost money, and the killswitch only
  has one project to stop. If paid models are ever needed, linking billing to
  `osrs-llm` is a new decision: its own budget, allow-list entry and ADR.
- **A budget that alerts, never caps.** £2, cost before credits, emailed to
  the billing admin's phone (ADR 0003). Thresholds are early (25% is 50p) so
  the first email arrives while the damage is still small.
- **A manual killswitch**, not an automatic one. `killswitch-sa` can only read
  and remove the billing link of allow-listed projects; it cannot link one. A
  person presses the button from the GitHub mobile app after a real run is
  confirmed by typing a word. Restoring billing is always a person's job, so
  a bug cannot flap billing on and off. An automatic reader of the budget
  topic was deferred: every message after a crossing still says "exceeded", so
  it needs state, and the sandbox from OSRS-18 is there to test one later.
- **A PR gate with two opinions.** The policy bans whole resource families
  that are never free and allow-lists the rest, so adding a service is a
  deliberate edit. Infracost prices the same plan in USD and fails above $0;
  it catches fixed-price resources the policy did not think of, but prices
  usage-based services at $0, so it is the check on the gate, not the gate.
- **Unlink, don't delete.** The killswitch removes the billing link and keeps
  every resource, so recovery is a relink, not a rebuild.
- **No spend-cap budget.** It cannot cover the resource families most likely
  to run away (Compute, GKE, Cloud SQL, storage), needs one budget per
  service, cannot share a scope with the alerts-only budget, and enforcement
  is "not instant" with overages billed as normal. Unlinking billing stops
  everything at once. Revisit if Cloud Run becomes the main workload: a cap
  there would add a per-service stop that needs no tap.

## What it cannot prevent

- **Spend before the alert.** Billing data lags by hours; a resource that
  bills by the second runs for all of them. The thresholds limit how much,
  not whether.
- **Spend between the alert and the tap.** The killswitch is manual. Nobody
  reads the budget topic yet.
- **Other projects on the billing account.** The budget filters on
  `osrs-infra` and the killswitch allow-lists it. A project outside Terraform
  on the same account (the old "My First Project") is unbudgeted and
  unstoppable from here.
- **Anything that bypasses the PR plan.** Only `envs/prod` is planned in CI.
  `iam/`, `billing/` and `sandbox/` are applied locally and not gated; local
  applies are not branch-locked; the console is not gated at all.
- **Usage-based cost.** Infracost shows Cloud Run, BigQuery and Pub/Sub as $0
  with no usage file, and the policy checks shape, not traffic. A free-tier
  service with heavy use is still billed.
- **Changes to the free tier.** The policy encodes today's allowances.
- **Spend outside Google Cloud.** GitHub Actions (free on a public repo, with
  its own guardrails in `CLAUDE.md`) and the Claude review workflow (runs on
  the Claude subscription, not an API key) are separate cost surfaces.

## Consequences

- Unlinking stops BigQuery and Pub/Sub too, so the billing export has a gap
  while billing is off and the budget's topic is unreachable. Emails still
  arrive: the budget lives on the billing account.
- A release takes two PRs (ADR 0004), so a change the button needs, like a
  new allow-listed project, is not live until it reaches `master`.
- Adding a new Google service means a policy edit, a Terraform change and
  usually an API to enable: three deliberate steps.
- `plan.yml` is not a required check yet, so a PR can be merged while the
  gate is red. Making it required is a follow-up.
- Cost: budgets, Pub/Sub at this volume, email, Conftest and Infracost's CLI
  are all free.
