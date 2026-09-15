# Two scale-to-zero Cloud Run GPU services. Images built and pushed by
# CI — see .github/workflows/deploy.yml and services/writer-model,
# services/judge-model. No public access: only the worker's service
# account can invoke either (see run_invoker bindings below).

resource "google_cloud_run_v2_service" "writer_model" {
  name     = "plainly-writer-model"
  location = var.region

  template {
    containers {
      image = "us-docker.pkg.dev/cloudrun/container/hello" # placeholder until first real build
      resources {
        limits = {
          cpu              = "20"
          memory           = "80Gi"
          "nvidia.com/gpu" = "1"
        }
      }
      ports {
        container_port = 8000
      }
    }
    node_selector {
      accelerator = var.writer_gpu_type
    }
    scaling {
      min_instance_count = 0 # scale to zero — this is the whole point
      max_instance_count = 1 # reduced from 2 — regional Cloud Run CPU/memory quota on this new project doesn't have room for more yet
    }
  }
}

resource "google_cloud_run_v2_service" "judge_model" {
  name     = "plainly-judge-model"
  location = var.region

  template {
    containers {
      image = "us-docker.pkg.dev/cloudrun/container/hello"
      resources {
        limits = {
          cpu              = "8"
          memory           = "32Gi"
          "nvidia.com/gpu" = "1"
        }
      }
      ports {
        container_port = 8000
      }
    }
    node_selector {
      accelerator = var.judge_gpu_type
    }
    scaling {
      min_instance_count = 0
      max_instance_count = 1 # reduced from 2 — regional Cloud Run CPU/memory quota on this new project doesn't have room for more yet
    }
  }
}

# Only the worker (running as its own runtime identity, not the deployer)
# can call either model service — not public.
resource "google_cloud_run_v2_service_iam_member" "writer_invoker" {
  name     = google_cloud_run_v2_service.writer_model.name
  location = var.region
  role     = "roles/run.invoker"
  member   = "serviceAccount:${google_service_account.worker_runtime.email}"
}

resource "google_cloud_run_v2_service_iam_member" "judge_invoker" {
  name     = google_cloud_run_v2_service.judge_model.name
  location = var.region
  role     = "roles/run.invoker"
  member   = "serviceAccount:${google_service_account.worker_runtime.email}"
}
