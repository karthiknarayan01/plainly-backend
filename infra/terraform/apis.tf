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
    "cloudbuild.googleapis.com", # writer/judge image builds — GitHub-hosted runners don't have enough disk for the vLLM base image
  ])
  service            = each.value
  disable_on_destroy = false
}
