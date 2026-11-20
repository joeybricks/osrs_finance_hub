# The app on Cloud Run (OSRS-28, OSRS-47). Scales to zero, so idle time is free.
#
# Image: Terraform only sets the image when it creates the service, using
# Google's public "hello" placeholder. Real images are shipped with
# `gcloud run deploy` (by hand in OSRS-49, then CI in OSRS-30), and
# ignore_changes below stops a later apply from rolling them back.
#
# Access (OSRS-29, ADR 0006): two gates, both must pass.
#   browser → IAP (Google sign-in; is this user allowed?) → Cloud Run IAM
#   (is the caller allowed to invoke?) → app
# IAP admits the users in iam/iap.tf; Cloud Run admits only the IAP service
# agent (and project Owners). Neither list is in this file: identity grants
# live in iam/, which is applied locally, so no email reaches a PR plan.
resource "google_cloud_run_v2_service" "app" {
  project  = google_project.infra.project_id
  name     = "osrs-finance-hub"
  location = var.region

  # Reachable from the internet (needed for the phone on mobile data); IAP and
  # IAM still decide who gets in.
  ingress = "INGRESS_TRAFFIC_ALL"

  # Sign-in page in front of the service, no load balancer needed. Free.
  # No launch_stage: Google's example sets BETA, but the API keeps IAP on at
  # the default GA (the console reset it to GA without turning IAP off).
  # The sign-in client is a custom External OAuth client, made once in the
  # console and set on this service only. Terraform can't create External
  # clients (google_iap_client is Internal-only; ADR 0006).
  iap_enabled = true

  # The service holds no data; a redeploy rebuilds it in a minute. Off so
  # `terraform destroy` works without a separate apply first.
  deletion_protection = false

  template {
    # Runs as runtime-sa (iam/), which has no roles. Without this Cloud Run
    # would use the Compute Engine default account, which has project Editor.
    service_account = local.runtime_sa_email

    # Hardening (OSRS-60). Defaults are 300s and 80.
    # Timeout: the slowest request, the long-term scan (25 /timeseries calls,
    # 3 at a time, so 9 rounds), takes seconds. A slow wiki (~7s a call) or a
    # hanging one (20s httpx timeout) passes 60s; the UI shows the 504 as an
    # error, which beats holding a billed request open.
    timeout = "60s"
    # Concurrency: each in-flight /api/flips holds about 5 MB while it builds
    # the ~4 MB payload. Measured locally: 20 at once peaks at ~175 MB, 40 at
    # ~280 MB, so 80 could run the 512Mi instance out of memory. One user
    # peaks at about 8 (page load, 3 rating workers, the 60s poll).
    max_instance_request_concurrency = 20

    scaling {
      min_instance_count = 0 # idle = no instance = free
      # One instance: pins (positions.json) and the price cache live in each
      # instance's memory/disk, so two would disagree. Pins still reset when
      # the instance scales to zero; persistent storage is a follow-up.
      max_instance_count = 1
    }

    containers {
      image = "us-docker.pkg.dev/cloudrun/container/hello"

      ports {
        container_port = 8080 # the one port Cloud Run sends traffic to; it sets PORT to match
      }

      resources {
        limits = {
          cpu    = "1"
          memory = "512Mi" # cache is four bulk API responses, well under this
        }
        # Request-based billing: CPU only while serving a request. The default,
        # set so the plan shows it. The app has no background loops (OSRS-65).
        cpu_idle = true
        # Extra CPU during startup, for shorter cold starts. Free.
        startup_cpu_boost = true
      }

      # Wiki User-Agent contact. Not a secret; no default in code (OSRS-37).
      env {
        name  = "OSRS_FINANCE_HUB_CONTACT"
        value = "joey_bricks"
      }

      # /health never calls the wiki. The app answers it only after priming the
      # cache from the wiki (~1s locally), so allow up to 30s on startup.
      startup_probe {
        http_get {
          path = "/health"
        }
        period_seconds    = 2
        timeout_seconds   = 2
        failure_threshold = 15
      }

      liveness_probe {
        http_get {
          path = "/health"
        }
        period_seconds    = 30
        timeout_seconds   = 5
        failure_threshold = 3
      }
    }
  }

  lifecycle {
    # Deploys own the image from now on. client/client_version are stamped by
    # gcloud on every deploy; without these the next plan would undo the deploy.
    # Traffic is set by hand with gcloud (Joe, OSRS-62): rollbacks and gradual
    # rollouts pin it to named revisions, and without this an apply would put
    # it back to 100% LATEST, undoing a rollback (ops/runbooks/rollback.md).
    ignore_changes = [
      template[0].containers[0].image,
      client,
      client_version,
      traffic,
    ]
  }

  depends_on = [google_project_service.run, google_project_service.iap]
}

output "cloud_run_url" {
  description = "Service URL. Open it in a browser and sign in with Google (IAP)."
  value       = google_cloud_run_v2_service.app.uri
}
