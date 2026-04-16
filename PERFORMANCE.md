"""
Performance Optimization & Deployment Guide

TARGET METRICS
==============

Response Time:        <200ms P99
Queries per request:  6 (stable, not 62+)
Cache hit rate:       >60% after warmup
Error rate:           <0.1%
Database CPU:         <30% at peak load
Memory usage:         <500MB per instance
Throughput:           >1000 req/sec per instance


DATABASE OPTIMIZATION
====================

Critical Indexes
----------------
These indexes must exist for v2 to perform well:

1. Products table:
   ```sql
   CREATE INDEX idx_products_created_at 
   ON products(created_at DESC);
   
   CREATE INDEX idx_products_is_active 
   ON products(is_active)
   WHERE is_active = true;
   ```

2. Order/Placed Order table:
   ```sql
   CREATE INDEX idx_orders_seller_created
   ON orders(seller_id, created_at DESC);
   
   CREATE INDEX idx_orders_product_created
   ON orders(product_id, created_at DESC);
   ```

3. Product Engagement (Reactions/Comments):
   ```sql
   CREATE INDEX idx_reactions_product_created
   ON reactions(product_id, created_at DESC);
   
   CREATE INDEX idx_comments_product_created  
   ON comments(product_id, created_at DESC);
   ```

4. Warehouse Stock:
   ```sql
   CREATE INDEX idx_warehouse_stock_available
   ON warehouse_stock(warehouse_id)
   WHERE quantity_available > 0;
   ```

Check existing indexes:
```sql
SELECT * FROM pg_indexes 
WHERE tablename IN ('products', 'orders', 'reactions', 'comments')
ORDER BY tablename, indexname;
```

Query Plan Analysis
-------------------
For each of the 6 main queries, verify the plan:

```sql
EXPLAIN ANALYZE
SELECT products.id, COUNT(orders.id) as order_count
FROM products
LEFT JOIN orders ON products.id = orders.product_id
GROUP BY products.id
ORDER BY order_count DESC
LIMIT 60;
```

Expected: Index scan, <5ms on fresh cache

If slow:
1. Add missing indexes
2. Analyze table: ANALYZE products;
3. Set work_mem higher: SET work_mem = '256MB';
4. Use LIMIT earlier in query


CONNECTION POOLING
==================

Current setup (check in config):
```
pool_size = 10
max_overflow = 20
pool_recycle = 3600
pool_pre_ping = True
```

For scale (50+ concurrent sellers):
```
pool_size = 20
max_overflow = 40
pool_recycle = 3600
pool_pre_ping = True
```

Monitor pool:
```python
from sqlalchemy import event
from sqlalchemy.pool import Pool

@event.listens_for(Pool, "checkout")
def receive_checkout(dbapi_conn, connection_record, connection_proxy):
    logger.debug(f"Pool checkout: {dbapi_conn}")
```


CACHING STRATEGY
================

Current: In-memory cache (Redis optional)
TTL: Configurable, default 1 hour

Warmup Strategy:
1. On startup, pre-compute top 100 sellers:
   ```python
   # In startup
   top_sellers = db.query(Seller).order_by(
       Seller.order_count.desc()
   ).limit(100).all()
   
   for seller in top_sellers:
       engine.compute_recommendations(seller.id, limit=30)
       # Automatically cached
   ```

2. Background refresh:
   ```python
   # Every 30 minutes, refresh top 500 sellers
   scheduler.add_job(
       refresh_top_seller_cache,
       'interval',
       minutes=30
   )
   ```

Eviction:
- Manual: DELETE cache keys for seller when they update preferences
- TTL: Cache expires after 1 hour (configurable)
- LRU: When memory > threshold, evict oldest entries


LOAD TESTING
============

Simulate production load:

```bash
# Test with 50 concurrent sellers, 100 total requests
python -m locust -f locustfile.py \
  --host=http://localhost:8000 \
  --users=50 \
  --spawn-rate=10 \
  --run-time=5m

# Expected:
# - Average response time: 50-150ms
# - Max response time: 200-500ms
# - Failures: 0
```

Locustfile example:
```python
# locustfile.py
from locust import HttpUser, task, between
import random

class RecommendationUser(HttpUser):
    wait_time = between(1, 3)
    
    @task
    def get_recommendations(self):
        seller_id = f"seller_{random.randint(1, 1000)}"
        limit = random.choice([20, 30, 50])
        self.client.get(
            f"/api/recommend/products",
            params={"seller_id": seller_id, "limit": limit}
        )
```

Stress testing (find breaking point):
```bash
# Gradually increase load until response time degrades
python -m locust -f locustfile.py \
  --headless \
  --users=1000 \
  --spawn-rate=100 \
  --run-time=10m \
  --csv=results/stress-test
```


MONITORING
==========

Key metrics to track:

1. Response Time:
   ```python
   # In FastAPI middleware
   @app.middleware("http")
   async def log_response_time(request, call_next):
       start = time.time()
       response = await call_next(request)
       duration = time.time() - start
       logger.info(f"{request.url.path}: {duration*1000:.1f}ms")
       return response
   ```

2. Database Queries:
   ```python
   # In SQLAlchemy events
   from sqlalchemy import event
   
   query_count = 0
   
   @event.listens_for(Engine, "before_cursor_execute")
   def receive_before_cursor_execute(conn, cursor, statement, params, context, executemany):
       global query_count
       query_count += 1
       logger.debug(f"Query #{query_count}: {statement[:100]}")
   ```

3. Cache Performance:
   ```python
   # Track cache hits/misses
   cache_stats = cache_service.get_stats()
   logger.info(f"Cache hit rate: {cache_stats['hit_rate']:.1%}")
   ```

Prometheus metrics:
```python
from prometheus_client import Counter, Histogram

recommendation_time = Histogram(
    'recommendation_request_seconds',
    'Time to compute recommendations',
    buckets=[0.05, 0.1, 0.2, 0.5, 1.0]
)

cache_hits = Counter(
    'cache_hits_total',
    'Total cache hits'
)

cache_misses = Counter(
    'cache_misses_total',
    'Total cache misses'
)
```


DEPLOYMENT
==========

Docker deployment:

```dockerfile
FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install -r requirements.txt

COPY app/ ./app/
COPY main.py .

EXPOSE 8000

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
```

Kubernetes deployment:

```yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: recommendation-engine
spec:
  replicas: 3
  selector:
    matchLabels:
      app: recommendation-engine
  template:
    metadata:
      labels:
        app: recommendation-engine
        version: v2
    spec:
      containers:
      - name: app
        image: maroc-affiliate/recommendation-engine:v2.0.0
        ports:
        - containerPort: 8000
        env:
        - name: DATABASE_URL
          valueFrom:
            secretKeyRef:
              name: database-creds
              key: url
        - name: REDIS_URL
          valueFrom:
            secretKeyRef:
              name: redis-creds
              key: url
        - name: LOG_LEVEL
          value: INFO
        resources:
          requests:
            cpu: 100m
            memory: 256Mi
          limits:
            cpu: 500m
            memory: 512Mi
        livenessProbe:
          httpGet:
            path: /api/status
            port: 8000
          initialDelaySeconds: 10
          periodSeconds: 10
        readinessProbe:
          httpGet:
            path: /api/recommend/health
            port: 8000
          initialDelaySeconds: 5
          periodSeconds: 5
---
apiVersion: v1
kind: Service
metadata:
  name: recommendation-engine
spec:
  selector:
    app: recommendation-engine
  ports:
  - port: 8000
    targetPort: 8000
  type: ClusterIP
```

Vercel/Cloud deployment:

```bash
# .env.production
DATABASE_URL=postgresql://...
REDIS_URL=redis://...
LOG_LEVEL=WARNING
ENVIRONMENT=production

# Deploy
vercel deploy --prod
```


MONITORING DASHBOARDS
====================

Suggested Grafana dashboard panels:

1. Request Rate (req/sec)
   ```
   rate(http_requests_total[1m])
   ```

2. Response Time (P50, P95, P99)
   ```
   histogram_quantile(0.99, recommendation_request_seconds)
   ```

3. Cache Hit Rate
   ```
   cache_hits_total / (cache_hits_total + cache_misses_total)
   ```

4. Database Queries per Request
   ```
   rate(db_queries_total[1m]) / rate(http_requests_total[1m])
   ```

5. Error Rate
   ```
   rate(http_errors_total[1m]) / rate(http_requests_total[1m])
   ```


SCALING STRATEGIES
==================

Horizontal scaling (add more instances):
1. Load balancer (Nginx/HAProxy) distributes traffic
2. Shared cache (Redis) across all instances
3. Database connection pooling

Vertical scaling (bigger instance):
1. More CPU cores (better parallel query execution)
2. More RAM (larger working set in cache)
3. Better I/O (NVMe SSD for database)

Recommended for <10k sellers:
- Single instance with local cache (current setup)

Recommended for 10k-100k sellers:
- 3-5 instances with Redis cache
- Read replica for database (SELECT queries only)

Recommended for >100k sellers:
- CDN caching layer
- Separate read/write databases
- Distributed cache (Redis cluster)
- Query caching at application level
"""
