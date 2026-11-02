#!/usr/bin/env bash
# Synthetic budget message through the budget-alerts topic (OSRS-15 test).
#
# Publishes a fake budget notification to the topic, then reads it back from
# the budget-alerts-audit subscription. Proves the topic path that the
# killswitch (OSRS-16) will use. It does NOT send an email: budget emails come
# only from the budget service, when real spend crosses a threshold.
#
# Run as a project Owner after envs/prod is applied:
#   ./ops/scripts/budget-alert-test.sh
#
# The fake message is marked so no reader acts on it: attribute synthetic=true,
# billingAccountId=synthetic, and cost below 100% of the budget.
# Other messages pulled while searching are not acked, so they stay in the
# subscription (they reappear after the 60s ack deadline).
set -euo pipefail

PROJECT="osrs-infra"
TOPIC="budget-alerts"
SUB="budget-alerts-audit"
marker="synthetic-$(date -u +%Y%m%dT%H%M%SZ)-$$"

# Same shape as a real message (schema 1.0); field list from Google's docs.
payload=$(cat <<JSON
{"budgetDisplayName": "osrs-infra monthly (SYNTHETIC TEST ${marker})",
 "alertThresholdExceeded": 0.9,
 "costAmount": 1.85,
 "costIntervalStart": "$(date -u +%Y-%m-01T07:00:00Z)",
 "budgetAmount": 2.0,
 "budgetAmountType": "SPECIFIED_AMOUNT",
 "currencyCode": "GBP"}
JSON
)

echo "== publish ${marker}"
gcloud pubsub topics publish "$TOPIC" --project="$PROJECT" \
  --message="$payload" \
  --attribute="billingAccountId=synthetic,budgetId=${marker},schemaVersion=1.0,synthetic=true"

echo "== pull from ${SUB}"
for attempt in 1 2 3 4 5 6; do
  found=$(gcloud pubsub subscriptions pull "$SUB" --project="$PROJECT" \
    --limit=20 --format=json | python3 -c '
import base64, json, sys
marker = sys.argv[1]
for m in json.load(sys.stdin):
    msg = m["message"]
    if msg.get("attributes", {}).get("budgetId") == marker:
        print(m["ackId"])
        print(json.dumps(json.loads(base64.b64decode(msg["data"])), indent=2))
        break
' "$marker")
  if [[ -n "$found" ]]; then
    ack_id=$(head -n1 <<<"$found")
    tail -n +2 <<<"$found"
    gcloud pubsub subscriptions ack "$SUB" --project="$PROJECT" --ack-ids="$ack_id" >/dev/null
    echo "PASS  synthetic message published, received and acked (attempt ${attempt})"
    exit 0
  fi
  sleep 5
done
echo "FAIL  synthetic message not received from ${SUB} after 6 attempts"
exit 1
