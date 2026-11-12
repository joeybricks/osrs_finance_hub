# infra

Terraform for the cloud resources. Every resource is created through Terraform
and CI, never by hand, and must stay inside free tiers.

The one exception is the Terraform state bucket, which has to exist before
Terraform can run. It is made by `bootstrap/` and then imported (OSRS-10).

| Path | Contents |
| --- | --- |
| `bootstrap/` | One-off script that creates the state bucket `gs://osrs-infra-tfstate` |
| `envs/prod/` | Root module for the prod environment; state under `prod/` in the bucket |
| `iam/` | Service accounts and their roles (OSRS-11); state under `iam/`. Applied locally by an Owner, never by CI |
| `billing/` | Budget on the billing account (OSRS-14); state under `billing/`. Applied locally by the billing admin, never by CI |
| `sandbox/killswitch_test/` | Throwaway project for the live killswitch test (OSRS-18); state under `sandbox/killswitch-test/`. Applied and destroyed locally by Joe, never by CI. Kept for reruns; empty between tests |
| `policy/` | Free-tier policy (OSRS-19): Rego rules Conftest runs against every PR plan, plus their unit tests. See "Free-tier gate" |
| `modules/` | Reusable modules (none yet) |
| `.tflint.hcl` | TFLint rules: Terraform recommended preset + Google ruleset |

## Service accounts (`iam/`)

| Account | Job | Can | Cannot |
| --- | --- | --- | --- |
| `plan-sa` | `terraform plan` on PRs | read both projects, the state bucket, BigQuery metadata, enabled APIs, Pub/Sub topics and the Artifact Registry repo | write anything, read BigQuery rows, pull Pub/Sub messages, see the billing account or budget |
| `deployer-sa` | `terraform apply` of `envs/prod` and app deploys after merge (OSRS-27) | write `prod/` state (IAM condition), update state bucket settings, read BigQuery metadata, enabled APIs, Pub/Sub topics and the Artifact Registry repo; push images to the `osrs-finance-hub` repo, deploy Cloud Run revisions (`run.developer`), act as `runtime-sa` only | IAM (incl. making the service public), enable APIs, billing, delete images, act as any other account, change projects or datasets, read BigQuery rows, touch `iam/` state |
| `killswitch-sa` | unlink billing when over budget (OSRS-16, button OSRS-17) | `deleteBillingAssignment` + `projects.get` on `osrs-infra` | re-link billing, anything else |
| `runtime-sa` | identity of the app running on Cloud Run (OSRS-44) | nothing yet (E3 adds BigQuery read) | everything; no GitHub run may act as it |

Roles are hand-picked built-in roles where one is narrow enough, and custom
roles (`tfStateBucketEditor`, `billingKillswitch`) where every built-in role
also allows something dangerous. No service account keys: CI uses Workload
Identity Federation (below).

## CI sign-in: Workload Identity Federation (`iam/wif.tf`, ADR 0002)

```
GitHub job (id-token: write) --OIDC token--> STS: provider accepts only this repo (by ID)
  --federated token--> IAM Credentials: is the run bound to this SA? --1h token--> terraform
```

| Account | Runs that may use it |
| --- | --- |
| `plan-sa` | any `pull_request` run (`plan.yml`) |
| `deployer-sa` | jobs in GitHub Environment `production` on `master` |
| `killswitch-sa` | jobs in GitHub Environment `killswitch` on `master` |

The org policy `iam.disableServiceAccountKeyCreation` blocks key creation in
both projects; `verify.sh` checks it and that no account has a key.


`iam/` is applied locally by a project Owner, never by CI. Merge first, then
apply from an up-to-date `develop` so the cloud matches the branch:

```bash
git checkout develop && git pull
cd infra/iam && terraform plan && terraform apply
./verify.sh      # Policy Troubleshooter: every check must PASS
```

## Budget (`billing/`)

The budget lives on the billing account, outside both projects. CI can't read
it: that needs `roles/billing.viewer` on the whole account (payment details,
spend), and custom roles can't be used because the billing account is not in
the org. So `billing/` is a separate root, planned and applied only by the
billing admin, after `envs/prod` (Budget API on, topic created).
The provider sets a quota project (`user_project_override`), which the Budgets
API requires for user credentials. Steps and checks:
[`ops/runbooks/budget-alerts.md`](../ops/runbooks/budget-alerts.md).

## Free-tier gate (`policy/`, OSRS-19)

`plan.yml` saves the PR plan, converts it to JSON and runs
[Conftest](https://www.conftest.dev/) over it. Conftest evaluates rules
written in Rego, the language of Open Policy Agent (OPA), against any JSON.
A rule that matches produces a `deny` message and fails the job.

```
terraform plan -out=tfplan  →  terraform show -json tfplan  →  conftest test plan.json
                                 resource_changes[].{type, change.actions, change.after}
```

Only resources being created or updated are judged, so deleting a banned
resource passes. The rules in `policy/free_tier.rego`:

| Rule | Why |
| --- | --- |
| Banned families: `google_compute_*`, `google_sql_*`, `google_container_*`, `google_redis_*`, `google_filestore_*` | Compute Engine, load balancers, Cloud NAT, Cloud SQL, GKE, Memorystore and Filestore are billed from the first minute |
| Any other type must be on the allowlist | Adding a service is a deliberate edit, not an accident |
| Cloud Run v2: `min_instance_count` 0, `max_instance_count` set and ≤ 2 | An always-on instance bills every second; the default max is 100 |
| Buckets and BigQuery datasets in `us-central1` only, and the location must be known at plan time | Always Free is regional (ADR 0001); the multi-region `US` is billed. A computed location is not in the plan and would pass unchecked |
| Artifact Registry repos need a `cleanup_policies` block with at least one `DELETE` rule and `cleanup_policy_dry_run` off | Storage past 0.5 GB is billed and images pile up; `KEEP` rules only protect, and dry run only logs |

Every message names the resource address and what to do instead. The unit
tests in `policy/free_tier_test.rego` run in `ci.yml` with no GCP access.
Only `envs/prod` is planned in CI, so `iam/`, `billing/` and `sandbox/` are
not gated; run the policy by hand on those when they change.

### Second opinion: Infracost (OSRS-20)

After the policy, `plan.yml` runs [Infracost](https://www.infracost.io/) on
the same `plan.json` and fails if the estimated monthly cost is above $0.
It prices in USD and does not model every free allowance; usage-based
services (Cloud Run, BigQuery, Pub/Sub) show $0 without a usage file. So it
catches fixed-price resources the policy might miss, not the other way
round. The policy is the gate, Infracost is the check on it.

Only resource types and SKUs are sent to Infracost's pricing API, never plan
values, and `INFRACOST_ENABLE_CLOUD=false` stops estimates being uploaded.
The CLI and pricing API are free. The key is the `INFRACOST_API_KEY` repo
secret, made once with `infracost auth login`.

CI pins the classic CLI (v0.10.x, `breakdown`, API key). Homebrew installs
Infracost v2, a different product (`scan`, OAuth token, uploads every run to
Infracost Cloud). For local use get the classic binary from
https://github.com/infracost/infracost/releases (`infracost-darwin-arm64`);
its `auth login` writes the API key to `~/.config/infracost/credentials.yml`.

## Local checks (same as CI)

```bash
cd infra
terraform fmt -check -recursive
for root in envs/prod iam billing; do (cd $root && terraform init && terraform validate); done
tflint --init --config "$(pwd)/.tflint.hcl"
tflint --recursive --config "$(pwd)/.tflint.hcl"
conftest fmt --check policy && conftest verify -p policy     # brew install conftest
```

To run the policy on a real plan (the plan file holds every value in clear
text, so keep it out of the repo; both names are gitignored):

```bash
cd infra/envs/prod
terraform plan -lock=false -out=tfplan && terraform show -json tfplan > plan.json
conftest test plan.json -p ../../policy --namespace free_tier
```

Provider versions are fixed by each root's `.terraform.lock.hcl` (darwin_arm64 and
linux_amd64 hashes). After changing a provider version run
`terraform providers lock -platform=darwin_arm64 -platform=linux_amd64`.
