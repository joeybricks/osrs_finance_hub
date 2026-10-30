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

echo "== No service account keys (OSRS-12)"
for proj in "$PROJECT" osrs-llm; do
  enforced=$(gcloud org-policies describe iam.disableServiceAccountKeyCreation \
    --project="$proj" --effective --format='value(spec.rules[0].enforce)' 2>&1)
  mark=PASS; [[ "$enforced" == True ]] || mark=FAIL
  report "$mark" org-policy "disableServiceAccountKeyCreation enforced" "$proj" True "$enforced"
done
for sa in "$PLAN" "$DEPLOYER" "$KILLSWITCH"; do
  keys=$(gcloud iam service-accounts keys list --iam-account="$sa" \
    --managed-by=user --format='value(name)' 2>&1 | grep -c . || true)
  mark=PASS; [[ "$keys" == 0 ]] || mark=FAIL
  report "$mark" "${sa%%@*}" "user-managed keys" "$PROJECT" 0 "$keys"
done

echo "== Workload Identity Federation: who may act as each account (OSRS-12)"
POOL=$(gcloud iam workload-identity-pools describe github --project="$PROJECT" \
  --location=global --format='value(name)' 2>&1)
SET="principalSet://iam.googleapis.com/${POOL}"
condition=$(gcloud iam workload-identity-pools providers describe github-actions \
  --project="$PROJECT" --location=global --workload-identity-pool=github \
  --format='value(attributeCondition)' 2>&1)
want_condition="assertion.repository_id == '1438251307' && assertion.repository_owner_id == '84076451'"
mark=PASS; [[ "$condition" == "$want_condition" ]] || mark=FAIL
report "$mark" github-actions "provider accepts only this repo + owner (by ID)" github yes "$([[ $mark == PASS ]] && echo yes || echo "$condition")"

# wif_check <sa> <expected single member of roles/iam.workloadIdentityUser>
wif_check() {
  local sa="$1" want="$2" got
  got=$(gcloud iam service-accounts get-iam-policy "$sa" --format=json 2>&1 | python3 -c '
import json, sys
try:
    bindings = json.load(sys.stdin).get("bindings", [])
except ValueError:
    sys.exit("unreadable policy")
members = [m for b in bindings if b["role"] == "roles/iam.workloadIdentityUser" for m in b["members"]]
print(" ".join(members) or "none")
')
  local mark=PASS
  [[ "$got" == "$want" ]] || mark=FAIL
  report "$mark" "${sa%%@*}" "workloadIdentityUser = ${want##*/attribute.}" "$PROJECT" yes "$([[ $mark == PASS ]] && echo yes || echo "$got")"
}
wif_check "$PLAN" "${SET}/attribute.event_name/pull_request"
wif_check "$DEPLOYER" "${SET}/attribute.env_on_master/production"
wif_check "$KILLSWITCH" "${SET}/attribute.env_on_master/killswitch"

echo
echo "Note: ALLOW verdicts cover allow policies; deny policies were not readable (none exist)."
if (( failures > 0 )); then
  echo "FAILED: ${failures} check(s) did not match."
  exit 1
fi
echo "All checks passed: each account can do its own job and none of the others'."
