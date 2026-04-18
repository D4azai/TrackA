# Observability (metrics + dashboards)

## Metrics endpoint

- `GET /metrics` exposes Prometheus exposition format.

## Metrics emitted

### HTTP metrics
- `http_requests_total{method,path,status_code}`
- `http_request_duration_seconds{method,path}`

### Recommendation metrics
- `recommendation_compute_duration_seconds{cache_hit}`
- `recommendation_cache_total{result="hit|miss"}`

## Prometheus

- Config: `monitoring/prometheus.yml`
- Scrapes the API service at its internal compose network address.

## Grafana

Provisioned assets:
- Datasource: `monitoring/grafana/provisioning/datasources/prometheus.yml`
- Dashboard provider: `monitoring/grafana/provisioning/dashboards/dashboards.yml`
- Dashboard JSON: `monitoring/grafana/dashboards/recommendation-overview.json`

Default local endpoint:
- `http://localhost:3001` (`admin` / `admin`)

