# Latency metrics derived from the worker's structured JSON logs
# (services/worker/logging_json.py) — not a second instrumentation
# system. Cloud Run already ingests stdout as Cloud Logging entries and
# auto-parses the JSON payload; a log-based metric turns one numeric
# field from that payload into a Cloud Monitoring DISTRIBUTION metric,
# queryable for p50/p95/p99 with no app-side dependency (no Prometheus,
# no OpenTelemetry collector — this stack had no existing metrics infra
# to plug into, and one wasn't worth standing up just for this).
#
# Field names below must match services/worker/llm.py's and main.py's
# log() calls exactly (jsonPayload.ttft_ms, jsonPayload.total_ms,
# jsonPayload.duration_ms) — there is no schema enforcement between a
# Python log() call and a Terraform filter string, so a renamed field in
# one place silently breaks the corresponding metric in the other.

resource "google_logging_metric" "writer_ttft_ms" {
  name = "writer_ttft_ms"
  # Time to first token — what the README screenshot (p95) measures.
  # Distinct from writer_total_ms below: a slow provider route can have
  # a fast first token and a slow tail, or vice versa, and only
  # measuring one hides which failure mode is actually happening.
  filter          = "resource.type=\"cloud_run_revision\" jsonPayload.event=\"llm_call_end\" jsonPayload.ttft_ms>0"
  value_extractor = "EXTRACT(jsonPayload.ttft_ms)"
  metric_descriptor {
    metric_kind = "DELTA"
    value_type  = "DISTRIBUTION"
    unit        = "ms"
  }
  bucket_options {
    exponential_buckets {
      num_finite_buckets = 30
      growth_factor      = 1.4
      scale              = 10
    }
  }
  depends_on = [google_project_service.apis]
}

resource "google_logging_metric" "writer_total_ms" {
  name            = "writer_total_ms"
  filter          = "resource.type=\"cloud_run_revision\" jsonPayload.event=\"llm_call_end\" jsonPayload.total_ms>0"
  value_extractor = "EXTRACT(jsonPayload.total_ms)"
  metric_descriptor {
    metric_kind = "DELTA"
    value_type  = "DISTRIBUTION"
    unit        = "ms"
  }
  bucket_options {
    exponential_buckets {
      num_finite_buckets = 30
      growth_factor      = 1.4
      scale              = 10
    }
  }
  depends_on = [google_project_service.apis]
}

resource "google_logging_metric" "page_total_ms" {
  name = "page_total_ms"
  # The whole process_chunk() attempt for one page — classification,
  # the writer call, and the DB write that saves the result. Bigger than
  # writer_total_ms by whatever the DB round-trip and classification add
  # on top of the model call itself; the gap between the two metrics IS
  # the answer to "how much of a page's total latency is the model call
  # versus everything else," without needing a third measurement.
  filter          = "resource.type=\"cloud_run_revision\" jsonPayload.event=\"page_completed\" jsonPayload.duration_ms>0"
  value_extractor = "EXTRACT(jsonPayload.duration_ms)"
  metric_descriptor {
    metric_kind = "DELTA"
    value_type  = "DISTRIBUTION"
    unit        = "ms"
  }
  bucket_options {
    exponential_buckets {
      num_finite_buckets = 30
      growth_factor      = 1.4
      scale              = 10
    }
  }
  depends_on = [google_project_service.apis]
}

# Small on purpose — one chart, for the one number the README screenshot
# needs (p95 time-to-first-token). Not a general-purpose ops dashboard;
# add panels here only when there's a specific number to show, not
# speculatively.
resource "google_monitoring_dashboard" "writer_latency" {
  dashboard_json = jsonencode({
    displayName = "Plainly writer latency"
    gridLayout = {
      widgets = [
        {
          title = "Writer time-to-first-token — p95 / p99"
          xyChart = {
            dataSets = [
              {
                timeSeriesQuery = {
                  timeSeriesFilter = {
                    filter = "metric.type=\"logging.googleapis.com/user/writer_ttft_ms\" resource.type=\"cloud_run_revision\""
                    aggregation = {
                      alignmentPeriod  = "60s"
                      perSeriesAligner = "ALIGN_PERCENTILE_95"
                    }
                  }
                }
                plotType       = "LINE"
                legendTemplate = "p95"
              },
              {
                timeSeriesQuery = {
                  timeSeriesFilter = {
                    filter = "metric.type=\"logging.googleapis.com/user/writer_ttft_ms\" resource.type=\"cloud_run_revision\""
                    aggregation = {
                      alignmentPeriod  = "60s"
                      perSeriesAligner = "ALIGN_PERCENTILE_99"
                    }
                  }
                }
                plotType       = "LINE"
                legendTemplate = "p99"
              }
            ]
            yAxis = {
              label = "ms"
              scale = "LINEAR"
            }
          }
        },
        {
          title = "Page total latency — p95"
          xyChart = {
            dataSets = [
              {
                timeSeriesQuery = {
                  timeSeriesFilter = {
                    filter = "metric.type=\"logging.googleapis.com/user/page_total_ms\" resource.type=\"cloud_run_revision\""
                    aggregation = {
                      alignmentPeriod  = "60s"
                      perSeriesAligner = "ALIGN_PERCENTILE_95"
                    }
                  }
                }
                plotType       = "LINE"
                legendTemplate = "p95"
              }
            ]
            yAxis = {
              label = "ms"
              scale = "LINEAR"
            }
          }
        }
      ]
    }
  })
  depends_on = [google_project_service.apis]
}
