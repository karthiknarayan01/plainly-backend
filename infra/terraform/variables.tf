variable "project_id" {
  description = "GCP project for the Plainly backend"
  type        = string
  default     = "plainly-backend-440007"
}

variable "region" {
  type    = string
  default = "us-central1"
}

variable "github_repo" {
  description = "GitHub repo allowed to deploy via Workload Identity Federation"
  type        = string
  default     = "karthiknarayan01/plainly-backend"
}

variable "db_tier" {
  description = "Cloud SQL machine tier — smallest tier, per the cost-conscious v1 scope"
  type        = string
  default     = "db-f1-micro"
}

# --- Inference: two separate Cloud Run GPU services, scale-to-zero ---
# Split rather than co-located on purpose: Cloud Run bills per-request
# while each service is actually handling traffic, so each model gets
# exactly the GPU tier it needs instead of both sharing one bigger,
# constantly-idle-capable card. Neither runs, or costs anything, between
# requests.

variable "writer_gpu_type" {
  # Qwen3-32B at 8-bit is ~32GB of weights alone — already past the L4's
  # 24GB ceiling before counting the whole-document KV cache this design
  # needs (§09 of the plainly-web design doc). RTX PRO 6000 Blackwell
  # (96GB) leaves generous headroom for that. Confirm region availability
  # before applying — it's not available everywhere yet.
  type    = string
  default = "nvidia-rtx-pro-6000"
}

variable "judge_gpu_type" {
  # The judge (8B) only needs the cheaper L4 (24GB) — it only ever sees
  # one passage pair at a time, not a whole cached document, so 8 vCPU /
  # 32Gi (already under the current 40Gi/20vCPU region caps) is enough.
  #
  # Considered switching to RTX PRO 6000 to dodge the pending L4 GPU
  # quota approval (this project has 0 L4 quota but 3 free RTX PRO 6000
  # GPUs by default) — doesn't actually work. Confirmed by testing: Cloud
  # Run enforces a fixed CPU/memory pairing per GPU type, and for RTX PRO
  # 6000 the floor is 20 vCPU / exactly 80Gi, regardless of what the
  # workload needs. That hits the exact same memory-quota wall as the
  # writer, for no benefit — L4's actual blocker (GPU count = 0) is the
  # smaller, more normal ask, so staying on L4 and waiting on that
  # approval is the better bet.
  type    = string
  default = "nvidia-l4"
}

variable "eval_operator_email" {
  # Normally only the worker's own service account can invoke the writer/
  # judge Cloud Run services (see writer_invoker/judge_invoker in
  # inference.tf) — the model services aren't public. eval/run_eval.py
  # needs to call them directly from a human's machine, so set this to
  # your own Google account email (in the gitignored terraform.tfvars,
  # not here) to grant just that one identity run.invoker too. Leave
  # empty to skip — no eval_operator grants are created by default.
  type    = string
  default = ""
}
