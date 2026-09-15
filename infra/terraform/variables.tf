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
