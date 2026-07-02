from prometheus_client import Counter, Histogram, Gauge, generate_latest, CONTENT_TYPE_LATEST

# Route decision metrics
route_decisions_total = Counter(
    "router_route_decisions_total",
    "Total routing decisions by tier",
    ["tier", "method"]
)

fallback_count_total = Counter(
    "router_fallback_count_total",
    "Total fallback events (classifier timeout/failure)",
    ["reason"]
)

upstream_errors_total = Counter(
    "router_upstream_errors_total",
    "Total upstream errors",
    ["tier", "error_type"]
)

upstream_latency_seconds = Histogram(
    "router_upstream_latency_seconds",
    "Upstream request latency",
    ["tier"],
    buckets=[0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0, 30.0, 60.0]
)

time_to_first_token_seconds = Histogram(
    "router_time_to_first_token_seconds",
    "Time to first token (streaming)",
    ["tier"],
    buckets=[0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0]
)

tokens_streamed_total = Counter(
    "router_tokens_streamed_total",
    "Total tokens streamed",
    ["tier"]
)

router_overhead_seconds = Histogram(
    "router_overhead_seconds",
    "Router processing overhead (routing decision time)",
    ["method"],
    buckets=[0.001, 0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5]
)

active_requests = Gauge(
    "router_active_requests",
    "Number of active requests"
)

recent_requests = Gauge(
    "router_recent_requests",
    "Number of requests started in the last 60 seconds"
)

recent_requests_by_tier = Gauge(
    "router_recent_requests_by_tier",
    "Number of requests started in the last 60 seconds by tier",
    ["tier"]
)

canary_routing_total = Counter(
    "router_canary_routing_total",
    "Total canary routing decisions",
    ["tier", "target"]
)
