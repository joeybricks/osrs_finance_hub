#!/usr/bin/env bash
# Billing killswitch (OSRS-16): unlink billing from one project.
#
#   ./ops/scripts/killswitch.sh --project osrs-infra --dry-run   # say what would happen
#   ./ops/scripts/killswitch.sh --project osrs-infra             # unlink billing
#
# Unlinking billing stops every paid service in the project at once. Free
# resources are kept, but billing-only services (BigQuery storage, Pub/Sub,
# ...) stop working until billing is linked again by hand (runbook:
# ops/runbooks/killswitch.md). Nothing is deleted.
#
# Safe to run twice: it reads the billing link first and does nothing if
# billing is already off. Refuses any project not in ALLOWED_PROJECTS.
#
# Runs as killswitch-sa from .github/workflows/killswitch.yml (OSRS-17), or
# locally as an Owner. killswitch-sa can only read and remove the billing
# link (infra/iam/killswitch_sa.tf), so even a bug here cannot do more.
#
# Exit codes: 0 done or nothing to do, 1 failed, 2 refused or bad arguments.
#
# The repo is public and so are its Actions logs: never print the billing
# account name. Only billingEnabled (true/false) is shown.
set -euo pipefail

# Projects this script may touch. A second lock on top of IAM. The throwaway
# test project is added here in OSRS-18.
ALLOWED_PROJECTS=(osrs-infra)

usage() {
  echo "usage: $0 --project PROJECT_ID [--dry-run]" >&2
  exit 2
}

project=""
dry_run=false
while [[ $# -gt 0 ]]; do
  case "$1" in
    --project) [[ $# -ge 2 ]] || usage; project="$2"; shift 2 ;;
    --dry-run) dry_run=true; shift ;;
    -h|--help) usage ;;
    *) echo "unknown argument: $1" >&2; usage ;;
  esac
done
[[ -n "$project" ]] || usage

mode="unlink"; $dry_run && mode="dry-run"
actor="${GITHUB_ACTOR:-${USER:-unknown}}"
run_url=""
if [[ -n "${GITHUB_RUN_ID:-}" ]]; then
  run_url="${GITHUB_SERVER_URL}/${GITHUB_REPOSITORY}/actions/runs/${GITHUB_RUN_ID}"
fi

# One line per run to stdout, plus the job summary when in GitHub Actions.
# GitHub's run notification (email / mobile app) links to that summary.
log_result() {  # log_result <result> <before> <after> <message>
  local result="$1" before="$2" after="$3" message="$4"
  local ts; ts=$(date -u +%Y-%m-%dT%H:%M:%SZ)
  echo "killswitch time=${ts} actor=${actor} project=${project} mode=${mode}" \
    "billing_before=${before} billing_after=${after} result=${result}"
  echo "$message"
  if [[ -n "${GITHUB_STEP_SUMMARY:-}" ]]; then
    {
      echo "## Killswitch: ${result}"
      echo
      echo "$message"
      echo
      echo "| Field | Value |"
      echo "| --- | --- |"
      echo "| Project | \`${project}\` |"
      echo "| Mode | ${mode} |"
      echo "| Billing before | ${before} |"
      echo "| Billing after | ${after} |"
      echo "| Triggered by | ${actor} |"
      echo "| Time (UTC) | ${ts} |"
      [[ -z "$run_url" ]] || echo "| Run | ${run_url} |"
    } >> "$GITHUB_STEP_SUMMARY"
  fi
}

allowed=false
for p in "${ALLOWED_PROJECTS[@]}"; do
  [[ "$project" == "$p" ]] && allowed=true
done
if ! $allowed; then
  log_result REFUSED - - "Project '${project}' is not in the allow list (${ALLOWED_PROJECTS[*]}). Nothing done."
  exit 2
fi

# billingEnabled: True or False. Needs resourcemanager.projects.get.
billing_enabled() {
  gcloud billing projects describe "$project" --format='value(billingEnabled)'
}

if ! before=$(billing_enabled); then
  log_result FAILED unknown unknown "Could not read the billing link for '${project}'. Nothing done."
  exit 1
fi

if [[ "$before" != True ]]; then
  log_result NO-OP "$before" "$before" "Billing is already unlinked from '${project}'. Nothing to do."
  exit 0
fi

if $dry_run; then
  log_result DRY-RUN "$before" "$before" "Billing is linked to '${project}'. A real run would unlink it."
  exit 0
fi

# Output (which names the billing account) is thrown away; errors still
# go to stderr.
if ! gcloud billing projects unlink "$project" >/dev/null; then
  log_result FAILED "$before" unknown "Unlink call failed for '${project}'. Check the job log."
  exit 1
fi

after=$(billing_enabled || echo unknown)
if [[ "$after" == False ]]; then
  log_result UNLINKED "$before" "$after" "Billing unlinked from '${project}'. Paid services are stopped. To restore, see ops/runbooks/killswitch.md."
  exit 0
fi
log_result FAILED "$before" "$after" "Unlink call returned, but billing still reads as '${after}' for '${project}'. Check by hand."
exit 1
