# 0002. Keyless CI auth to GCP with Workload Identity Federation

- **Status:** Accepted
- **Date:** 2026-10-30
- **Ticket:** OSRS-12 (epic OSRS-7)

## Context

GitHub Actions needs to call GCP: `terraform plan` on PRs now, `terraform
apply` after merge and the billing killswitch later. The repo is public. A
service account key file stored as a GitHub secret never expires, can be
copied out of any workflow that can read it, and the org policy
`iam.disableServiceAccountKeyCreation` already blocks creating one.

## Decision

- Use Workload Identity Federation: a pool `github` and an OIDC provider
  `github-actions` in `osrs-infra`, trusting `token.actions.githubusercontent.com`.
  GitHub's short-lived OIDC token is exchanged (STS) for a federated token,
  which impersonates a service account for one hour.
- The provider's `attribute_condition` accepts only this repository and owner,
  by **numeric ID**. A name can be reused after a rename or delete; an ID cannot.
- Each service account is bound to one kind of run:
  - `plan-sa`: `event_name == pull_request`
  - `deployer-sa`: GitHub Environment `production` **and** ref `master`
  - `killswitch-sa`: GitHub Environment `killswitch` **and** ref `master`

  Branch and environment are combined in one mapped attribute
  (`env_on_master`), so GCP checks the branch itself rather than relying only
  on GitHub's environment branch rules.
- The WIF config lives in `infra/iam/`, applied locally by an Owner, because
  anyone who can edit it can widen who is trusted.

## Consequences

- No key files exist anywhere; there is nothing to rotate or leak.
- Recreating the repo changes its ID: update `github_repository_id` and re-apply
  `infra/iam`.
- Any PR from a branch in this repo can plan as `plan-sa`, which is read-only.
  Fork PRs get no OIDC token and are skipped.
- `plan-sa` can read every object in the state bucket, including `iam/` state.
  Acceptable today (no secrets in state); revisit if secrets ever reach state.
- A deleted pool or provider keeps its ID for 30 days.
- Cost: WIF, STS and IAM Credentials have no charge.
