# The inference box. Both models co-located per §09 of the design doc —
# writing model on port 8000, judge model on port 8001. No internal load
# balancer yet: with exactly one instance, there's nothing to route
# across. Add that back once a second instance exists (affinity-by-job-id,
# as designed in the plainly-web architecture doc).

resource "google_compute_network" "vpc" {
  name                    = "plainly-vpc"
  auto_create_subnetworks = true
}

resource "google_compute_firewall" "internal_inference" {
  name    = "allow-internal-inference"
  network = google_compute_network.vpc.name
  allow {
    protocol = "tcp"
    ports    = ["8000", "8001"]
  }
  source_ranges = ["10.128.0.0/9"] # internal GCP ranges only — never public
}

resource "google_compute_instance" "inference" {
  name         = "plainly-inference"
  machine_type = var.gpu_machine_type
  zone         = var.zone

  guest_accelerator {
    type  = var.gpu_type
    count = var.gpu_count
  }

  # Required alongside any guest_accelerator.
  scheduling {
    on_host_maintenance = "TERMINATE"
    automatic_restart   = true
  }

  boot_disk {
    initialize_params {
      # Deep Learning VM image — CUDA + drivers preinstalled, saves
      # provisioning the GPU driver stack by hand.
      image = "projects/ml-images/global/images/family/common-cu124-debian-11"
      size  = 200
    }
  }

  network_interface {
    network = google_compute_network.vpc.name
    # No access_config block — no public IP. Reached only from the
    # worker, over the internal network.
  }

  metadata_startup_script = file("${path.module}/scripts/start-vllm.sh")

  depends_on = [google_project_service.apis]
}
