#!/usr/bin/env bash
# ^ "Shebang": run this file with bash, found via PATH (env), so it works on
#   macOS and the GitHub runner without hard-coding bash's location.

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
#
# Flow:
#   parse args -> allow list? -> read billing -> already off? -> dry run? -> unlink -> re-read -> report

# Bash "strict mode":
#   -e           stop at the first command that fails (non-zero exit)
#   -u           treat an unset variable as an error (catches typos)
#   -o pipefail  a pipeline fails if any part fails, not just the last
set -euo pipefail

# --- Configuration ---------------------------------------------------------

# Projects this script may touch. A second lock on top of IAM. The throwaway
# test project is added here in OSRS-18. ( ... ) makes a bash array.
ALLOWED_PROJECTS=(osrs-infra)

# --- Arguments -------------------------------------------------------------

# Print how to call the script to stderr (>&2), then exit 2 ("bad arguments").
# $0 is the path the script was called by.
usage() {
  echo "usage: $0 --project PROJECT_ID [--dry-run]" >&2
  exit 2
}

project=""      # the project ID to act on; must be given
dry_run=false   # true/false are bash commands, so "$dry_run" can be run as a test below

# Walk through the arguments one by one. $# is how many are left; $1 is the
# current one; `shift N` drops N from the front.
while [[ $# -gt 0 ]]; do
  case "$1" in
    # --project needs a value after it: if there isn't one, show usage.
    # Otherwise keep the value ($2) and drop both words.
    --project) [[ $# -ge 2 ]] || usage; project="$2"; shift 2 ;;
    # A flag with no value: switch dry run on and drop one word.
    --dry-run) dry_run=true; shift ;;
    -h|--help) usage ;;
    # Anything else is a mistake: say which word, then show usage.
    *) echo "unknown argument: $1" >&2; usage ;;
  esac
done

# -n means "not empty". No --project given -> usage and exit 2.
[[ -n "$project" ]] || usage

# --- Context for the log -------------------------------------------------

# Run the dry_run "command" (true/false); if true, the mode becomes dry-run.
mode="unlink"; $dry_run && mode="dry-run"

# Who ran it. ${A:-B} means "A if set and not empty, else B". In Actions
# GITHUB_ACTOR is the GitHub user who pressed the button; locally it's $USER.
actor="${GITHUB_ACTOR:-${USER:-unknown}}"

# A link back to the Actions run, only when running in GitHub Actions
# (GITHUB_RUN_ID is set there and nowhere else). The :- default stops -u
# from failing when it's missing locally.
run_url=""
if [[ -n "${GITHUB_RUN_ID:-}" ]]; then
  run_url="${GITHUB_SERVER_URL}/${GITHUB_REPOSITORY}/actions/runs/${GITHUB_RUN_ID}"
fi

# --- Reporting -------------------------------------------------------------

# One line per run to stdout, plus the job summary when in GitHub Actions.
# GitHub's run notification (email / mobile app) links to that summary.
log_result() {  # log_result <result> <before> <after> <message>
  # `local` keeps these variables inside the function.
  local result="$1" before="$2" after="$3" message="$4"
  # UTC timestamp in ISO 8601, e.g. 2026-10-30T15:39:12Z. Declared and set on
  # separate lines so a failing `date` isn't hidden by `local`'s own exit code.
  local ts; ts=$(date -u +%Y-%m-%dT%H:%M:%SZ)
  # key=value pairs on one line: easy to grep and to read in the job log.
  # The trailing \ continues the command on the next line.
  echo "killswitch time=${ts} actor=${actor} project=${project} mode=${mode}" \
    "billing_before=${before} billing_after=${after} result=${result}"
  # The human-readable sentence.
  echo "$message"
  # GITHUB_STEP_SUMMARY is a file path Actions provides; markdown appended to
  # it shows on the run's summary page. Skipped when running locally.
  if [[ -n "${GITHUB_STEP_SUMMARY:-}" ]]; then
    # { ... } groups the echoes so one >> appends them all to the file.
    {
      echo "## Killswitch: ${result}"
      echo
      echo "$message"
      echo
      echo "| Field | Value |"
      echo "| --- | --- |"
      echo "| Project | \`${project}\` |"   # \` is a literal backtick (code style)
      echo "| Mode | ${mode} |"
      echo "| Billing before | ${before} |"
      echo "| Billing after | ${after} |"
      echo "| Triggered by | ${actor} |"
      echo "| Time (UTC) | ${ts} |"
      # -z means "empty". Only add the Run row if there is a link.
      [[ -z "$run_url" ]] || echo "| Run | ${run_url} |"
    } >> "$GITHUB_STEP_SUMMARY"
  fi
}

# --- Lock 1: the allow list ------------------------------------------------

# Check the requested project against every entry in the allow list.
# "${ALLOWED_PROJECTS[@]}" expands to each element, safely quoted.
allowed=false
for p in "${ALLOWED_PROJECTS[@]}"; do
  [[ "$project" == "$p" ]] && allowed=true
done
# Not on the list: log REFUSED and exit 2 before any call to Google.
# - and - fill the before/after columns, since nothing was read.
# ${ALLOWED_PROJECTS[*]} joins the list with spaces for the message.
if ! $allowed; then
  log_result REFUSED - - "Project '${project}' is not in the allow list (${ALLOWED_PROJECTS[*]}). Nothing done."
  exit 2
fi

# --- Read the current state ------------------------------------------------

# Ask the Cloud Billing API (getBillingInfo) whether billing is on.
# --format='value(billingEnabled)' prints just True or False, and nothing
# else, so the billing account name never reaches the log.
# Needs resourcemanager.projects.get (granted in infra/iam/killswitch_sa.tf).
billing_enabled() {
  gcloud billing projects describe "$project" --format='value(billingEnabled)'
}

# $( ... ) captures the output into `before`. Wrapping it in `if !` handles a
# failure here ourselves (log + exit 1) instead of -e stopping silently.
if ! before=$(billing_enabled); then
  log_result FAILED unknown unknown "Could not read the billing link for '${project}'. Nothing done."
  exit 1
fi

# --- Idempotency: already off? ---------------------------------------------

# Anything other than True means billing is already unlinked: nothing to do.
# This is what makes a second run (or a double tap) harmless.
if [[ "$before" != True ]]; then
  log_result NO-OP "$before" "$before" "Billing is already unlinked from '${project}'. Nothing to do."
  exit 0
fi

# --- Dry run: report and stop ----------------------------------------------

# Billing is on. In dry-run mode, say what would happen and stop here.
if $dry_run; then
  log_result DRY-RUN "$before" "$before" "Billing is linked to '${project}'. A real run would unlink it."
  exit 0
fi

# --- The real action -------------------------------------------------------

# Remove the project's billing link (updateBillingInfo with no account).
# gcloud's output names the billing account, so stdout goes to /dev/null
# (thrown away). Errors still go to stderr, so they show in the job log.
if ! gcloud billing projects unlink "$project" >/dev/null; then
  log_result FAILED "$before" unknown "Unlink call failed for '${project}'. Check the job log."
  exit 1
fi

# --- Check it worked -------------------------------------------------------

# Don't trust the call alone: read the state again. If the read itself fails,
# use "unknown" (|| echo unknown) rather than letting -e stop the script.
after=$(billing_enabled || echo unknown)

# False means billing really is off: success.
if [[ "$after" == False ]]; then
  log_result UNLINKED "$before" "$after" "Billing unlinked from '${project}'. Paid services are stopped. To restore, see ops/runbooks/killswitch.md."
  exit 0
fi

# The call said OK but billing still reads as on (or unreadable): flag it so
# a person checks. Exit 1 turns the GitHub run red, which notifies you.
log_result FAILED "$before" "$after" "Unlink call returned, but billing still reads as '${after}' for '${project}'. Check by hand."
exit 1
