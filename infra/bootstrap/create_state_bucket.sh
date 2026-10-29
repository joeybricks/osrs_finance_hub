#!/usr/bin/env bash
# Create the GCS bucket that holds Terraform state. Run once, by hand.
#
# Terraform cannot create the bucket it stores its own state in, so this one
# bucket is made by script. OSRS-10 imports it so Terraform owns it afterwards.
# Safe to re-run: if the bucket exists it only checks the settings.
#
# Usage: infra/bootstrap/create_state_bucket.sh [--dry-run]
#        [--project ID] [--bucket NAME] [--region REGION]

set -euo pipefail

PROJECT="osrs-infra"
BUCKET="osrs-infra-tfstate"
# us-central1 is inside the GCS Always Free tier (5 GB Standard storage).
REGION="us-central1"
DRY_RUN=false
HERE="$(cd "$(dirname "$0")" && pwd)"
LIFECYCLE_FILE="$HERE/lifecycle.json"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --dry-run) DRY_RUN=true; shift ;;
    --project) PROJECT="$2"; shift 2 ;;
    --bucket)  BUCKET="$2"; shift 2 ;;
    --region)  REGION="$2"; shift 2 ;;
    -h|--help) sed -n '2,10p' "$0"; exit 0 ;;
    *) echo "Unknown argument: $1" >&2; exit 2 ;;
  esac
done

# Print every command; only execute it when not a dry run.
run() {
  echo "+ $*"
  if [[ "$DRY_RUN" == false ]]; then "$@"; fi
}

# Compare the live bucket with what this script would create.
verify() {
  local actual expected
  actual="$(gcloud storage buckets describe "gs://$BUCKET" --project "$PROJECT" --raw \
    --format='csv[no-heading](location,storageClass,versioning.enabled,iamConfiguration.uniformBucketLevelAccess.enabled,iamConfiguration.publicAccessPrevention,softDeletePolicy.retentionDurationSeconds,lifecycle.rule[0].condition.daysSinceNoncurrentTime)')"
  expected="$(echo "$REGION" | tr '[:lower:]' '[:upper:]'),STANDARD,True,True,enforced,0,30"
  echo "fields:   location,class,versioning,ubla,pap,soft_delete_s,noncurrent_days"
  echo "expected: $expected"
  echo "actual:   $actual"
  if [[ "$actual" != "$expected" ]]; then
    echo "Bucket settings drifted from the bootstrap spec." >&2
    exit 1
  fi
  echo "OK: gs://$BUCKET matches the bootstrap spec."
}

echo "Project: $PROJECT  Bucket: gs://$BUCKET  Region: $REGION  Dry run: $DRY_RUN"

if gcloud storage buckets describe "gs://$BUCKET" --project "$PROJECT" >/dev/null 2>&1; then
  echo "Bucket already exists; checking settings only."
  verify
  exit 0
fi

# Soft delete is off: versioning already keeps old state, and soft-deleted
# bytes are billed storage.
run gcloud storage buckets create "gs://$BUCKET" \
  --project "$PROJECT" \
  --location "$REGION" \
  --default-storage-class STANDARD \
  --uniform-bucket-level-access \
  --public-access-prevention \
  --soft-delete-duration 0 \
  --lifecycle-file "$LIFECYCLE_FILE"

# `create` has no versioning flag, so it is turned on with `update`.
run gcloud storage buckets update "gs://$BUCKET" --project "$PROJECT" --versioning

if [[ "$DRY_RUN" == false ]]; then verify; fi
