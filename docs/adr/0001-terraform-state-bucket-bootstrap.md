# 0001. Bootstrap the Terraform state bucket by script

- **Status:** Accepted
- **Date:** 2026-10-29
- **Ticket:** OSRS-9 (epic OSRS-7)

## Context

Terraform keeps its state in a GCS bucket, and that bucket has to exist before
`terraform init` can run. Terraform cannot create the bucket it stores its own
state in. The project rule is that every cloud resource is created through
Terraform and CI, never by hand.

## Decision

- Create one bucket, `gs://osrs-infra-tfstate` in project `osrs-infra`, with the
  committed script `infra/bootstrap/create_state_bucket.sh`, run once by a person.
- Import the bucket into Terraform in OSRS-10. After that Terraform owns it and
  the script is only a record and a settings check.
- Region `us-central1`: inside the GCS Always Free tier, has the widest range of
  services, and becomes the single region for the project (OSRS-19 enforces it).
- Settings: Standard class, versioning on, noncurrent versions deleted after 30
  days, uniform bucket-level access, public access prevention enforced, soft
  delete off (versioning already keeps old state; soft-deleted bytes are billed).
- State for each environment lives under its own prefix (`prod/`). The GCS
  backend's built-in lock object is used; no separate lock store.

## Consequences

- One resource exists outside Terraform until OSRS-10. Its Terraform config must
  match the script's settings exactly or the first plan after import shows drift.
- The script is idempotent and verifies settings, so it can be re-run safely.
- `terraform plan` takes a lock by writing an object to this bucket, so the
  read-only plan service account (OSRS-11) still needs object create/delete on
  this bucket, or plans must run with `-lock=false`.
- The bucket name is public (public repo). Access is IAM-only and public access
  is blocked, so knowing the name grants nothing.
- Cost: state is a few KB in a free-tier region; expected £0.
