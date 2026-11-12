# Free-tier policy for Terraform plans (OSRS-19).
#
# Input is `terraform show -json tfplan`. Only resources being created or
# updated are checked, so deleting a banned resource always passes and
# resources already in state are not re-judged. Every deny message names the
# resource address and says what to do instead.
#
# Run against a plan:  conftest test plan.json -p infra/policy --namespace free_tier
# Run the unit tests:  conftest verify -p infra/policy
package free_tier

import rego.v1

region := "us-central1" # ADR 0001: one region, inside GCS Always Free

# Resource types that cannot be free tier. The prefix catches the whole
# family: google_compute_* is Compute Engine, load balancers, Cloud NAT,
# routers, disks and addresses.
banned := {
	"google_compute_": "Compute Engine, load balancers and Cloud NAT are never free tier; use Cloud Run",
	"google_sql_": "Cloud SQL has no free tier; use Firestore or BigQuery",
	"google_container_": "GKE has no free tier; use Cloud Run",
	"google_redis_": "Memorystore has no free tier",
	"google_filestore_": "Filestore has no free tier; use GCS",
}

# Everything we expect to use. A type that is neither banned nor here is
# denied too, so adding a new service is a deliberate edit to this list.
allowed_prefixes := {
	"google_project", # google_project, google_project_service, google_project_iam_*
	"google_service_account",
	"google_iam_", # custom roles, WIF pools and providers
	"google_storage_bucket",
	"google_bigquery_",
	"google_pubsub_",
	"google_cloud_scheduler_",
	"google_cloud_run_v2_",
	"google_artifact_registry_",
	"google_firestore_",
	"google_secret_manager_",
	"google_monitoring_",
	"google_logging_",
	"google_billing_budget",
}

# Resources this plan would create or update. Replacements show as
# ["delete", "create"], so they are included.
changed contains rc if {
	some rc in input.resource_changes
	rc.mode == "managed"
	some action in rc.change.actions
	action in {"create", "update"}
}

# Iterate over banned and allowed
is_banned(type) if {
	some prefix, _ in banned
	startswith(type, prefix)
}

is_allowed(type) if {
	some prefix in allowed_prefixes
	startswith(type, prefix)
}

# Deny blocks like this are effectively union'd together - building up the denied list
deny contains msg if {
	some rc in changed
	some prefix, reason in banned
	startswith(rc.type, prefix)
	msg := sprintf("%s: %s", [rc.address, reason])
}

deny contains msg if {
	some rc in changed
	not is_banned(rc.type)
	not is_allowed(rc.type)
	msg := sprintf("%s: %s is not on the free-tier allowlist in infra/policy/free_tier.rego; add it there if it is free", [rc.address, rc.type])
}

# Cloud Run: an always-on instance is billed every second; cap scale-out so a
# traffic spike cannot run up a bill.
deny contains msg if {
	some rc in changed
	rc.type == "google_cloud_run_v2_service"
	some scaling in rc.change.after.template[_].scaling
	scaling.min_instance_count > 0
	msg := sprintf("%s: min_instance_count is %d; must be 0 so idle time is free", [rc.address, scaling.min_instance_count])
}

deny contains msg if {
	some rc in changed
	rc.type == "google_cloud_run_v2_service"
	some scaling in rc.change.after.template[_].scaling
	scaling.max_instance_count > 2
	msg := sprintf("%s: max_instance_count is %d; must be 2 or less", [rc.address, scaling.max_instance_count])
}

# Unset max_instance_count defaults to 100 and is not in `after` (it lands in
# `after_unknown`), so require it to be written down.
deny contains msg if {
	some rc in changed
	rc.type == "google_cloud_run_v2_service"
	not has_max_instances(rc)
	msg := sprintf("%s: set template.scaling.max_instance_count explicitly (2 or less); the provider default is 100", [rc.address])
}

has_max_instances(rc) if {
	some scaling in rc.change.after.template[_].scaling
	is_number(scaling.max_instance_count)
}

# Storage outside the region is outside Always Free (and the multi-region
# "US" is billed too). The plan keeps the case from the .tf file.
deny contains msg if {
	some rc in changed
	rc.type in {"google_storage_bucket", "google_bigquery_dataset"}
	lower(rc.change.after.location) != region
	msg := sprintf("%s: location %q is outside %s (ADR 0001)", [rc.address, rc.change.after.location, region])
}

# A location computed from another resource is not in `after` at plan time
# (it sits in `after_unknown`), which would make the rule above pass silently.
deny contains msg if {
	some rc in changed
	rc.type in {"google_storage_bucket", "google_bigquery_dataset"}
	rc.change.after_unknown.location == true
	msg := sprintf("%s: location is not known at plan time; write it as a literal or var.region so it can be checked", [rc.address])
}

# Artifact Registry storage is billed past 0.5 GB; images pile up without a
# cleanup policy.
deny contains msg if {
	some rc in changed
	rc.type == "google_artifact_registry_repository"
	count(object.get(rc.change.after, "cleanup_policies", [])) == 0
	msg := sprintf("%s: add at least one cleanup_policies block so old images are deleted", [rc.address])
}

# KEEP rules only protect versions from DELETE rules; on their own they
# delete nothing, so a KEEP-only repo grows forever.
deny contains msg if {
	some rc in changed
	rc.type == "google_artifact_registry_repository"
	policies := object.get(rc.change.after, "cleanup_policies", [])
	count(policies) > 0
	every p in policies {
		p.action != "DELETE"
	}
	msg := sprintf("%s: cleanup_policies has no DELETE rule, so nothing is ever deleted (KEEP only protects); add one, e.g. untagged older than 7 days", [rc.address])
}

# In dry-run mode the cleanup policy only logs what it would delete.
deny contains msg if {
	some rc in changed
	rc.type == "google_artifact_registry_repository"
	rc.change.after.cleanup_policy_dry_run == true
	msg := sprintf("%s: cleanup_policy_dry_run is true, so cleanup policies delete nothing; set it to false", [rc.address])
}
