# Caching (Redis)

## What is cached

- **Per-seller recommendations**:
  - key: `rec:products:<seller_id>`
  - value: JSON list of recommendation dicts
  - TTL: `CACHE_TTL_SECONDS` (default 3600)

## Cache behavior

- Cache is checked first on `/products`.
- If cached value exists, the API slices it to `limit` without recomputing.
- If Redis is unavailable, caching is disabled but the service continues to compute recommendations.

## Invalidation

Admin endpoint `POST /api/recommend/cache/clear`:
- With `seller_id`: deletes seller keys
- Without `seller_id`: deletes all `rec:*` keys using `SCAN` (cursor-based)

## Operational notes

- Redis is configured in compose with max memory and `allkeys-lru`.
- Cache miss/hit is exposed via Prometheus metric `recommendation_cache_total{result="hit|miss"}`.

