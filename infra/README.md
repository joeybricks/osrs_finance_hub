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
| `modules/` | Reusable modules (none yet) |
| `.tflint.hcl` | TFLint rules: Terraform recommended preset + Google ruleset |

## Service accounts (`iam/`)

| Account | Job | Can | Cannot |
| --- | --- | --- | --- |
| `plan-sa` | `terraform plan` on PRs | read both projects and the state bucket | write anything |
| `deployer-sa` | `terraform apply` of `envs/prod` after merge | write `prod/` state (IAM condition), update state bucket settings | IAM, enable APIs, billing, change projects, touch `iam/` state |
| `killswitch-sa` | unlink billing when over budget (OSRS-16) | `deleteBillingAssignment` on `osrs-infra` | re-link billing, anything else |

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

## Local checks (same as CI)

```bash
cd infra
terraform fmt -check -recursive
for root in envs/prod iam; do (cd $root && terraform init && terraform validate); done
tflint --init --config "$(pwd)/.tflint.hcl"
tflint --recursive --config "$(pwd)/.tflint.hcl"
```

Provider versions are fixed by each root's `.terraform.lock.hcl` (darwin_arm64 and
linux_amd64 hashes). After changing a provider version run
`terraform providers lock -platform=darwin_arm64 -platform=linux_amd64`.
