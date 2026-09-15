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
    # Zonal-redundant GPU quota is a separate, harder-to-get approval —
    # not needed for a single-region, non-HA-critical v1 anyway. Also
    # makes the pending memory-quota increase (still needed regardless —
    # this doesn't fix that one) more likely to be granted quickly.
    gpu_zonal_redundancy_disabled = true
  }

  lifecycle {
    ignore_changes = [template[0].containers[0].image]
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
    gpu_zonal_redundancy_disabled = true
  }

  lifecycle {
    ignore_changes = [template[0].containers[0].image]
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

# Lets eval/run_eval.py call the model services directly from a human's
# machine (via `gcloud auth print-identity-token`), without making either
# service public. Only created if eval_operator_email is set.
resource "google_cloud_run_v2_service_iam_member" "writer_invoker_eval_operator" {
  count    = var.eval_operator_email != "" ? 1 : 0
  name     = google_cloud_run_v2_service.writer_model.name
  location = var.region
  role     = "roles/run.invoker"
  member   = "user:${var.eval_operator_email}"
}

resource "google_cloud_run_v2_service_iam_member" "judge_invoker_eval_operator" {
  count    = var.eval_operator_email != "" ? 1 : 0
  name     = google_cloud_run_v2_service.judge_model.name
  location = var.region
  role     = "roles/run.invoker"
  member   = "user:${var.eval_operator_email}"
}
