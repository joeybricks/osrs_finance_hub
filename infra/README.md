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
| `modules/` | Reusable modules (none yet) |
| `.tflint.hcl` | TFLint rules: Terraform recommended preset + Google ruleset |

## Service accounts (`iam/`)

| Account | Job | Can | Cannot |
| --- | --- | --- | --- |
| `plan-sa` | `terraform plan` on PRs | read both projects, the state bucket, BigQuery metadata, enabled APIs and Pub/Sub topics | write anything, read BigQuery rows, pull Pub/Sub messages, see the billing account or budget |
| `deployer-sa` | `terraform apply` of `envs/prod` after merge | write `prod/` state (IAM condition), update state bucket settings, read BigQuery metadata, enabled APIs and Pub/Sub topics | IAM, enable APIs, billing, change projects or datasets, read BigQuery rows, touch `iam/` state |
| `killswitch-sa` | unlink billing when over budget (OSRS-16, button OSRS-17) | `deleteBillingAssignment` + `projects.get` on `osrs-infra` | re-link billing, anything else |

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

## Local checks (same as CI)

```bash
cd infra
terraform fmt -check -recursive
for root in envs/prod iam billing; do (cd $root && terraform init && terraform validate); done
tflint --init --config "$(pwd)/.tflint.hcl"
tflint --recursive --config "$(pwd)/.tflint.hcl"
```

Provider versions are fixed by each root's `.terraform.lock.hcl` (darwin_arm64 and
linux_amd64 hashes). After changing a provider version run
`terraform providers lock -platform=darwin_arm64 -platform=linux_amd64`.
