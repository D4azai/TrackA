# Platform Integration Guide

How the recommendation service fits into your main platform's event flow.

---

## Architecture Overview

```
Main Platform                    Recommendation Service
─────────────────                ────────────────────────────────────────
Checkout Service  ──POST event─► /api/recommend/events/order-placed
Catalog Service   ──POST event─► /api/recommend/events/product-updated
Engagement Svc    ──POST event─► /api/recommend/events/product-engaged
External Cron     ──POST─────►  /api/recommend/refresh/active   (scheduled)
                                       │
                                       ▼
                               RecommendationRefreshJob (Postgres queue)
                                       │
                               Refresh Worker (polling)
                                       │
                               RecommendationEngine.compute_recommendations()
                                       │
                               ┌───────┴──────────┐
                               SellerRecommendation  Redis hot cache
                               (Postgres snapshot)   (TTL = freshness)
                                       │
Next.js ◄──GET /api/recommend/products?seller_id=X
```

---

## Trigger Sequences

### 1. Order Placement

**When:** Immediately after the order status changes to `CONFIRMED` or `COMPLETED`.

```
1. Checkout service processes payment
2. Order status → CONFIRMED
3. Checkout service calls:
   POST /api/recommend/events/order-placed
   {"seller_id": "<seller>", "order_id": <id>}
4. RecommendationRefreshJob created with trigger="order_placed", priority=150
5. Worker picks it up within POLL_INTERVAL_SECONDS (default: 30s)
6. New snapshot stored in SellerRecommendation
7. Redis cache warmed (TTL = PRECOMPUTED_FRESHNESS_SECONDS)
8. Next GET /products request returns fresh personalized results
```

**Timing rationale:** Order data changes the seller's history signal. A 30s delay is acceptable — the seller won't see updated recommendations mid-checkout. The stale snapshot is served until the worker completes.

**Idempotency:** If the order service fires twice (retry/webhook dedup failure), the second call returns `already_queued` — no duplicate job is created.

---

### 2. Product Create / Update

**When:** After a product is saved as `AVAILABLE` or its price/category changes.

```
1. Catalog service saves product
2. Catalog service calls:
   POST /api/recommend/events/product-updated
   {"product_id": <id>, "requested_by": "catalog-service"}
3. Enqueues refresh for ALL active sellers (up to REFRESH_ACTIVE_SELLERS_LIMIT)
4. Worker drains queue over the next few minutes
5. All seller snapshots updated with the new product scored
```

**Avoid for every minor edit:** If your catalog service saves frequently (autosave drafts), gate this call behind a `status == AVAILABLE` check to prevent flooding the queue.

---

### 3. Product Engagement (Like / Comment)

**When:** After a user reaction is saved.

```
1. User likes a product on seller X's storefront
2. Engagement service calls:
   POST /api/recommend/events/product-engaged
   {"seller_id": "X", "product_id": <id>, "event_type": "liked"}
3. Single seller job queued with priority=150
4. Worker processes → seller's engagement signal updated in snapshot
```

**Optional:** Engagement is a weak signal (5% weight). You can batch these or debounce them — e.g., only fire once per seller per 10 minutes — without meaningfully hurting recommendation quality.

---

### 4. Seller Onboarding

**When:** A new seller activates their account.

```
1. User service creates seller account
2. Onboarding service calls:
   POST /api/recommend/refresh/seller
   {"seller_id": "<new-seller>", "requested_by": "onboarding"}
3. Worker runs → popularity-only snapshot (no history yet)
4. New seller immediately gets global trending recommendations
5. As they place orders, subsequent refreshes personalise results
```

---

### 5. Scheduled Full Re-index

**When:** Every 30 minutes via cron or the scheduler service.

```
1. Scheduler (APScheduler or external cron) calls:
   POST /api/recommend/refresh/active
   {"requested_by": "scheduler"}
2. Enqueues all sellers with orders in last 30 days
3. Worker drains queue over the next ~5 minutes
4. All seller snapshots refreshed before hitting PRECOMPUTED_FRESHNESS_SECONDS (1800s)
```

**Why 30 min / 1800s freshness:** A 30-minute re-index cycle keeps snapshots within the freshness window. Adjust `ENQUEUE_INTERVAL_MINUTES` and `PRECOMPUTED_FRESHNESS_SECONDS` together — they should satisfy: `ENQUEUE_INTERVAL_MINUTES * 60 < PRECOMPUTED_FRESHNESS_SECONDS`.

---

## Deployment Sequence (First Deploy)

```bash
# 1. Apply the new Postgres tables (one-time migration)
psql $DATABASE_URL -f docs/recommendation-storage.sql

# 2. Start the API
docker compose up -d recommendation-service

# 3. Verify health
curl http://localhost:8000/api/recommend/health

# 4. Start the worker and scheduler
docker compose up -d worker scheduler

# 5. Seed initial jobs for all active sellers
curl -s -X POST http://localhost:8000/api/recommend/refresh/active \
  -H "X-API-Key: $ADMIN_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"requested_by": "initial-seed"}'

# 6. Monitor job processing
docker compose logs -f worker

# 7. Verify snapshots
psql $DATABASE_URL -c \
  'SELECT "sellerId", COUNT(*) AS recs, MAX("computedAt") AS last_computed
   FROM "SellerRecommendation" GROUP BY "sellerId" ORDER BY last_computed DESC LIMIT 10;'
```

---

## Environment Variables Checklist

| Variable | Required | Description |
|----------|----------|-------------|
| `DATABASE_URL` | ✅ | Postgres connection string |
| `REDIS_URL` | ✅ | Redis connection string |
| `ADMIN_API_KEY` | ✅ | Secret for all event + admin endpoints |
| `ENVIRONMENT` | ✅ | `development` / `staging` / `production` |
| `PRECOMPUTED_FRESHNESS_SECONDS` | recommended | Default 1800 (30 min) |
| `SERVE_STALE_PRECOMPUTED` | recommended | Default `true` — serve stale while refreshing |
| `SYNC_RECOMPUTE_FALLBACK_ENABLED` | optional | `false` in production — use worker path only |
| `REFRESH_MAX_ATTEMPTS` | optional | Default 3 — retries before permanent FAILED |
| `POLL_INTERVAL_SECONDS` | optional | Default 30 — worker poll frequency |
| `WORKER_BATCH_SIZE` | optional | Default 10 — jobs per worker cycle |

---

## Monitoring

Key Prometheus metrics to alert on:

| Metric | Alert condition | Meaning |
|--------|----------------|---------|
| `recommendation_response_source_total{source="queued_empty"}` | rate > 0 sustained | No snapshot available — worker may be stuck |
| `recommendation_precomputed_total{result="stale"}` | rate > 0 sustained | Snapshots aging past freshness threshold |
| `recommendation_refresh_jobs_total{status="failed"}` | rate > 0 | Job failures (check `lastError` column in DB) |
| `recommendation_refresh_duration_seconds` | p99 > 5s | Slow recomputes — check DB query performance |

Access Grafana at `http://localhost:3001` (admin/admin) after `docker compose up`.
