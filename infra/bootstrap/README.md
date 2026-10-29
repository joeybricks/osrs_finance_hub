# bootstrap

Creates `gs://osrs-infra-tfstate`, the bucket that holds Terraform state.

Terraform cannot create the bucket it stores its own state in, so this one
bucket is made by script, once. OSRS-10 imports it into Terraform; after that
the script is only a record of how it was made and a settings check.

## Run

```bash
gcloud auth login                                   # your user account, no key files
infra/bootstrap/create_state_bucket.sh --dry-run    # print the commands only
infra/bootstrap/create_state_bucket.sh              # create, then verify
```

Re-running is safe: if the bucket exists the script only compares its settings
with the spec below and exits non-zero on drift.

## Settings

| Setting | Value | Why |
| --- | --- | --- |
| Project | `osrs-infra` | The project with billing |
| Location | `us-central1` | Inside the GCS Always Free tier (5 GB Standard) |
| Storage class | `STANDARD` | The only class the free tier covers |
| Versioning | on | Roll back a bad or corrupt state write |
| Lifecycle | delete noncurrent versions after 30 days (`lifecycle.json`) | Keeps a month of history without growing for ever |
| Uniform bucket-level access | on | IAM only, no per-object ACLs |
| Public access prevention | enforced | State can contain secrets; the bucket can never be made public |
| Soft delete | off (0) | Versioning already keeps old state; soft-deleted bytes are billed |

State for `infra/envs/prod` lives under the `prod/` prefix (see `../envs/prod/backend.tf`).
