output "api_url" {
  value = google_cloud_run_v2_service.api.uri
}

output "db_connection_name" {
  value = google_sql_database_instance.main.connection_name
}

output "writer_model_url" {
  value = google_cloud_run_v2_service.writer_model.uri
}

output "judge_model_url" {
  value = google_cloud_run_v2_service.judge_model.uri
}

output "wif_provider" {
  description = "Full resource name for the GitHub Actions workflow's auth step"
  value       = google_iam_workload_identity_pool_provider.github.name
}

output "deployer_service_account" {
  value = google_service_account.deployer.email
}
