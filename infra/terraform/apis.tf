# Already enabled manually during initial setup — declared here too so a
# fresh `terraform apply` on a new project stays reproducible.
resource "google_project_service" "apis" {
  for_each = toset([
    "compute.googleapis.com",
    "sqladmin.googleapis.com",
    "run.googleapis.com",
    "iamcredentials.googleapis.com",
    "cloudresourcemanager.googleapis.com",
    "artifactregistry.googleapis.com",
    "secretmanager.googleapis.com", # OPENROUTER_API_KEY for the worker — see secrets.tf
  ])
  service            = each.value
  disable_on_destroy = false
}
