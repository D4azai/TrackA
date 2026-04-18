# Security checklist

## API surface

- Public endpoints:
  - `GET /api/recommend/products`
  - `GET /api/recommend/health`
  - `GET /metrics` (typically internal-only in production)

## Admin endpoint protection

- `POST /api/recommend/cache/clear`
  - Protected by `X-API-Key` when `ADMIN_API_KEY` is configured.
  - If `ADMIN_API_KEY` is not configured, the endpoint returns `503` (disabled).

## CORS and hosts

- Configure `CORS_ORIGINS` to your trusted frontend domains (avoid `*`).
- Configure `ALLOWED_HOSTS` to your deployed hostnames.

## Secrets management

- Never commit `.env`.
- Store `DATABASE_URL` and `ADMIN_API_KEY` in:
  - container secrets / vault, or
  - CI/CD secret store, or
  - orchestration secrets (Kubernetes secrets, etc.)

## Network

- Put the service behind TLS termination (reverse proxy / gateway).
- Restrict `/metrics` to internal networks if exposed.

