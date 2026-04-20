# Event Publisher Examples

All event endpoints are secured with the `X-API-Key` header (`ADMIN_API_KEY` in your `.env`).
Responses follow the same schema — `status`, `seller_id`, `job_id`, `created`.

---

## 1. Order Placed

Call this **after a checkout is confirmed** in your main platform (order service, webhook handler, etc.).  
The recommendation engine will queue a high-priority refresh for that seller.

### curl
```bash
curl -s -X POST https://reco.example.com/api/recommend/events/order-placed \
  -H "X-API-Key: $ADMIN_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "seller_id": "seller-abc123",
    "order_id": 98765,
    "requested_by": "checkout-service"
  }' | jq
```

### Python (httpx)
```python
import httpx

RECO_URL = "https://reco.example.com"
API_KEY  = "your-admin-api-key"

def notify_order_placed(seller_id: str, order_id: int) -> dict:
    resp = httpx.post(
        f"{RECO_URL}/api/recommend/events/order-placed",
        headers={"X-API-Key": API_KEY},
        json={"seller_id": seller_id, "order_id": order_id, "requested_by": "checkout-service"},
        timeout=5.0,
    )
    resp.raise_for_status()
    return resp.json()
```

### Next.js / TypeScript
```ts
// lib/recoClient.ts
const RECO_URL = process.env.RECO_SERVICE_URL!;
const API_KEY  = process.env.RECO_ADMIN_API_KEY!;

export async function notifyOrderPlaced(sellerId: string, orderId: number) {
  const res = await fetch(`${RECO_URL}/api/recommend/events/order-placed`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      "X-API-Key": API_KEY,
    },
    body: JSON.stringify({ seller_id: sellerId, order_id: orderId, requested_by: "next-checkout" }),
  });
  if (!res.ok) throw new Error(`Reco event failed: ${res.status}`);
  return res.json();
}
```

**Expected response:**
```json
{
  "status": "queued",
  "seller_id": "seller-abc123",
  "job_id": 42,
  "created": true
}
```
If a job is already pending for this seller, `status` is `"already_queued"` and `created` is `false` — this is idempotent by design.

---

## 2. Product Engaged (Like / Comment)

Call after a user likes or comments on a product in the seller's catalog.

### curl
```bash
curl -s -X POST https://reco.example.com/api/recommend/events/product-engaged \
  -H "X-API-Key: $ADMIN_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "seller_id": "seller-abc123",
    "product_id": 7890,
    "event_type": "liked",
    "requested_by": "engagement-service"
  }' | jq
```

### Python (httpx)
```python
def notify_product_engaged(seller_id: str, product_id: int, event_type: str = "liked") -> dict:
    resp = httpx.post(
        f"{RECO_URL}/api/recommend/events/product-engaged",
        headers={"X-API-Key": API_KEY},
        json={
            "seller_id": seller_id,
            "product_id": product_id,
            "event_type": event_type,
            "requested_by": "engagement-service",
        },
        timeout=5.0,
    )
    resp.raise_for_status()
    return resp.json()
```

---

## 3. Product Updated (Catalog Change)

Call after a product is **created, updated, or re-activated**.  
This queues a refresh for ALL active sellers (up to `REFRESH_ACTIVE_SELLERS_LIMIT`).

### curl
```bash
curl -s -X POST https://reco.example.com/api/recommend/events/product-updated \
  -H "X-API-Key: $ADMIN_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "product_id": 7890,
    "requested_by": "catalog-service",
    "seller_limit": 200
  }' | jq
```

### Python (httpx)
```python
def notify_product_updated(product_id: int, seller_limit: int | None = None) -> dict:
    resp = httpx.post(
        f"{RECO_URL}/api/recommend/events/product-updated",
        headers={"X-API-Key": API_KEY},
        json={"product_id": product_id, "requested_by": "catalog-service", "seller_limit": seller_limit},
        timeout=10.0,
    )
    resp.raise_for_status()
    return resp.json()
```

**Expected response:**
```json
{
  "status": "queued",
  "trigger": "product_updated",
  "queued": 48,
  "already_queued": 3
}
```

---

## 4. Manual Admin Refresh (Single Seller)

Force an immediate re-index for one seller — useful after data corrections or seller onboarding.

### curl
```bash
curl -s -X POST https://reco.example.com/api/recommend/refresh/seller \
  -H "X-API-Key: $ADMIN_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"seller_id": "seller-abc123", "requested_by": "ops-team"}' | jq
```

---

## 5. Trigger the Worker via HTTP (Cron Alternative)

If you prefer cron-HTTP over an always-on worker:

```bash
# In your crontab — drain up to 20 jobs every 5 minutes
*/5 * * * * curl -sf -X POST "https://reco.example.com/api/recommend/jobs/run?limit=20" \
  -H "X-API-Key: $ADMIN_API_KEY"

# Enqueue active sellers every 30 minutes
*/30 * * * * curl -sf -X POST "https://reco.example.com/api/recommend/refresh/active" \
  -H "X-API-Key: $ADMIN_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"requested_by": "cron"}'
```

---

## Error Handling Guidance

| Status | Meaning | What to do |
|--------|---------|-----------|
| `200` | Job queued or already pending | Nothing — idempotent |
| `401` | Missing or wrong `X-API-Key` | Check `ADMIN_API_KEY` env var |
| `422` | Invalid request body | Fix payload (see Pydantic validation detail) |
| `503` | Recommendation engine unavailable | Retry with backoff; check DB/Redis health |

> **Fire-and-forget is fine.** These endpoints are non-blocking — they only write a row to `RecommendationRefreshJob`.
> The actual recomputation happens in the worker process. Your checkout service should not wait for recommendations to be ready.
