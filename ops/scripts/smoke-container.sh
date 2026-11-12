#!/usr/bin/env bash
# Container smoke test (OSRS-42): build the image, run it, check it works.
#
#   OSRS_FINANCE_HUB_CONTACT=you ./ops/scripts/smoke-container.sh          # test, then stop
#   OSRS_FINANCE_HUB_CONTACT=you ./ops/scripts/smoke-container.sh --keep   # leave it running
#
# Run from anywhere; it uses the repo's compose.yaml. Needs Docker running.
# Makes 4 wiki requests (the startup prime); every later call is served from
# the cache, which the script checks.
#
# Checks, in order:
#   1. /health answers 200 (process up, no wiki needed)
#   2. /api/flips has data: thousands of items, have_data true, no errors
#   3. the User-Agent carries the contact
#   4. the API calls above caused no extra wiki requests (lazy refresh)
#   5. the page and its script are served
#   6. Docker reports the container healthy
#
# Exit codes: 0 all checks passed, 1 a check failed, 2 bad arguments or setup.

set -euo pipefail

BASE_URL="http://127.0.0.1:8080"
MIN_ITEMS=1000          # the wiki lists ~4,600; far fewer means a broken prime
WAIT_SECONDS=90         # build is cached after the first run; prime takes ~5s

keep=false
case "${1:-}" in
  "") ;;
  --keep) keep=true ;;
  *) echo "usage: $0 [--keep]" >&2; exit 2 ;;
esac

if [[ -z "${OSRS_FINANCE_HUB_CONTACT:-}" ]]; then
  echo "Set OSRS_FINANCE_HUB_CONTACT first: the wiki asks every client for a contact." >&2
  exit 2
fi
if ! docker info >/dev/null 2>&1; then
  echo "Docker is not running. Start Docker Desktop and try again." >&2
  exit 2
fi

cd "$(dirname "$0")/../.."   # repo root, where compose.yaml lives

failures=0
pass() { echo "  PASS  $1"; }
fail() { echo "  FAIL  $1"; failures=$((failures + 1)); }

# Always stop the container on exit, even after a failure, unless --keep.
cleanup() {
  if [[ "$keep" == true ]]; then
    echo "Left running at $BASE_URL/  (stop with: docker compose down)"
  else
    docker compose down >/dev/null 2>&1 || true
  fi
}
trap cleanup EXIT

echo "Building and starting a fresh container..."
# Start clean so the log only holds this run's wiki requests (check 4).
docker compose down >/dev/null 2>&1 || true
docker compose up --build -d --quiet-pull 2>&1 | grep -iE "error|fail" || true

# 1. /health
echo "Checks:"
up=false
for _ in $(seq 1 "$WAIT_SECONDS"); do
  if curl -sf "$BASE_URL/health" >/dev/null; then up=true; break; fi
  sleep 1
done
if [[ "$up" == true ]]; then
  pass "/health answers 200"
else
  fail "/health did not answer within ${WAIT_SECONDS}s"
  docker compose logs app | tail -20
  exit 1
fi

# 2. /api/flips
summary=$(curl -sf --compressed "$BASE_URL/api/flips" | python3 -c '
import json, sys
d = json.load(sys.stdin)
s = d["status"]
print(len(d["items"]), s["have_data"], ",".join(sorted(s["errors"])) or "none")
') || summary="0 False request-failed"
read -r items have_data errors <<<"$summary"
if (( items >= MIN_ITEMS )) && [[ "$have_data" == True && "$errors" == none ]]; then
  pass "/api/flips: $items items, have_data, no errors"
else
  fail "/api/flips: items=$items have_data=$have_data errors=$errors"
fi

logs=$(docker compose logs app 2>&1)

# 3. User-Agent
if grep -q "User-Agent: .*(contact: $OSRS_FINANCE_HUB_CONTACT)" <<<"$logs"; then
  pass "User-Agent carries the contact"
else
  fail "User-Agent does not show contact '$OSRS_FINANCE_HUB_CONTACT'"
fi

# 4. Only the startup prime reached the wiki
wiki_calls=$(grep -c "prices.runescape.wiki" <<<"$logs" || true)
if (( wiki_calls == 4 )); then
  pass "4 wiki requests (startup prime only; API calls hit the cache)"
else
  fail "expected 4 wiki requests, saw $wiki_calls"
fi

# 5. Page and script
for path in / /static/app.js; do
  code=$(curl -s -o /dev/null -w "%{http_code}" "$BASE_URL$path")
  if [[ "$code" == 200 ]]; then pass "$path served"; else fail "$path returned $code"; fi
done

# 6. Docker's own health check (compose.yaml), may need a moment to report
status=unknown
for _ in $(seq 1 30); do
  status=$(docker inspect -f '{{.State.Health.Status}}' "$(docker compose ps -q app)")
  [[ "$status" == healthy ]] && break
  sleep 1
done
if [[ "$status" == healthy ]]; then pass "Docker reports healthy"; else fail "Docker health: $status"; fi

echo
if (( failures == 0 )); then
  echo "All checks passed."
else
  echo "$failures check(s) failed."
  exit 1
fi
