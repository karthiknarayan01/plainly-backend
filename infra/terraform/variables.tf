variable "project_id" {
  description = "GCP project for the Plainly backend"
  type        = string
  default     = "plainly-backend-440007"
}

variable "region" {
  type    = string
  default = "us-central1"
}

variable "zone" {
  type    = string
  default = "us-central1-a"
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

variable "gpu_machine_type" {
  # a2-highgpu-1g pairs with a single A100 40GB. Chosen over the cheaper
  # L4 (24GB) because co-locating a ~14GB writing model + ~8GB judge model
  # at 8-bit leaves only ~2GB of headroom on an L4 — not enough for the
  # whole-document KV cache this design needs (see plainly-web design doc
  # §09). Revisit once exact model sizes are final — if the writing model
  # ends up smaller, or the two models run on separate boxes, L4 becomes
  # viable and meaningfully cheaper.
  description = "GCE machine type for the inference box"
  type        = string
  default     = "a2-highgpu-1g"
}

variable "gpu_type" {
  type    = string
  default = "nvidia-tesla-a100"
}

variable "gpu_count" {
  type    = number
  default = 1
}
