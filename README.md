# Recommendation Service (TrackA)

FastAPI microservice that returns **personalized product recommendations** for sellers.

- **API**: `GET /api/recommend/products?seller_id=<id>&limit=<n>`
- **Caching**: Redis (graceful деградация if Redis is down)
- **Observability**: Prometheus metrics at `GET /metrics` + optional Grafana dashboard

## Documentation (A → Z)

Start here:
- **Docs index**: `docs/README.md`
- **System design**: `docs/system-design.md`
- **Diagrams**: `docs/diagrams.md` (Mermaid)

## Quick start (Docker)

This is the recommended local setup (Postgres + Redis + API + Prometheus + Grafana):

```bash
docker compose up -d --build
```

Then open:
- **API**: `http://localhost:8000`
- **API docs** (dev only): `http://localhost:8000/docs`
- **Prometheus**: `http://localhost:9090`
- **Grafana**: `http://localhost:3001` (default `admin` / `admin`)

## Quick start (Python / local)

```bash
python3 -m venv .venv
. .venv/bin/activate
pip install -r requirements-dev.txt
cp .env.example .env
uvicorn app.main:app --host 0.0.0.0 --port 8000
```

## Key endpoints

- **Service info**: `GET /`
- **Recommendations**: `GET /api/recommend/products`
- **Health**: `GET /api/recommend/health`
- **Metrics**: `GET /metrics`
- **Admin cache clear**: `POST /api/recommend/cache/clear` (requires `X-API-Key` if `ADMIN_API_KEY` is set)

## Tests

```bash
pytest -q
```

## Configuration

See `.env.example`. Minimum required in real deployments:
- **`DATABASE_URL`**

Production recommendations:
- **`ENVIRONMENT=production`**
- **`ADMIN_API_KEY=<strong-secret>`**
- **`CORS_ORIGINS=[...]`**
- **`ALLOWED_HOSTS=[...]`**
