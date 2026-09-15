# The service account, WIF pool, and WIF provider below already exist —
# created directly via gcloud while setting this up (see repo history/
# conversation). They're declared here for documentation and so a fresh
# environment can reproduce this; run `terraform import` against each
# before applying, or this file will try to recreate what's already there.
#
# What's NOT done yet, and needs a real `terraform apply` (blocked by a
# safety check on granting IAM roles — needs explicit approval before
# running): binding the WIF provider to this service account, and
# granting it the roles it needs to actually deploy.

resource "google_service_account" "deployer" {
  account_id   = "plainly-deployer"
  display_name = "Plainly CI/CD Deployer"
}

# Runtime identity for the worker service — separate from the deployer.
# The deployer's job is to push new revisions; this is what the worker
# actually runs as, and it's the identity that needs run.invoker on the
# two inference services (see inference.tf).
resource "google_service_account" "worker_runtime" {
  account_id   = "plainly-worker-runtime"
  display_name = "Plainly Worker Runtime"
}

resource "google_iam_workload_identity_pool" "github" {
  workload_identity_pool_id = "github-pool"
  display_name              = "GitHub Actions Pool"
}

resource "google_iam_workload_identity_pool_provider" "github" {
  workload_identity_pool_id          = google_iam_workload_identity_pool.github.workload_identity_pool_id
  workload_identity_pool_provider_id = "github-provider"
  display_name                       = "GitHub OIDC"

  attribute_mapping = {
    "google.subject"       = "assertion.sub"
    "attribute.repository" = "assertion.repository"
  }
  attribute_condition = "assertion.repository=='${var.github_repo}'"

  oidc {
    issuer_uri = "https://token.actions.githubusercontent.com"
  }
}

# --- Not yet applied — see note above ---

resource "google_service_account_iam_binding" "wif_binding" {
  service_account_id = google_service_account.deployer.name
  role               = "roles/iam.workloadIdentityUser"
  members = [
    "principalSet://iam.googleapis.com/${google_iam_workload_identity_pool.github.name}/attribute.repository/${var.github_repo}"
  ]
}

resource "google_project_iam_member" "deployer_roles" {
  for_each = toset([
    "roles/run.admin",
    "roles/cloudsql.admin",
    "roles/artifactregistry.writer",
    "roles/iam.serviceAccountUser",
    "roles/cloudbuild.builds.editor", # submit/watch builds for writer/judge images — see cloudbuild.tf
  ])
  project = var.project_id
  role    = each.value
  member  = "serviceAccount:${google_service_account.deployer.email}"
}

# Cloud Build's own default service account executes the build itself
# (not the deployer — the deployer only submits/watches it) and needs
# push access to Artifact Registry.
data "google_project" "current" {
  project_id = var.project_id
}

resource "google_project_iam_member" "cloudbuild_default_sa_artifact_writer" {
  project    = var.project_id
  role       = "roles/artifactregistry.writer"
  member     = "serviceAccount:${data.google_project.current.number}@cloudbuild.gserviceaccount.com"
  depends_on = [google_project_service.apis]
}
