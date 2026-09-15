# API and worker layers — the design doc's "N replicas" for each. Cloud
# Run gives an HTTPS URL automatically, so no custom load balancer is
# needed for v1 (§06 of the design doc simplifies to this on purpose).

resource "google_artifact_registry_repository" "images" {
  location      = var.region
  repository_id = "plainly"
  format        = "DOCKER"
  depends_on    = [google_project_service.apis]
}

resource "google_cloud_run_v2_service" "api" {
  name     = "plainly-api"
  location = var.region

  template {
    containers {
      # Placeholder — CI (.github/workflows/deploy.yml) owns the real
      # image via `gcloud run deploy` after the first build, and
      # lifecycle.ignore_changes below stops Terraform from fighting it
      # and reverting back to this placeholder on every unrelated apply.
      image = "us-docker.pkg.dev/cloudrun/container/hello"
      env {
        name  = "DATABASE_HOST"
        value = google_sql_database_instance.main.connection_name
      }
    }
    scaling {
      min_instance_count = 0 # scales to zero between jobs
      max_instance_count = 5
    }
  }

  lifecycle {
    ignore_changes = [template[0].containers[0].image]
  }

  depends_on = [google_project_service.apis]
}

resource "google_cloud_run_v2_service" "worker" {
  name     = "plainly-worker"
  location = var.region

  template {
    service_account = google_service_account.worker_runtime.email
    containers {
      image = "us-docker.pkg.dev/cloudrun/container/hello"
      env {
        name  = "DATABASE_HOST"
        value = google_sql_database_instance.main.connection_name
      }
      # Writer/judge models now come from OpenRouter (hosted, pay-per-token)
      # instead of self-hosted Cloud Run GPU services — no more per-model
      # endpoint URLs. Whatever OpenRouter API key/model config the worker
      # needs is a follow-up (likely a Secret Manager-backed env var), not
      # yet wired up here.
    }
    scaling {
      min_instance_count = 1 # at least one worker running to poll the queue
      max_instance_count = 5
    }
  }

  lifecycle {
    ignore_changes = [template[0].containers[0].image]
  }

  depends_on = [google_project_service.apis]
}

resource "google_cloud_run_v2_service_iam_member" "api_public" {
  name     = google_cloud_run_v2_service.api.name
  location = var.region
  role     = "roles/run.invoker"
  member   = "allUsers"
}
