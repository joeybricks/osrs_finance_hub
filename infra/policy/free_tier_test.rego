# Unit tests for free_tier.rego. Run: conftest verify -p infra/policy
# Each fixture is the smallest slice of `terraform show -json` that the rule
# reads: resource_changes[].{address,mode,type,change.{actions,after}}.
package free_tier

import rego.v1

plan(resources) := {"resource_changes": resources}

create(address, type, after) := {
	"address": address,
	"mode": "managed",
	"type": type,
	"change": {"actions": ["create"], "after": after},
}

# Today's envs/prod resources: the policy must pass the real infra as it is.
test_current_prod_passes if {
	count(deny) == 0 with input as plan([
		create("google_project.infra", "google_project", {"project_id": "osrs-infra"}),
		create("google_project_service.pubsub", "google_project_service", {}),
		create("google_storage_bucket.tfstate", "google_storage_bucket", {"location": "US-CENTRAL1"}),
		create("google_bigquery_dataset.billing_export", "google_bigquery_dataset", {"location": "us-central1"}),
		create("google_pubsub_topic.budget_alerts", "google_pubsub_topic", {}),
		create("google_pubsub_subscription.audit", "google_pubsub_subscription", {}),
	])
}

test_compute_instance_denied_once_with_readable_message if {
	msgs := deny with input as plan([create("google_compute_instance.vm", "google_compute_instance", {})])
	count(msgs) == 1
	msgs == {"google_compute_instance.vm: Compute Engine, load balancers and Cloud NAT are never free tier; use Cloud Run"}
}

test_cloud_sql_denied if {
	msgs := deny with input as plan([create("google_sql_database_instance.db", "google_sql_database_instance", {})])
	count(msgs) == 1
	some msg in msgs
	contains(msg, "Cloud SQL has no free tier")
}

test_load_balancer_and_nat_denied if {
	msgs := deny with input as plan([
		create("google_compute_forwarding_rule.lb", "google_compute_forwarding_rule", {}),
		create("google_compute_router_nat.nat", "google_compute_router_nat", {}),
	])
	count(msgs) == 2
}

test_unknown_type_denied if {
	msgs := deny with input as plan([create("google_dataflow_job.etl", "google_dataflow_job", {})])
	count(msgs) == 1
	some msg in msgs
	contains(msg, "google_dataflow_job is not on the free-tier allowlist")
}

# Deleting or leaving a banned resource alone is not a violation.
test_delete_and_noop_ignored if {
	count(deny) == 0 with input as plan([
		{"address": "google_compute_instance.old", "mode": "managed", "type": "google_compute_instance", "change": {"actions": ["delete"], "after": null}},
		{"address": "google_compute_instance.kept", "mode": "managed", "type": "google_compute_instance", "change": {"actions": ["no-op"], "after": {}}},
	])
}

test_replace_is_checked if {
	count(deny) == 1 with input as plan([{
		"address": "google_compute_instance.vm",
		"mode": "managed",
		"type": "google_compute_instance",
		"change": {"actions": ["delete", "create"], "after": {}},
	}])
}

test_data_sources_ignored if {
	count(deny) == 0 with input as plan([{
		"address": "data.google_compute_zones.all",
		"mode": "data",
		"type": "google_compute_zones",
		"change": {"actions": ["read"], "after": {}},
	}])
}

cloud_run(min, max) := create("google_cloud_run_v2_service.api", "google_cloud_run_v2_service", {"template": [{"scaling": [{"min_instance_count": min, "max_instance_count": max}]}]})

test_cloud_run_scale_to_zero_passes if {
	count(deny) == 0 with input as plan([cloud_run(0, 2)])
}

test_cloud_run_min_instances_denied if {
	msgs := deny with input as plan([cloud_run(1, 2)])
	count(msgs) == 1
	some msg in msgs
	contains(msg, "min_instance_count is 1")
}

test_cloud_run_max_instances_denied if {
	msgs := deny with input as plan([cloud_run(0, 3)])
	count(msgs) == 1
	some msg in msgs
	contains(msg, "max_instance_count is 3")
}

test_cloud_run_unset_max_denied if {
	msgs := deny with input as plan([create("google_cloud_run_v2_service.api", "google_cloud_run_v2_service", {"template": [{"scaling": []}]})])
	count(msgs) == 1
	some msg in msgs
	contains(msg, "set template.scaling.max_instance_count explicitly")
}

test_bucket_outside_region_denied if {
	msgs := deny with input as plan([create("google_storage_bucket.eu", "google_storage_bucket", {"location": "EUROPE-WEST2"})])
	count(msgs) == 1
	some msg in msgs
	contains(msg, `location "EUROPE-WEST2" is outside us-central1`)
}

test_bucket_multi_region_denied if {
	count(deny) == 1 with input as plan([create("google_storage_bucket.us", "google_storage_bucket", {"location": "US"})])
}

test_bigquery_outside_region_denied if {
	count(deny) == 1 with input as plan([create("google_bigquery_dataset.eu", "google_bigquery_dataset", {"location": "EU"})])
}

test_artifact_registry_without_cleanup_denied if {
	msgs := deny with input as plan([create("google_artifact_registry_repository.images", "google_artifact_registry_repository", {"cleanup_policies": []})])
	count(msgs) == 1
	some msg in msgs
	contains(msg, "add at least one cleanup_policies block")
}

test_artifact_registry_with_cleanup_passes if {
	count(deny) == 0 with input as plan([create("google_artifact_registry_repository.images", "google_artifact_registry_repository", {"cleanup_policies": [{"id": "keep-5", "action": "KEEP"}]})])
}

test_secret_manager_and_scheduler_allowed if {
	count(deny) == 0 with input as plan([
		create("google_secret_manager_secret.key", "google_secret_manager_secret", {}),
		create("google_cloud_scheduler_job.tick", "google_cloud_scheduler_job", {}),
		create("google_firestore_database.db", "google_firestore_database", {}),
		create("google_monitoring_alert_policy.p", "google_monitoring_alert_policy", {}),
	])
}
