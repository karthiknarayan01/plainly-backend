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
    service_account = google_service_account.api_runtime.email
    containers {
      # Placeholder — CI (.github/workflows/deploy.yml) owns the real
      # image via `gcloud run deploy` after the first build, and
      # lifecycle.ignore_changes below stops Terraform from fighting it
      # and reverting back to this placeholder on every unrelated apply.
      image = "us-docker.pkg.dev/cloudrun/container/hello"
      env {
        name  = "DATABASE_INSTANCE_CONNECTION_NAME"
        value = google_sql_database_instance.main.connection_name
      }
      env {
        name  = "DATABASE_USER"
        value = google_sql_user.app.name
      }
      env {
        name  = "DATABASE_NAME"
        value = google_sql_database.plainly.name
      }
      env {
        name = "DATABASE_PASSWORD"
        value_source {
          secret_key_ref {
            secret  = google_secret_manager_secret.db_password.secret_id
            version = "latest"
          }
        }
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
        name  = "DATABASE_INSTANCE_CONNECTION_NAME"
        value = google_sql_database_instance.main.connection_name
      }
      env {
        name  = "DATABASE_USER"
        value = google_sql_user.app.name
      }
      env {
        name  = "DATABASE_NAME"
        value = google_sql_database.plainly.name
      }
      env {
        name = "DATABASE_PASSWORD"
        value_source {
          secret_key_ref {
            secret  = google_secret_manager_secret.db_password.secret_id
            version = "latest"
          }
        }
      }
      env {
        name = "OPENROUTER_API_KEY"
        value_source {
          secret_key_ref {
            secret  = google_secret_manager_secret.openrouter_api_key.secret_id
            version = "latest"
          }
        }
      }
      resources {
        # Cloud Run's default only allocates CPU while handling an inbound
        # HTTP request — wrong for this service, which does its real work
        # in a background polling loop between health-check pings, not in
        # request handlers. Without this, confirmed by testing: a claimed
        # chunk sat "processing" for minutes with the writer/judge calls
        # never completing, CPU too starved to make progress.
        cpu_idle = false
      }
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
