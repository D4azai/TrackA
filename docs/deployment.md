# Deployment and operations

## Local (docker compose)

```bash
docker compose up -d --build
docker compose ps
docker compose logs -f recommendation-service
```

## Production considerations

### Configuration

Set these environment variables (see `.env.example`):
- `DATABASE_URL` (required)
- `REDIS_URL` (recommended)
- `ENVIRONMENT=production`
- `CORS_ORIGINS=[...]`
- `ALLOWED_HOSTS=[...]`
- `ADMIN_API_KEY=<strong-secret>` (recommended if exposing admin endpoint)

### Uvicorn/Gunicorn

For production, prefer multiple workers (depending on your CPU/memory budget). Example (conceptual):
- `uvicorn app.main:app --host 0.0.0.0 --port 8000 --workers 2`

### Database

- Ensure DB pool settings match your DB limits.
- Monitor DB latency and connection saturation.

### Redis

- Ensure Redis is reachable from the service.
- Size memory for your key volume and TTL.

### Monitoring

- Ensure Prometheus can scrape `/metrics`.
- Alert on 5xx rate, p95 latency, and cache hit ratio regressions.

## Rollback strategy

- The service is stateless; rollback is typically a container/image rollback.
- Redis cache can be invalidated via admin endpoint if needed.

