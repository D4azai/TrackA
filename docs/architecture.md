# Architecture

## Overview

The system is a **single service** that exposes HTTP endpoints to compute and serve recommendations.

- **Runtime**: FastAPI + Uvicorn
- **Persistence**: PostgreSQL (primary data)
- **Cache**: Redis (per-seller recommendation list)
- **Monitoring**: Prometheus scrapes `/metrics`, Grafana dashboards visualize it

## Folder map

- `app/main.py`: app factory + middleware + lifespan
- `app/routers/recommendations.py`: API endpoints and response models
- `app/services/algorithm.py`: recommendation scoring and ranking
- `app/services/data_service.py`: SQLAlchemy queries for signals
- `app/services/cache_service.py`: Redis caching + invalidation
- `app/models.py`: SQLAlchemy ORM models used by queries
- `app/db.py`: engine/session and DB health checks
- `app/metrics.py`: Prometheus metric definitions
- `monitoring/`: Prometheus config + Grafana provisioning (datasource + dashboards)

## Data flow

1. Client calls `/api/recommend/products`
2. API checks Redis key `rec:products:<seller_id>`
3. Cache miss triggers DB queries to compute scores
4. Result list is cached in Redis and returned
5. Prometheus scrapes `/metrics` to record:
   - request rates and latencies
   - compute latency
   - cache hit/miss

## Environments

### Development
- Docs endpoints enabled (`/docs`, `/openapi.json`)
- `docker compose up -d --build` brings up Postgres + Redis + API + monitoring

### Production
- Disable docs endpoints by setting `ENVIRONMENT=production`
- Set `ADMIN_API_KEY` to protect cache-clear endpoint
- Set strict `CORS_ORIGINS` and `ALLOWED_HOSTS`
- Run behind a reverse proxy / gateway if needed (TLS termination, rate limiting)

