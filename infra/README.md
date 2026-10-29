# infra

Terraform for the cloud resources. Every resource is created through Terraform
and CI, never by hand, and must stay inside free tiers.

The one exception is the Terraform state bucket, which has to exist before
Terraform can run. It is made by `bootstrap/` and then imported (OSRS-10).

| Folder | Contents |
| --- | --- |
| `bootstrap/` | One-off script that creates the state bucket `gs://osrs-infra-tfstate` |
| `envs/prod/` | Root module for the prod environment; state under `prod/` in the bucket |
