# System design (A → Z)

## 1) Problem statement

Given a `seller_id`, return a ranked list of products that the seller is likely to purchase. The system must be:
- **Fast**: low latency (cache hits should be near-instant)
- **Safe**: avoid leaking internal details; protect admin operations
- **Scalable**: avoid N+1 DB patterns; use batch queries
- **Observable**: metrics for latency, errors, cache effectiveness

## 2) High-level solution

This repo implements a single backend service:
- **FastAPI** application exposing recommendation APIs
- **PostgreSQL** as the source of truth
- **Redis** for caching computed recommendations
- **Prometheus + Grafana** for monitoring (optional locally, recommended for ops)

Core business flow:
1. Client calls `GET /api/recommend/products?seller_id=...`
2. API checks Redis cache for that seller
3. If cache miss, compute recommendations using a weighted algorithm over multiple signals
4. Store results in Redis (TTL) and return response

## 3) Context and boundaries

### In scope (this repo)
- Recommendation API and algorithm
- Data access layer (SQLAlchemy queries)
- Redis cache layer
- Metrics endpoint and dashboard provisioning assets

### Out of scope
- Frontend / Next.js UI (expected to call this service)
- Authentication of end users (only an admin API key is used for the cache-clear endpoint)

## 4) Main components and responsibilities

- **API layer** (`app/routers/recommendations.py`)
  - Validates request params
  - Cache lookup + writeback
  - Shapes response payloads
  - Protects admin endpoint with `X-API-Key` (when configured)

- **Recommendation engine** (`app/services/algorithm.py`)
  - Selects candidate products
  - Fetches signals in batch
  - Computes weighted score and ranking
  - Returns a list of recommendation dicts (with score breakdown)

- **Data service** (`app/services/data_service.py`)
  - Runs SQLAlchemy queries for:
    - global popularity
    - seller order history
    - category affinity
    - engagement (reactions + comments)
    - seller-scoped recency
    - product newness
    - catalog fallback candidates

- **Cache service** (`app/services/cache_service.py`)
  - Redis access with graceful degradation
  - Per-seller recommendations cache (TTL)
  - Cache invalidation endpoints used by admin

- **App wiring & middleware** (`app/main.py`)
  - App factory, CORS + trusted hosts
  - Metrics middleware (HTTP latency + count)
  - `/metrics` Prometheus endpoint
  - Lifespan hooks: DB connection test and engine disposal

## 5) Request lifecycle (what happens on `/products`)

### Input
- Query parameters:
  - `seller_id` (required)
  - `limit` (optional; bounded)

### Steps
1. Router checks Redis for `rec:products:<seller_id>`
2. If found: return cached list sliced to `limit`
3. If not found:
   - fetch candidates from popularity + seller history
   - pad candidates using catalog fallback if pool is thin
   - batch fetch signals for candidates
   - compute weighted score and rank
4. Store full computed list in Redis with TTL
5. Return response to client

### Output
JSON containing:
- `seller_id`
- `recommendations[]` each with `product_id`, `score`, `rank`, `sources`, `is_personalized`
- `count`, `cache_hit`, `elapsed_ms`

## 6) Non-functional requirements

### Performance
- Avoid N+1 queries: batch queries across candidate IDs
- Cache results per seller
- Candidate pool typically `limit*2` to improve ranking quality

### Reliability
- Redis failures are **non-fatal** (service still works without caching)
- DB connectivity is tested at startup; service continues but logs warnings on failure

### Security
- Admin cache-clear endpoint can be gated by `ADMIN_API_KEY` (`X-API-Key` header)
- Trusted host middleware reduces Host header injection risks
- CORS is configurable (not wildcard by default)

### Observability
- `/metrics` exports:
  - request counts + latency histogram
  - recommendation compute latency histogram
  - cache hit/miss counter

