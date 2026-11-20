# Runbook: roll back the app (OSRS-62)

**A new version is broken; put the last good one back.** Cloud Run keeps every
revision (image + settings) it has run, so a rollback is a traffic change, not
a rebuild. It takes seconds.

```
Normal                            Rolled back
traffic: LATEST = 100%            traffic: <good> = 100%   ← pinned by name
                                           LATEST  =   0%
<new> ◄── LATEST, all requests    <good> ◄── all requests
                                  <new>  ◄── LATEST, no requests
```

## Who owns traffic
Traffic is set **by hand with gcloud** (Joe, 2026-11-20). Terraform ignores it
(`ignore_changes` in `infra/envs/prod/cloud_run.tf`), so an apply never undoes
a rollback. CI deploys the image only (`infra/README.md` App deploys).

Who can run these: project Owner (Joe). `deployer-sa` could too
(`run.developer`), but CI only deploys.

## 1. Find the last good revision

```bash
gcloud run revisions list --service osrs-finance-hub \
  --project osrs-infra --region us-central1
```

Newest first. Pick the one before the broken one. **Never** pick
`osrs-finance-hub-00001-wps`: it is Google's `hello` placeholder from the
first Terraform apply.

Cloud Run keeps its own copy of a revision's image, so cold starts don't pull
from Artifact Registry and its cleanup (keep the 5 newest, delete older ones
after 7 days) doesn't break a revision that is serving. Google only promises
that copy "as long as it is used by a serving revision", though, and a
rollback target has had 0% traffic. As a precaution, check its digest is
still in the registry; it must be for the "lasting fix" below anyway:

```bash
# the revision's image digest
gcloud run revisions describe <good-revision> --project osrs-infra \
  --region us-central1 --format='value(spec.containers[0].image)'
# is that digest still listed?
gcloud artifacts docker images list \
  us-central1-docker.pkg.dev/osrs-infra/osrs-finance-hub --include-tags
```

## 2. Roll back

```bash
gcloud run services update-traffic osrs-finance-hub \
  --project osrs-infra --region us-central1 \
  --to-revisions <good-revision>=100
```

Check: open the app (phone or browser), and

```bash
gcloud run services describe osrs-finance-hub --project osrs-infra \
  --region us-central1 --format='value(status.traffic)'
```

## 3. While rolled back
- **Deploys get no traffic.** gcloud only gives a new revision the traffic
  that was on LATEST, which is now 0%. So the fix can be deployed through CI
  as normal; it lands with 0%.
- **The CI smoke test fails** on that deploy ("gets 0% of traffic, expected
  100"). Expected while pinned; it is the reminder that traffic is still
  pinned.
- `terraform apply` of `envs/prod` is safe: it doesn't touch traffic.

## 4. Move to the fixed revision

All at once:

```bash
gcloud run services update-traffic osrs-finance-hub \
  --project osrs-infra --region us-central1 --to-latest
```

Or gradually: give the new revision a share, watch it, then raise it.

```bash
gcloud run services update-traffic osrs-finance-hub \
  --project osrs-infra --region us-central1 \
  --to-revisions <new-revision>=10,<good-revision>=90
# later
gcloud run services update-traffic osrs-finance-hub \
  --project osrs-infra --region us-central1 --to-latest
```

Each request is routed separately, so with one user a 10% split means about
1 in 10 page loads hits the new revision, not 10% of users.

**Prefer all at once for this app.** `max_instance_count = 1` is per revision,
so a split runs two instances, each with its own pins and price cache. Until
`--to-latest`, a pin can vanish from one page load to the next, the two
revisions can show different prices, and the wiki sees two clients refreshing.
Use a split only for a short check.

**Finish with `--to-latest`.** Until then traffic stays pinned and later deploys
get 0%.

## Lasting fix without a new commit
If the fix will take a while, redeploy the good image so it becomes LATEST
again (deploys then behave normally):

```bash
gcloud run deploy osrs-finance-hub --project osrs-infra --region us-central1 \
  --image us-central1-docker.pkg.dev/osrs-infra/osrs-finance-hub/osrs-finance-hub@sha256:<digest>
gcloud run services update-traffic osrs-finance-hub \
  --project osrs-infra --region us-central1 --to-latest
```

The broken code is still on `master`; the next CI deploy brings it back unless
it is reverted first.
