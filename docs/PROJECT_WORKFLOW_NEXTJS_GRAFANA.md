# How This Project Works

This document explains how the recommendation service works internally, how it sends recommendations to a Next.js app, and how to add Grafana monitoring.

## 1) End-to-end flow

1. Your Next.js app sends an HTTP request to:
   - `GET /api/recommend/products?seller_id=<seller-id>&limit=<n>`
2. FastAPI receives the request in `app/routers/recommendations.py`.
3. The route checks Redis cache first (`CacheService.get_recommendations`).
4. If cache miss, `RecommendationEngine.compute_recommendations` runs:
   - loads candidate products from popularity + seller history,
   - computes engagement, recency, and newness in batch queries,
   - applies weighted scoring and ranking.
5. The service returns JSON with:
   - `seller_id`
   - `recommendations` (each with `product_id`, `score`, `rank`, `sources`)
   - `count`, `cache_hit`, `elapsed_ms`
6. Next.js receives the JSON and renders recommendation cards/lists.

## 2) Internal components

- `app/main.py`
  - Creates FastAPI app, middleware, route prefix `/api/recommend`.
- `app/routers/recommendations.py`
  - Public endpoints: products + health.
  - Admin endpoint: cache clear (API key protected).
- `app/services/algorithm.py`
  - Core ranking strategy and signal weighting.
- `app/services/data_service.py`
  - SQL queries that fetch signal inputs for the model.
- `app/services/cache_service.py`
  - Redis caching and invalidation.
- `app/db.py` and `app/models.py`
  - SQLAlchemy session/engine and data models.

## 3) How Next.js should call this API

Best practice is to call this backend from Next.js server-side code (Route Handlers or Server Actions), then return/stream UI-safe data to the client.

### Example Next.js Route Handler

```ts
// app/api/recommendations/route.ts
import { NextRequest, NextResponse } from "next/server";

export async function GET(req: NextRequest) {
  const sellerId = req.nextUrl.searchParams.get("seller_id");
  const limit = req.nextUrl.searchParams.get("limit") ?? "20";

  if (!sellerId) {
    return NextResponse.json({ error: "seller_id is required" }, { status: 400 });
  }

  const apiBase = process.env.RECOMMENDATION_API_URL;
  const upstream = await fetch(
    `${apiBase}/api/recommend/products?seller_id=${encodeURIComponent(sellerId)}&limit=${limit}`,
    { cache: "no-store" }
  );

  const payload = await upstream.json();
  return NextResponse.json(payload, { status: upstream.status });
}
```

### Why server-side fetch is preferred

- Hides internal backend URL from browser clients.
- Allows auth/session checks before requesting recommendations.
- Centralizes retries, timeouts, and fallback behavior.

## 4) Current API security behavior

- `POST /api/recommend/cache/clear` now uses `X-API-Key`.
- It validates against `ADMIN_API_KEY`.
- In production, if `ADMIN_API_KEY` is missing, the endpoint is disabled.

## 5) Grafana setup (implemented + runbook)

The codebase now includes Prometheus metrics and local Grafana/Prometheus services.

Implemented:
- FastAPI metrics endpoint: `GET /metrics` (in `app/main.py`).
- HTTP metrics:
  - `http_requests_total{method,path,status_code}`
  - `http_request_duration_seconds{method,path}`
- Recommendation metrics:
  - `recommendation_compute_duration_seconds{cache_hit}`
  - `recommendation_cache_total{result="hit|miss"}`
- Prometheus config: `monitoring/prometheus.yml`
- Docker services in `docker-compose.yml`:
  - `prometheus` on `http://localhost:9090`
  - `grafana` on `http://localhost:3001`

### Run locally

```bash
docker compose up --build
```

Then open:
- API metrics: `http://localhost:8000/metrics`
- Prometheus: `http://localhost:9090`
- Grafana: `http://localhost:3001` (default `admin` / `admin`)

The repository also auto-provisions:
- Prometheus datasource (`monitoring/grafana/provisioning/datasources/prometheus.yml`)
- Dashboard provider (`monitoring/grafana/provisioning/dashboards/dashboards.yml`)
- Ready dashboard (`monitoring/grafana/dashboards/recommendation-overview.json`)

### Connect Grafana to Prometheus

In Grafana:
1. Go to **Connections** -> **Data sources** -> **Add data source**.
2. Choose **Prometheus**.
3. URL:
   - If Grafana runs via this compose file: `http://prometheus:9090`
   - If Grafana runs outside compose: `http://localhost:9090`
4. Save & test.

### Starter dashboard queries

- Request rate (RPS):
  - `sum(rate(http_requests_total[5m]))`
- 5xx error rate:
  - `sum(rate(http_requests_total{status_code=~"5.."}[5m]))`
- P95 API latency:
  - `histogram_quantile(0.95, sum(rate(http_request_duration_seconds_bucket[5m])) by (le, path))`
- Recommendation compute P95:
  - `histogram_quantile(0.95, sum(rate(recommendation_compute_duration_seconds_bucket[5m])) by (le, cache_hit))`
- Cache hit ratio:
  - `sum(rate(recommendation_cache_total{result="hit"}[5m])) / sum(rate(recommendation_cache_total[5m]))`

## 6) Metrics naming used

- `http_requests_total`
- `http_request_duration_seconds`
- `recommendation_compute_duration_seconds`
- `recommendation_cache_total` (label `result=hit|miss`)

## 7) Deployment checklist for Next.js + API

- Set `RECOMMENDATION_API_URL` in Next.js env.
- Configure backend `CORS_ORIGINS` to include your Next.js domain(s).
- Set backend `ALLOWED_HOSTS` for your deployed hostnames.
- Set `ADMIN_API_KEY` in production.
- Ensure Redis and PostgreSQL are reachable from backend runtime.
- Add monitoring (Prometheus/Grafana) before scaling traffic.
