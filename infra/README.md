# infra

Terraform for the cloud resources. Every resource is created through Terraform
and CI, never by hand, and must stay inside free tiers.

The one exception is the Terraform state bucket, which has to exist before
Terraform can run. It is made by `bootstrap/` and then imported (OSRS-10).

| Path | Contents |
| --- | --- |
| `bootstrap/` | One-off script that creates the state bucket `gs://osrs-infra-tfstate` |
| `envs/prod/` | Root module for the prod environment; state under `prod/` in the bucket |
| `modules/` | Reusable modules (none yet) |
| `.tflint.hcl` | TFLint rules: Terraform recommended preset + Google ruleset |

## Local checks (same as CI)

```bash
cd infra
terraform fmt -check -recursive
(cd envs/prod && terraform init && terraform validate)
tflint --init --config "$(pwd)/.tflint.hcl"
tflint --recursive --config "$(pwd)/.tflint.hcl"
```

Provider versions are fixed by `envs/prod/.terraform.lock.hcl` (darwin_arm64 and
linux_amd64 hashes). After changing a provider version run
`terraform providers lock -platform=darwin_arm64 -platform=linux_amd64`.
