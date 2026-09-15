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
  # The judge (8B) is comfortable on the cheaper L4 (24GB) — it only ever
  # sees one passage pair at a time, not a whole cached document, so its
  # memory needs are much smaller than the writer's.
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
