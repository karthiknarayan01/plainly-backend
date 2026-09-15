# Cloud SQL — the job store from the design doc: rewrite_jobs (including
# the uploaded file as bytea) and rewrite_chunks. Smallest tier by
# default; this table stays small (a few rows per job), not a bulk store.

resource "google_sql_database_instance" "main" {
  name             = "plainly-db"
  database_version = "POSTGRES_16"
  region           = var.region

  settings {
    tier              = var.db_tier
    availability_type = "ZONAL" # single zone — fine at current scale, no HA needed yet
    backup_configuration {
      enabled = true
    }
  }

  deletion_protection = true
  depends_on          = [google_project_service.apis]
}

resource "google_sql_database" "plainly" {
  name     = "plainly"
  instance = google_sql_database_instance.main.name
}

resource "google_sql_user" "app" {
  name     = "plainly_app"
  instance = google_sql_database_instance.main.name
  password = var.db_password
}

variable "db_password" {
  description = "Password for the app's Postgres user — pass via TF_VAR_db_password, never commit a real value"
  type        = string
  sensitive   = true
}
