# Runbook: Cloud Billing export to BigQuery (OSRS-13)

**Manual step.** Google has no API (so no Terraform resource) for switching
billing export on. Terraform makes the dataset; a person ticks the box.

```
Cloud Billing account ──(standard usage cost export, enabled here)──►
  osrs-infra.billing_export  (Terraform: infra/envs/prod/bigquery.tf)
    └─ gcp_billing_export_v1_<BILLING_ACCOUNT_ID>   (Google creates and writes it)
```

## Who can do it
- **Billing Account Administrator** or **Billing Account Costs Manager** on the
  billing account.
- **BigQuery User** (or Owner) on `osrs-infra`.
- `osrs-infra` must be linked to the same billing account.

## Order
1. `infra/iam`: `terraform apply` (CI read roles), then `./verify.sh`.
2. `infra/envs/prod`: `terraform apply`. Creates `billing_export` in
   `us-central1`.
3. Console steps below.

## Console steps
1. Console → **Billing** → pick the billing account → **Billing export**.
2. **BigQuery export** tab → **Standard usage cost** → **Edit settings**.
3. **Project:** `osrs-infra`. **Dataset:** `billing_export`.
4. **Save.**

Leave **Detailed usage cost** and **Pricing** off (not needed; pricing also
needs the BigQuery Data Transfer API).

## Check it worked
Data takes a few hours to arrive (up to a day).

```bash
# 1. Google's writer was added as dataset owner. Never remove it.
bq show --format=prettyjson osrs-infra:billing_export | grep billing-export-bigquery

# 2. The export table exists. Copy its name for the query below.
bq ls osrs-infra:billing_export

# 3. Cost by service, last 7 days. Dry run first: prints bytes scanned, costs nothing.
#    Drop --dry_run to run it. --maximum_bytes_billed (1 GB) makes the query
#    fail rather than scan past it.
bq query --use_legacy_sql=false --dry_run --maximum_bytes_billed=1000000000 '
SELECT service.description AS service,
       ROUND(SUM(cost), 2) AS cost,
       ROUND(SUM((SELECT SUM(c.amount) FROM UNNEST(credits) c)), 2) AS credits
FROM `osrs-infra.billing_export.gcp_billing_export_v1_XXXXXX_XXXXXX_XXXXXX`
WHERE _PARTITIONTIME >= TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL 7 DAY)
GROUP BY service
ORDER BY cost DESC'
```

Net cost = `cost` + `credits` (credits are negative, e.g. free tier).

## Undo
Billing export page → **Standard usage cost** → **Edit settings** →
**Disable**. The dataset and its rows stay; Terraform blocks deleting the
dataset (`deletion_policy = "PREVENT"` and `prevent_destroy`).

## Gotchas
- **No backfill.** The dataset is regional (ADR 0001), so rows start on the day
  the export is enabled. Multi-region `US`/`EU` would add the previous month.
  Older costs are still in the console's billing reports.
- **Location is fixed** once the dataset exists. Moving means a new dataset
  and re-pointing the export; old rows stay behind.
- **Don't add `access` blocks** to the dataset in Terraform. They replace the
  whole access list and would remove Google's writer. Use
  `google_bigquery_dataset_iam_member`.
- **Every project on the billing account is exported**, including any outside
  Terraform (e.g. "My First Project").
- **Public repo.** The table name contains the billing account ID. Use the
  placeholder in docs and notes.
- Free tier: 10 GiB storage and 1 TiB queries a month. Always filter on
  `_PARTITIONTIME` and set `--maximum_bytes_billed`.
