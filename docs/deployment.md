# Deployment and Operations

## Local — Docker Compose

```bash
# Full stack (API + worker + scheduler + Postgres + Redis + Prometheus + Grafana)
docker compose up -d --build

# Status and logs
docker compose ps
docker compose logs -f recommendation-service
docker compose logs -f worker
docker compose logs -f scheduler

# One-off worker run (useful for CI or manual re-index)
docker compose run --rm worker python -m worker.refresh_worker --once
```

### First-deploy migration

Apply the durable storage tables once before starting the worker:
```bash
psql $DATABASE_URL -f docs/recommendation-storage.sql
```

---

## Production Considerations

### Configuration

Set these environment variables (see `.env.example`):

| Variable | Required | Notes |
|----------|----------|-------|
| `DATABASE_URL` | ✅ | Postgres connection string |
| `REDIS_URL` | ✅ | Redis connection string |
| `ENVIRONMENT` | ✅ | Set to `production` |
| `ADMIN_API_KEY` | ✅ | Strong secret — used for all admin/event endpoints |
| `CORS_ORIGINS` | ✅ | JSON list of allowed origins |
| `ALLOWED_HOSTS` | ✅ | JSON list of allowed Host headers |
| `SYNC_RECOMPUTE_FALLBACK_ENABLED` | recommended | Set `false` in production — rely on worker path |
| `SERVE_STALE_PRECOMPUTED` | recommended | Keep `true` — avoids empty responses during refresh |

### API Server

```bash
# Multi-worker uvicorn (scale to CPU count)
uvicorn app.main:app --host 0.0.0.0 --port 8000 --workers 4
```

---

## Kubernetes CronJob (Scheduled Refresh)

Use this **instead of** the scheduler container if you prefer Kubernetes-native scheduling:

```yaml
# k8s/reco-refresh-cronjob.yaml
apiVersion: batch/v1
kind: CronJob
metadata:
  name: reco-enqueue-active
  namespace: recommendation
spec:
  schedule: "*/30 * * * *"          # Every 30 minutes
  concurrencyPolicy: Forbid          # Never run two at once
  successfulJobsHistoryLimit: 3
  failedJobsHistoryLimit: 3
  jobTemplate:
    spec:
      template:
        spec:
          restartPolicy: OnFailure
          containers:
            - name: enqueue
              image: your-registry/recommendation-service:latest
              command:
                - python
                - -m
                - worker.refresh_worker
                - --once
                - --batch-size
                - "500"              # Large batch for scheduled run
              envFrom:
                - secretRef:
                    name: reco-secrets   # DATABASE_URL, REDIS_URL, ADMIN_API_KEY
              resources:
                requests:
                  memory: "256Mi"
                  cpu: "100m"
                limits:
                  memory: "512Mi"
                  cpu: "500m"
---
# Always-on worker deployment (drains jobs queued by events + cron)
apiVersion: apps/v1
kind: Deployment
metadata:
  name: reco-worker
  namespace: recommendation
spec:
  replicas: 1              # Keep at 1 — SKIP LOCKED handles concurrency safely
  selector:
    matchLabels:
      app: reco-worker
  template:
    metadata:
      labels:
        app: reco-worker
    spec:
      terminationGracePeriodSeconds: 60
      containers:
        - name: worker
          image: your-registry/recommendation-service:latest
          command: ["python", "-m", "worker.refresh_worker"]
          envFrom:
            - secretRef:
                name: reco-secrets
          env:
            - name: POLL_INTERVAL_SECONDS
              value: "30"
            - name: BATCH_SIZE
              value: "10"
          resources:
            requests:
              memory: "256Mi"
              cpu: "100m"
            limits:
              memory: "512Mi"
              cpu: "500m"
```

---

## Systemd (Bare Metal)

```ini
# /etc/systemd/system/reco-worker.service
[Unit]
Description=Recommendation Refresh Worker
After=network.target postgresql.service redis.service
Requires=postgresql.service

[Service]
Type=simple
User=appuser
WorkingDirectory=/opt/recommendation-service
EnvironmentFile=/opt/recommendation-service/.env
ExecStart=/opt/recommendation-service/venv/bin/python -m worker.refresh_worker
Restart=on-failure
RestartSec=10
StandardOutput=journal
StandardError=journal
KillSignal=SIGTERM
TimeoutStopSec=60

[Install]
WantedBy=multi-user.target
```

```bash
# Enable and start
sudo systemctl daemon-reload
sudo systemctl enable reco-worker
sudo systemctl start reco-worker
sudo journalctl -u reco-worker -f
```

For the scheduler:
```ini
# /etc/systemd/system/reco-scheduler.service
[Unit]
Description=Recommendation Refresh Scheduler
After=network.target postgresql.service redis.service

[Service]
Type=simple
User=appuser
WorkingDirectory=/opt/recommendation-service
EnvironmentFile=/opt/recommendation-service/.env
ExecStart=/opt/recommendation-service/venv/bin/python -m worker.scheduler
Restart=on-failure
RestartSec=10
StandardOutput=journal
StandardError=journal
KillSignal=SIGTERM
TimeoutStopSec=30

[Install]
WantedBy=multi-user.target
```

---

## Health Checks

```bash
# API health
curl http://localhost:8000/api/recommend/health

# Worker liveness — count pending jobs
psql $DATABASE_URL -c \
  "SELECT status, COUNT(*) FROM \"RecommendationRefreshJob\"
   GROUP BY status ORDER BY status;"

# Snapshot freshness — check any seller is current
psql $DATABASE_URL -c \
  "SELECT \"sellerId\",
          MAX(\"computedAt\") AS last_computed,
          EXTRACT(EPOCH FROM NOW() - MAX(\"computedAt\"))::int AS age_seconds
   FROM \"SellerRecommendation\"
   GROUP BY \"sellerId\"
   ORDER BY age_seconds DESC
   LIMIT 10;"
```

### Alert thresholds

| Condition | Alert |
|-----------|-------|
| `age_seconds > PRECOMPUTED_FRESHNESS_SECONDS` for any seller | Worker may be stuck |
| `PENDING` job count growing without bound | Worker not consuming queue |
| `FAILED` job count increasing | Check `lastError` column; may need `REFRESH_MAX_ATTEMPTS` increase |
| API `5xx` rate > 1% | Check DB/Redis connectivity |

---

## Rollback Strategy

- **API rollback:** Container/image rollback — the service is stateless.
- **Worker rollback:** Stop the worker, roll back the image, restart. In-flight jobs stay `IN_PROGRESS` until reset.
- **Reset stuck IN_PROGRESS jobs:**
  ```sql
  UPDATE "RecommendationRefreshJob"
  SET status = 'PENDING', "startedAt" = NULL
  WHERE status = 'IN_PROGRESS'
    AND "startedAt" < NOW() - INTERVAL '10 minutes';
  ```
- **Redis cache:** Invalidate via admin endpoint if stale data needs clearing:
  ```bash
  curl -X POST http://localhost:8000/api/recommend/cache/clear \
    -H "X-API-Key: $ADMIN_API_KEY"
  ```
