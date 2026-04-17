"""
Prometheus metrics for the recommendation service.
"""

from prometheus_client import Counter, Histogram


HTTP_REQUESTS_TOTAL = Counter(
    "http_requests_total",
    "Total number of HTTP requests.",
    ["method", "path", "status_code"],
)

HTTP_REQUEST_DURATION_SECONDS = Histogram(
    "http_request_duration_seconds",
    "HTTP request latency in seconds.",
    ["method", "path"],
    buckets=(0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10),
)

RECOMMENDATION_COMPUTE_DURATION_SECONDS = Histogram(
    "recommendation_compute_duration_seconds",
    "Recommendation endpoint compute duration in seconds.",
    ["cache_hit"],
    buckets=(0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5),
)

RECOMMENDATION_CACHE_TOTAL = Counter(
    "recommendation_cache_total",
    "Recommendation cache outcomes.",
    ["result"],
)

