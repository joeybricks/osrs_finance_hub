# 0006. App access: IAP directly on Cloud Run, users granted in iam/

- **Status:** Accepted
- **Date:** 2026-11-19
- **Tickets:** OSRS-29, OSRS-50 to OSRS-54 (epic OSRS-23)

## Context

The app must open on Joe's phone over mobile data and refuse everyone else
(epic OSRS-23 "done when"), at £0. After OSRS-28 the service has no invoker
binding: only project Owners get in, and only with an identity token, which a
phone browser can't send.

Options (spike OSRS-51, checked 2026-11-12):

| Option | Phone works? | Cost | Notes |
| --- | --- | --- | --- |
| Cloud Run IAM invoker only | No | £0 | Every request needs an `Authorization: Bearer` identity token |
| IAP behind an external HTTPS load balancer | Yes | Monthly forwarding-rule charge | The old way; breaks £0 |
| **IAP directly on Cloud Run** (`iap_enabled`) | Yes | £0 | Normal Google sign-in, no load balancer |
| Google sign-in coded into the app | Yes | £0 | Auth becomes our code to secure |

Catch: IAP's default Google-managed OAuth client only admits users in the
project's organization. Joe's account is Gmail, which can't belong to an org.
Google's docs disagree on whether this blocks it.

## Decision

- IAP directly on the Cloud Run service: `iap_enabled = true` with
  `launch_stage = "BETA"`, as in Google's Terraform example (`envs/prod`).
- Two grants, both on the service only, in `iam/iap.tf`:
  `roles/iap.httpsResourceAccessor` for the users in `app_users`, and
  `roles/run.invoker` for the IAP service agent. Users get no invoker role.
- Users come from the gitignored `iam/terraform.tfvars`. `iam/` is planned
  locally, so no email reaches the public repo or a PR plan comment. The
  variable accepts only `user:<email>`.
- Try Joe's Gmail first. **If IAP refuses it**, create a user in the org's
  directory (Cloud Identity Free, £0) and put that in `app_users`. Rejected
  fallback: a custom OAuth client, because it needs console-only setup (the
  IAP OAuth Admin API that Terraform used is deprecated) and a client secret
  to look after.

## Consequences

- Any `iam/` plan now needs `terraform.tfvars`, or it prompts for `app_users`.
- Apply order: `envs/prod` (turns on the IAP API, which creates the service
  agent) before `iam/`. Between the two applies nobody gets in.
- If IAP were switched off (the deployer can update the service), the app
  still isn't public: Cloud Run's invoker check remains, and disabling that
  needs `run.services.setIamPolicy`, which the deployer lacks (`verify.sh`).
- IAP adds a little latency per request; fine for one user.
- Some integrations (for example Pub/Sub push) can't call an IAP-protected
  service. None are planned; a new ADR would revisit this.
- The service runs at the BETA launch stage. If `gcloud run deploy` (OSRS-30)
  changes it, the next plan shows drift: watch the first CI deploy.
