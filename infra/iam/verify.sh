#!/usr/bin/env bash
# Prove each service account can do its own job and none of the others'
# (OSRS-11 done criterion). Read-only: asks Policy Troubleshooter for GCP's
# verdict; nothing is written, unlinked or enabled.
#
# Run after `terraform apply` in infra/iam, as a project Owner:
#   ./infra/iam/verify.sh
# Exits 1 if any check does not match the expected verdict.
set -euo pipefail

PROJECT="osrs-infra"
BUCKET="osrs-infra-tfstate"
SA_DOMAIN="${PROJECT}.iam.gserviceaccount.com"
PLAN="plan-sa@${SA_DOMAIN}"
DEPLOYER="deployer-sa@${SA_DOMAIN}"
KILLSWITCH="killswitch-sa@${SA_DOMAIN}"

PROJECT_RES="//cloudresourcemanager.googleapis.com/projects/${PROJECT}"
BUCKET_RES="//storage.googleapis.com/projects/_/buckets/${BUCKET}"

failures=0

# check <ALLOW|DENY> <principal> <permission> <resource> [object path]
# The object path (e.g. prod/default.tfstate) lets Troubleshooter evaluate
# IAM conditions on resource.name.
check() {
  local expect="$1" who="$2" perm="$3" res="$4" object="${5:-}"
  local extra=()
  local target="$res"
  if [[ -n "$object" ]]; then
    target="${BUCKET_RES}/objects/${object}"
    extra=(--resource-name="$target"
           --resource-service=storage.googleapis.com
           --resource-type=storage.googleapis.com/Object)
  fi

  local state
  state=$(gcloud policy-intelligence troubleshoot-policy iam "$target" \
    --principal-email="$who" --permission="$perm" ${extra[@]+"${extra[@]}"} \
    --billing-project="$PROJECT" --format='value(overallAccessState)')

  local got
  case "$state" in
    CAN_ACCESS) got=ALLOW ;;
    CANNOT_ACCESS) got=DENY ;;
    *) got="UNKNOWN($state)" ;;
  esac

  local mark=PASS
  if [[ "$got" != "$expect" ]]; then
    mark=FAIL
    failures=$((failures + 1))
  fi
  printf '%-4s  %-14s %-50s %-28s want %-5s got %s\n' \
    "$mark" "${who%%@*}" "$perm" "${object:-${target##*/}}" "$expect" "$got"
}

echo "== plan-sa: read only"
check ALLOW "$PLAN" resourcemanager.projects.get "$PROJECT_RES"
check ALLOW "$PLAN" storage.objects.get "$BUCKET_RES" prod/default.tfstate
check DENY  "$PLAN" storage.objects.create "$BUCKET_RES" prod/default.tfstate
check DENY  "$PLAN" storage.buckets.update "$BUCKET_RES"
check DENY  "$PLAN" resourcemanager.projects.setIamPolicy "$PROJECT_RES"
check DENY  "$PLAN" resourcemanager.projects.deleteBillingAssignment "$PROJECT_RES"

echo "== deployer-sa: prod state and bucket settings only"
check ALLOW "$DEPLOYER" storage.objects.create "$BUCKET_RES" prod/default.tfstate
check ALLOW "$DEPLOYER" storage.objects.delete "$BUCKET_RES" prod/default.tflock
check ALLOW "$DEPLOYER" storage.buckets.update "$BUCKET_RES"
check DENY  "$DEPLOYER" storage.objects.create "$BUCKET_RES" iam/default.tfstate
check DENY  "$DEPLOYER" storage.objects.delete "$BUCKET_RES" iam/default.tfstate
check DENY  "$DEPLOYER" storage.buckets.setIamPolicy "$BUCKET_RES"
check DENY  "$DEPLOYER" storage.buckets.delete "$BUCKET_RES"
check DENY  "$DEPLOYER" resourcemanager.projects.setIamPolicy "$PROJECT_RES"
check DENY  "$DEPLOYER" serviceusage.services.enable "$PROJECT_RES"
check DENY  "$DEPLOYER" resourcemanager.projects.deleteBillingAssignment "$PROJECT_RES"
check DENY  "$DEPLOYER" resourcemanager.projects.createBillingAssignment "$PROJECT_RES"

echo "== killswitch-sa: unlink billing only"
check ALLOW "$KILLSWITCH" resourcemanager.projects.deleteBillingAssignment "$PROJECT_RES"
check DENY  "$KILLSWITCH" resourcemanager.projects.createBillingAssignment "$PROJECT_RES"
check DENY  "$KILLSWITCH" resourcemanager.projects.setIamPolicy "$PROJECT_RES"
check DENY  "$KILLSWITCH" storage.objects.get "$BUCKET_RES" prod/default.tfstate
check DENY  "$KILLSWITCH" storage.buckets.update "$BUCKET_RES"

echo
if (( failures > 0 )); then
  echo "FAILED: ${failures} check(s) did not match."
  exit 1
fi
echo "All checks passed: each account can do its own job and none of the others'."
