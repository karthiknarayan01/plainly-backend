# OPENROUTER_API_KEY is a real billable credential — stored in Secret
# Manager and injected into the worker via Cloud Run's native secret
# env-var support, not a plain-text value in Terraform state.

resource "google_secret_manager_secret" "openrouter_api_key" {
  secret_id = "openrouter-api-key"

  replication {
    auto {}
  }

  depends_on = [google_project_service.apis]
}

resource "google_secret_manager_secret_version" "openrouter_api_key" {
  secret      = google_secret_manager_secret.openrouter_api_key.id
  secret_data = var.openrouter_api_key
}

resource "google_secret_manager_secret_iam_member" "worker_openrouter_accessor" {
  secret_id = google_secret_manager_secret.openrouter_api_key.id
  role      = "roles/secretmanager.secretAccessor"
  member    = "serviceAccount:${google_service_account.worker_runtime.email}"
}

variable "openrouter_api_key" {
  description = "OpenRouter API key for the worker's writer/judge calls — pass via TF_VAR_openrouter_api_key, never commit a real value"
  type        = string
  sensitive   = true
}

# db_password (variable already declared in database.tf) was inert until
# now — the stub services never actually connected to Postgres. This is
# the first real consumer, so it goes through Secret Manager rather than
# becoming a plain-text env var on both services.
resource "google_secret_manager_secret" "db_password" {
  secret_id = "db-password"

  replication {
    auto {}
  }

  depends_on = [google_project_service.apis]
}

resource "google_secret_manager_secret_version" "db_password" {
  secret      = google_secret_manager_secret.db_password.id
  secret_data = var.db_password
}

resource "google_secret_manager_secret_iam_member" "worker_db_password_accessor" {
  secret_id = google_secret_manager_secret.db_password.id
  role      = "roles/secretmanager.secretAccessor"
  member    = "serviceAccount:${google_service_account.worker_runtime.email}"
}

resource "google_secret_manager_secret_iam_member" "api_db_password_accessor" {
  secret_id = google_secret_manager_secret.db_password.id
  role      = "roles/secretmanager.secretAccessor"
  member    = "serviceAccount:${google_service_account.api_runtime.email}"
}
