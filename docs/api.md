# API contract

Base path: `/api/recommend`

## 1) `GET /products`

Returns a ranked list of recommended products for a seller.

### Query parameters
- `seller_id` (string, required)
- `limit` (int, optional, default 30, range 1..100)

### Response (200)
Shape (simplified):
- `seller_id`: string
- `recommendations`: array of:
  - `product_id`: int
  - `score`: number (0..100)
  - `rank`: int (1..N)
  - `is_personalized`: boolean
  - `sources`: per-signal breakdown (numbers)
- `count`: int
- `cache_hit`: boolean
- `personalized`: int
- `elapsed_ms`: number | null

### Errors
- `400`: validation errors (bad parameters)
- `503`: recommendation engine temporarily unavailable

## 2) `GET /health`

Health signal for load balancers and monitors.

### Response (200)
- `status`: `"healthy"`
- `service`: string
- `version`: string
- `cache`: `"ok" | "unavailable" | "unknown"`

## 3) `POST /cache/clear`

Invalidate cached recommendations.

### Query parameters
- `seller_id` (string, optional)
  - if present: clear only that seller cache
  - else: clear all keys under `rec:*`

### Auth
Uses header `X-API-Key` and compares to `ADMIN_API_KEY` (when configured).

### Responses
- `200`: success
- `401`: invalid/missing API key
- `503`: admin API key not configured (endpoint disabled)
- `500`: failure to clear cache

## 4) `GET /metrics`

Prometheus exposition format.

### Notes
- Not included in OpenAPI schema.
- Scraped by Prometheus (see `monitoring/prometheus.yml`).

