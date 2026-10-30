#!/usr/bin/env bash
# Prove each service account can do its own job and none of the others'
# (OSRS-11 done criterion). Read-only: asks Policy Troubleshooter for GCP's
# verdict and reads the state bucket's IAM policy; nothing is written.
#
# Run after `terraform apply` in infra/iam, as a project Owner:
#   ./infra/iam/verify.sh
# Exits 1 if any check does not match the expected verdict.
#
# Two Troubleshooter limits shape this script:
# - Verdicts come from allow policies only. Reading deny policies needs an
#   org-level deny reviewer role we don't hold, so the overall verdict is
#   UNKNOWN_INFO. We create no deny policies, and they can only remove access,
#   so "not granted" is proof of DENY.
# - It cannot target a single object, so it evaluates the deployer's
#   prod/-only condition against the bucket name and always gets false. That
#   condition is checked by reading the bucket policy instead (last section).
set -uo pipefail

PROJECT="osrs-infra"
BUCKET="osrs-infra-tfstate"
SA_DOMAIN="${PROJECT}.iam.gserviceaccount.com"
PLAN="plan-sa@${SA_DOMAIN}"
DEPLOYER="deployer-sa@${SA_DOMAIN}"
KILLSWITCH="killswitch-sa@${SA_DOMAIN}"

PROJECT_RES="//cloudresourcemanager.googleapis.com/projects/${PROJECT}"
BUCKET_RES="//storage.googleapis.com/projects/_/buckets/${BUCKET}"

failures=0

report() {  # report <PASS|FAIL> <who> <what> <where> <want> <got>
  [[ "$1" == FAIL ]] && failures=$((failures + 1))
  printf '%-4s  %-14s %-50s %-20s want %-5s got %s\n' "$@"
}

# check <ALLOW|DENY> <principal> <permission> <resource>
check() {
  local expect="$1" who="$2" perm="$3" res="$4" state got
  if ! state=$(gcloud policy-intelligence troubleshoot-policy iam "$res" \
      --principal-email="$who" --permission="$perm" \
      --billing-project="$PROJECT" \
      --format='value(allowPolicyExplanation.allowAccessState)' 2>&1); then
    state="ERROR: ${state##*ERROR: }"
  fi
  case "$state" in
    ALLOW_ACCESS_STATE_GRANTED) got=ALLOW ;;
    ALLOW_ACCESS_STATE_NOT_GRANTED) got=DENY ;;
    *) got="$state" ;;
  esac
  local mark=PASS
  [[ "$got" == "$expect" ]] || mark=FAIL
  report "$mark" "${who%%@*}" "$perm" "${res##*/}" "$expect" "$got"
}

echo "== plan-sa: read only"
check ALLOW "$PLAN" resourcemanager.projects.get "$PROJECT_RES"
check ALLOW "$PLAN" storage.objects.get "$BUCKET_RES"
check ALLOW "$PLAN" storage.buckets.get "$BUCKET_RES"
check DENY  "$PLAN" storage.objects.create "$BUCKET_RES"
check DENY  "$PLAN" storage.objects.delete "$BUCKET_RES"
check DENY  "$PLAN" storage.buckets.update "$BUCKET_RES"
check DENY  "$PLAN" resourcemanager.projects.setIamPolicy "$PROJECT_RES"
check DENY  "$PLAN" resourcemanager.projects.deleteBillingAssignment "$PROJECT_RES"

echo "== deployer-sa: prod state and bucket settings only"
check ALLOW "$DEPLOYER" resourcemanager.projects.get "$PROJECT_RES"
check ALLOW "$DEPLOYER" storage.objects.get "$BUCKET_RES"
check ALLOW "$DEPLOYER" storage.buckets.update "$BUCKET_RES"
check DENY  "$DEPLOYER" storage.buckets.setIamPolicy "$BUCKET_RES"
check DENY  "$DEPLOYER" storage.buckets.delete "$BUCKET_RES"
check DENY  "$DEPLOYER" resourcemanager.projects.setIamPolicy "$PROJECT_RES"
check DENY  "$DEPLOYER" serviceusage.services.enable "$PROJECT_RES"
check DENY  "$DEPLOYER" resourcemanager.projects.deleteBillingAssignment "$PROJECT_RES"
check DENY  "$DEPLOYER" resourcemanager.projects.createBillingAssignment "$PROJECT_RES"

echo "== killswitch-sa: unlink billing only"
check ALLOW "$KILLSWITCH" resourcemanager.projects.deleteBillingAssignment "$PROJECT_RES"
check DENY  "$KILLSWITCH" resourcemanager.projects.createBillingAssignment "$PROJECT_RES"
check DENY  "$KILLSWITCH" resourcemanager.projects.get "$PROJECT_RES"
check DENY  "$KILLSWITCH" resourcemanager.projects.setIamPolicy "$PROJECT_RES"
check DENY  "$KILLSWITCH" storage.objects.get "$BUCKET_RES"
check DENY  "$KILLSWITCH" storage.buckets.update "$BUCKET_RES"

echo "== deployer-sa: state writes limited to prod/ (bucket policy)"
want_cond="resource.name.startsWith(\"projects/_/buckets/${BUCKET}/objects/prod/\")"
if policy=$(gcloud storage buckets get-iam-policy "gs://${BUCKET}" --format=json 2>&1); then
  # One line per deployer write binding: "<role>|<condition or none>".
  writes=$(python3 -c '
import json, sys
member = "serviceAccount:" + sys.argv[1]
write_roles = {"roles/storage.objectUser", "roles/storage.objectCreator",
               "roles/storage.objectAdmin", "roles/storage.admin",
               "roles/storage.legacyBucketWriter", "roles/storage.legacyBucketOwner"}
for b in json.loads(sys.stdin.read()).get("bindings", []):
    if member in b["members"] and b["role"] in write_roles:
        print(b["role"] + "|" + b.get("condition", {}).get("expression", "none"))
' "$DEPLOYER" <<<"$policy")
  if [[ "$writes" == "roles/storage.objectUser|${want_cond}" ]]; then
    report PASS deployer-sa "only write role is objectUser + prod/ condition" "$BUCKET" yes yes
  else
    report FAIL deployer-sa "only write role is objectUser + prod/ condition" "$BUCKET" yes "${writes:-no write binding}"
  fi
else
  report FAIL deployer-sa "read bucket IAM policy" "$BUCKET" ok "${policy##*ERROR: }"
fi

echo
echo "Note: ALLOW verdicts cover allow policies; deny policies were not readable (none exist)."
if (( failures > 0 )); then
  echo "FAILED: ${failures} check(s) did not match."
  exit 1
fi
echo "All checks passed: each account can do its own job and none of the others'."
