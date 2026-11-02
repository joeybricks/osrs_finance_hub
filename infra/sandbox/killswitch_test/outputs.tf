output "canary_url" {
  description = "Read it with: gcloud storage cat <this>"
  value       = "gs://${google_storage_bucket.canary.name}/${google_storage_bucket_object.canary.name}"
}
