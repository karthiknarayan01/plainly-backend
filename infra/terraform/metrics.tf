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
# log() calls exactly — there is no schema enforcement between a Python
# log() call and a Terraform filter string, so a renamed field in one
# place silently breaks the corresponding metric in the other.
#
# 2026-09-21: what matters here is time-to-first-token specifically, not
# total completion time — and TTFT isn't one number, it's the sum of
# several components a reader's wait is actually made of: how long a
# page sat queued before a worker claimed it, how long the cheap
# in-code classifier took, how long the rate limiter made the call
# wait, and how long the model/network itself took to produce a first
# token. Each is logged and metered separately (not just the total) so
# a slow page can be attributed to a specific cause — see
# services/worker/main.py's process_chunk() for where each is measured,
# and its ttft_since_claim_ms for the one number that sums them all.

resource "google_logging_metric" "page_claim_wait_ms" {
  name = "page_claim_wait_ms"
  # Real queueing delay: time between a chunk being created and a
  # worker lane actually claiming it. Near-zero when workers keep up
  # with incoming volume; grows under real load when all
  # CONCURRENT_WORKERS lanes are busy — the component most likely to
  # explain a slow page that ISN'T the model's fault at all.
  filter          = "resource.type=\"cloud_run_revision\" jsonPayload.event=\"page_completed\" jsonPayload.claim_wait_ms>=0"
  value_extractor = "EXTRACT(jsonPayload.claim_wait_ms)"
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

resource "google_logging_metric" "page_classify_ms" {
  name = "page_classify_ms"
  # classify_page_type()'s own cost — a pure in-code heuristic, expected
  # to sit near zero. Metered anyway rather than assumed negligible, so
  # a future change to the classifier that accidentally makes it
  # expensive (e.g. swapping the heuristic for a model call) would show
  # up here immediately, not get discovered later as an unexplained
  # regression in a metric two steps removed from the actual cause.
  filter          = "resource.type=\"cloud_run_revision\" jsonPayload.event=\"page_completed\" jsonPayload.classify_ms>=0"
  value_extractor = "EXTRACT(jsonPayload.classify_ms)"
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

resource "google_logging_metric" "page_throttle_wait_ms" {
  name = "page_throttle_wait_ms"
  # Time spent inside llm.py's rate-limit token bucket before the
  # request was even sent — a hard ceiling (WRITER_REQUESTS_PER_MINUTE)
  # independent of how fast the model itself responds. If this is the
  # dominant component under real load, the fix is raising the rate
  # limit or adding worker capacity, not touching the model or prompt.
  filter          = "resource.type=\"cloud_run_revision\" jsonPayload.event=\"page_completed\" jsonPayload.throttle_wait_ms>=0"
  value_extractor = "EXTRACT(jsonPayload.throttle_wait_ms)"
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

resource "google_logging_metric" "writer_ttft_ms" {
  name = "writer_ttft_ms"
  # The model/network component only: time from dispatching the request
  # to the first streamed token, once the call has actually been sent
  # (i.e. after throttling). Distinct from writer_total_ms below: a slow
  # provider route can have a fast first token and a slow tail, or vice
  # versa, and only measuring one hides which failure mode is
  # happening. This is a per-attempt metric (sourced from llm_call_end,
  # not page_completed) — see page_ttft_since_claim_ms for the
  # per-page, full-pipeline equivalent that's the actual headline number.
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

resource "google_logging_metric" "page_ttft_since_claim_ms" {
  name = "page_ttft_since_claim_ms"
  # THE headline number: claim_wait_ms + classify_ms + throttle_wait_ms
  # + model ttft_ms, computed once in main.py and logged as a single
  # field — the real, full time a reader waited from this page becoming
  # available to work on, to its first visible output. This is what the
  # README's p95 screenshot measures; the four metrics above are its
  # components, for explaining *why* this number is what it is.
  filter          = "resource.type=\"cloud_run_revision\" jsonPayload.event=\"page_completed\" jsonPayload.ttft_since_claim_ms>0"
  value_extractor = "EXTRACT(jsonPayload.ttft_since_claim_ms)"
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
  # The whole process_chunk() attempt for one page, through to the DB
  # write that saves the result — i.e. page_ttft_since_claim_ms PLUS the
  # rest of the model's generation (total_ms - ttft_ms) PLUS the DB
  # round-trip. Not the headline number (a reader sees the page as soon
  # as the whole document finishes, not the first token specifically),
  # but useful for capacity planning — see eval/observability/README.md.
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

# One chart per number that matters, nothing speculative: the headline
# p95/p99 TTFT (what the README screenshot uses), then its components
# side by side at p50 so a bottleneck is visible at a glance, then total
# page time for capacity-planning context.
resource "google_monitoring_dashboard" "writer_latency" {
  dashboard_json = jsonencode({
    displayName = "Plainly writer latency"
    gridLayout = {
      widgets = [
        {
          title = "Time-to-first-token, full pipeline — p95 / p99"
          xyChart = {
            dataSets = [
              {
                timeSeriesQuery = {
                  timeSeriesFilter = {
                    filter = "metric.type=\"logging.googleapis.com/user/page_ttft_since_claim_ms\" resource.type=\"cloud_run_revision\""
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
                    filter = "metric.type=\"logging.googleapis.com/user/page_ttft_since_claim_ms\" resource.type=\"cloud_run_revision\""
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
          title = "TTFT components — median (p50), which one dominates"
          xyChart = {
            dataSets = [
              {
                timeSeriesQuery = {
                  timeSeriesFilter = {
                    filter      = "metric.type=\"logging.googleapis.com/user/page_claim_wait_ms\" resource.type=\"cloud_run_revision\""
                    aggregation = { alignmentPeriod = "60s", perSeriesAligner = "ALIGN_PERCENTILE_50" }
                  }
                }
                plotType       = "LINE"
                legendTemplate = "queue wait"
              },
              {
                timeSeriesQuery = {
                  timeSeriesFilter = {
                    filter      = "metric.type=\"logging.googleapis.com/user/page_classify_ms\" resource.type=\"cloud_run_revision\""
                    aggregation = { alignmentPeriod = "60s", perSeriesAligner = "ALIGN_PERCENTILE_50" }
                  }
                }
                plotType       = "LINE"
                legendTemplate = "classify"
              },
              {
                timeSeriesQuery = {
                  timeSeriesFilter = {
                    filter      = "metric.type=\"logging.googleapis.com/user/page_throttle_wait_ms\" resource.type=\"cloud_run_revision\""
                    aggregation = { alignmentPeriod = "60s", perSeriesAligner = "ALIGN_PERCENTILE_50" }
                  }
                }
                plotType       = "LINE"
                legendTemplate = "rate-limit throttle"
              },
              {
                timeSeriesQuery = {
                  timeSeriesFilter = {
                    filter      = "metric.type=\"logging.googleapis.com/user/writer_ttft_ms\" resource.type=\"cloud_run_revision\""
                    aggregation = { alignmentPeriod = "60s", perSeriesAligner = "ALIGN_PERCENTILE_50" }
                  }
                }
                plotType       = "LINE"
                legendTemplate = "model + network"
              }
            ]
            yAxis = {
              label = "ms"
              scale = "LINEAR"
            }
          }
        },
        {
          title = "Page total latency (through DB save) — p95"
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
