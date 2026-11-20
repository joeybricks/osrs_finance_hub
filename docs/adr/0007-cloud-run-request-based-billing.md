# 0007. Host the app on Cloud Run with request-based billing

- **Status:** Accepted
- **Date:** 2026-11-20
- **Tickets:** OSRS-63 (story OSRS-31, epic OSRS-23); builds on OSRS-25,
  OSRS-28, OSRS-29, OSRS-65

## Context

Epic OSRS-23 lifts the existing FastAPI app (one container, no database) off
Joe's laptop so it opens on his phone. Constraints:

- **£2 budget, stay in free tiers** (ADR 0005). Idle time must cost nothing.
- **One user**, a few hours a day at most. The open tab polls `/api/flips`
  every 60s.
- **Wiki etiquette:** the app must not fetch more often because it moved.
- **Learning goal:** standard, portable building blocks (a container image,
  Terraform, keyless CI), not a platform-specific app.
- **Private:** Joe only (ADR 0006).

Options considered:

| Option | Idle cost | Fit | Why not |
| --- | --- | --- | --- |
| **Cloud Run service** | £0 (scales to zero) | Runs the existing image as is | — |
| Compute Engine `e2-micro` | £0 in the Always Free tier, if it stays the only VM | A VM we patch, plus HTTPS and sign-in to set up | OS upkeep; IAP for a VM needs a load balancer (monthly charge); the free-tier policy rejects Compute (Infracost priced a test VM at $6.51/month, OSRS-19) |
| App Engine standard | £0 (scales to zero) | Works, also supports IAP | Its own runtime and config; Google's newer path is Cloud Run; less portable |
| GKE Autopilot | Cluster fee | Far more than one container needs | Cost and operational weight |
| Cloud Run functions | £0 | One function per handler | The app is a full web server with a static UI, not separate handlers |

## Decision

**Cloud Run service `osrs-finance-hub`** in `us-central1` (ADR 0001), built
from the OSRS-25 image and defined in `infra/envs/prod/cloud_run.tf`.

- **Scaling:** min 0, max 1. Min 0 means no instance, so no cost, while idle.
  Max 1 because pins and the price cache live in the instance's memory; two
  instances would disagree.
- **Access:** IAP directly on the service, no load balancer. See ADR 0006.
- **Identity:** runs as `runtime-sa`, which has no roles, not the Compute
  default account (project Editor).

### Billing mode: request-based

Cloud Run bills in one of two ways:

```
Request-based (cpu_idle = true)        Instance-based (cpu_idle = false)
CPU billed only while a request        CPU billed for the whole time an
is being handled; throttled to ~0      instance is up, requests or not
between requests
```

**Chosen: request-based.** Joe's use is short requests (~1s for `/api/flips`)
with long gaps: one request a minute while a tab is open. Estimate for 4h a
day:

- Request-based: about 60 requests/hour × ~1s ≈ 60 vCPU-seconds an hour, so
  ~7,200 a month. The request-based free tier is 180,000 vCPU-seconds a
  month.
- Instance-based: the instance stays up the whole session, plus idle time
  before it scales down. 4h × 3,600s × 30 ≈ 430,000 vCPU-seconds a month,
  well past 180,000. Instance-based may have a smaller free allowance or none
  (not checked); either way most of it would be billed.

Instance-based only pays off for steady traffic or real background work.
Neither applies here.

**What it changed in the app (OSRS-65).** With CPU throttled between
requests, the old background loops that refreshed prices on a timer would
stall. They were replaced with refresh-on-request: middleware on `/api/*`
calls `ensure_fresh()`, which refetches each wiki endpoint past its TTL once,
behind a lock. Same 60s/300s/24h cadence as before, and no fetches at all
while nobody uses the app, which is kinder to the wiki too. The local CLI uses
the same code path.

### Hardening (OSRS-31)
- Request timeout 60s, concurrency 20 (OSRS-60): concurrency 80 could run the
  512Mi instance out of memory. After a timeout the handler keeps running in
  the container, but with no request active its CPU is throttled, so it
  stalls until the next request (the 60s poll) wakes the instance.
- Two lanes (OSRS-30, OSRS-62): CI ships the image; Terraform owns the rest of
  the service; traffic is set by hand with gcloud for rollbacks and gradual
  rollouts (`ops/runbooks/rollback.md`).

## Consequences

- **State is lost on scale to zero.** Pins (`positions.json`) and the cache
  reset once the idle instance is shut down (typically within 15 minutes).
  Persistent pins (e.g. a Cloud Storage volume) is a noted follow-up.
- **Cold starts:** the first request after idle waits for the container to
  start and prime the cache from the wiki (a few seconds; startup CPU boost
  helps).
- **No background work in the app.** Future scheduled jobs (price or text
  ingestion) need Cloud Scheduler with Cloud Run jobs, not loops in the
  service.
- **One instance is the ceiling.** If the app ever had more users, state has
  to move out of memory first (shared cache, stored pins), then max can rise.
- **Egress is the next cost to watch:** `/api/flips` is ~4 MB before gzip on
  every poll (OSRS-66).
- **Portable:** the same image runs locally (`compose.yaml`) and could run on
  another provider's container service, which suits the multi-cloud plan.

Free-tier figure (180,000 vCPU-seconds a month, request-based) confirmed by
Joe on 2026-11-20. It can change; the budget and killswitch (ADR 0005) are
the backstop.
