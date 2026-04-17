# Recommendation Service

FastAPI service that returns personalized product recommendations for sellers.

## What this service does

- Exposes recommendation APIs under `/api/recommend`.
- Computes a weighted score per product using:
  - popularity
  - seller order history
  - seller-specific recency
  - product newness
  - engagement
- Caches recommendation responses in Redis for faster repeated reads.

## Tech stack

- Python 3.11
- FastAPI + Uvicorn
- SQLAlchemy + PostgreSQL
- Redis
- Docker + docker-compose
- pytest for tests

## Project structure

- `app/main.py`: app factory, middleware, startup/shutdown lifecycle.
- `app/routers/recommendations.py`: API routes and request/response schemas.
- `app/services/algorithm.py`: recommendation scoring logic.
- `app/services/data_service.py`: SQL query layer for scoring signals.
- `app/services/cache_service.py`: Redis cache operations.
- `app/db.py`: SQLAlchemy engine/session setup.
- `app/models.py`: ORM models.
- `tests/test_api_smoke.py`: smoke tests for key endpoints.

## API endpoints

- `GET /`: service metadata.
- `GET /api/recommend/products?seller_id=<id>&limit=<n>`: recommendations.
- `GET /api/recommend/health`: health endpoint.
- `POST /api/recommend/cache/clear`: clear cache (admin endpoint).
  - Protected by `X-API-Key` when `ADMIN_API_KEY` is configured.
  - In production, this endpoint is disabled unless `ADMIN_API_KEY` is set.

## Quick start

1. Install dependencies:

```bash
pip install -r requirements.txt
```

2. Create environment file:

```bash
cp .env.example .env
```

3. Start the service:

```bash
uvicorn app.main:app --host 0.0.0.0 --port 8000
```

4. Open docs:

- `http://localhost:8000/docs` (development mode)

## Running tests

```bash
pip install -r requirements-dev.txt
pytest -q
```

## Prometheus + Grafana

This project now exposes Prometheus metrics at `GET /metrics`.

To run app + Redis + Prometheus + Grafana:

```bash
docker compose up --build
```

Endpoints:
- App: `http://localhost:8000`
- Prometheus: `http://localhost:9090`
- Grafana: `http://localhost:3001` (`admin` / `admin`)

For full setup and dashboard queries, see `docs/PROJECT_WORKFLOW_NEXTJS_GRAFANA.md`.

## Environment variables

See `.env.example` for all supported settings.

Minimum required:

- `DATABASE_URL`

Strongly recommended for production:

- `ENVIRONMENT=production`
- `ADMIN_API_KEY=<strong-secret>`
- `CORS_ORIGINS=[...]`
- `ALLOWED_HOSTS=[...]`

## Notes

- The service does not include a frontend.
- Next.js should call this API over HTTP(S), typically via server-side routes/actions.
- For a full integration walkthrough and a Grafana rollout plan, read `docs/PROJECT_WORKFLOW_NEXTJS_GRAFANA.md`.
