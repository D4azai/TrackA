import os
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from types import SimpleNamespace

import httpx
import pytest

# Ensure required settings exist before importing the app modules.
os.environ.setdefault("DATABASE_URL", "sqlite:///./test.db")
os.environ.setdefault("ALLOWED_HOSTS", '["testserver","localhost","127.0.0.1"]')
os.environ.setdefault("CACHE_ENABLED", "false")
os.environ.setdefault("ADMIN_API_KEY", "test-admin-key")
os.environ["DEBUG"] = "false"

from app.main import app
from app.routers import recommendations as recommendation_router
from app.services.precomputed_service import PrecomputedRecommendationSnapshot


def _dummy_recommendations(limit: int = 1):
    return [
        {
            "product_id": 42,
            "score": 88.5,
            "rank": 1,
            "is_personalized": True,
            "sources": {
                "popularity": 20.0,
                "history": 30.0,
                "recency": 18.0,
                "newness": 15.0,
                "engagement": 5.5,
            },
        }
    ][:limit]


class DummyRecommendationEngine:
    def compute_recommendations(self, seller_id: str, limit: int):
        return _dummy_recommendations(limit)


class DummyPrecomputedService:
    def __init__(self, snapshot=None):
        self.snapshot = snapshot

    def get_latest_snapshot(self, seller_id: str, limit: int):
        return self.snapshot


class DummyRefreshService:
    def __init__(self):
        self.enqueued = []
        self.active_enqueues = []
        self.ran_limits = []

    def enqueue_seller_refresh(self, seller_id: str, trigger: str, requested_by=None, details=None, priority=None):
        self.enqueued.append(
            {
                "seller_id": seller_id,
                "trigger": trigger,
                "requested_by": requested_by,
                "details": details,
            }
        )
        return SimpleNamespace(job_id=101, created=True, status="PENDING")

    def enqueue_active_sellers(self, trigger: str, requested_by=None, limit=None, priority=None, details=None):
        self.active_enqueues.append(
            {
                "trigger": trigger,
                "requested_by": requested_by,
                "limit": limit,
                "details": details,
            }
        )
        return {"queued": 3, "already_queued": 1}

    def refresh_seller_now(self, seller_id: str, trigger: str, commit: bool = True, warm_cache: bool = True):
        return _dummy_recommendations(1)

    def run_pending_jobs(self, limit: int = 1):
        self.ran_limits.append(limit)
        return SimpleNamespace(processed=limit, succeeded=limit, failed=0)


@pytest.fixture
def anyio_backend():
    return "asyncio"


@asynccontextmanager
async def _http_client():
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            yield client

    app.dependency_overrides.clear()


async def _call_products(snapshot=None, refresh_service=None, seller_id="seller-1", limit=1):
    original_precomputed_builder = recommendation_router.build_precomputed_service
    original_refresh_builder = recommendation_router.build_refresh_service
    recommendation_router.build_precomputed_service = lambda db: DummyPrecomputedService(snapshot)
    recommendation_router.build_refresh_service = lambda db: refresh_service or DummyRefreshService()
    try:
        return await recommendation_router.get_recommendations(
            seller_id=seller_id,
            limit=limit,
            db=object(),
        )
    finally:
        recommendation_router.build_precomputed_service = original_precomputed_builder
        recommendation_router.build_refresh_service = original_refresh_builder


@pytest.mark.anyio
async def test_root_endpoint():
    async with _http_client() as client:
        response = await client.get("/")
    assert response.status_code == 200
    payload = response.json()
    assert payload["service"] == "Recommendation Service"


@pytest.mark.anyio
async def test_health_endpoint():
    async with _http_client() as client:
        response = await client.get("/api/recommend/health")
    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "healthy"


@pytest.mark.anyio
async def test_products_endpoint_returns_expected_shape():
    response = await _call_products()
    payload = response.model_dump()
    assert payload["seller_id"] == "seller-1"
    assert payload["count"] == 1
    assert len(payload["recommendations"]) == 1
    assert payload["recommendations"][0]["product_id"] == 42
    assert payload["recommendations"][0]["sources"]["history"] == 30.0


@pytest.mark.anyio
async def test_products_endpoint_reads_fresh_precomputed_snapshot(monkeypatch):
    monkeypatch.setattr(recommendation_router.settings, "cache_enabled", False)
    snapshot = PrecomputedRecommendationSnapshot(
        seller_id="seller-1",
        recommendations=_dummy_recommendations(1),
        computed_at=datetime.now(UTC),
        algorithm_version="test-v1",
        age_seconds=5.0,
        is_fresh=True,
    )
    response = await _call_products(snapshot=snapshot)
    payload = response.model_dump()
    assert payload["count"] == 1
    assert payload["cache_hit"] is False
    assert payload["recommendations"][0]["rank"] == 1


@pytest.mark.anyio
async def test_products_endpoint_returns_stale_snapshot_and_queues_refresh(monkeypatch):
    monkeypatch.setattr(recommendation_router.settings, "cache_enabled", False)
    monkeypatch.setattr(recommendation_router.settings, "serve_stale_precomputed", True)
    monkeypatch.setattr(recommendation_router.settings, "sync_recompute_fallback_enabled", False)
    refresh_service = DummyRefreshService()
    snapshot = PrecomputedRecommendationSnapshot(
        seller_id="seller-1",
        recommendations=_dummy_recommendations(1),
        computed_at=datetime.now(UTC),
        algorithm_version="test-v1",
        age_seconds=7200.0,
        is_fresh=False,
    )
    response = await _call_products(snapshot=snapshot, refresh_service=refresh_service)
    payload = response.model_dump()
    assert payload["count"] == 1
    assert refresh_service.enqueued[0]["trigger"] == "snapshot_stale"


@pytest.mark.anyio
async def test_products_endpoint_normalizes_seller_id():
    response = await _call_products(seller_id=" seller-1 ")
    payload = response.model_dump()
    assert payload["seller_id"] == "seller-1"


@pytest.mark.anyio
async def test_products_endpoint_rejects_blank_seller_id():
    with pytest.raises(recommendation_router.HTTPException) as exc_info:
        await _call_products(seller_id="   ")
    assert exc_info.value.status_code == 400
    assert exc_info.value.detail == "seller_id must not be blank."


@pytest.mark.anyio
async def test_products_endpoint_ignores_invalid_cached_payload(monkeypatch):
    monkeypatch.setattr(recommendation_router.settings, "cache_enabled", True)

    monkeypatch.setattr(
        recommendation_router.cache_service,
        "get_recommendations",
        lambda seller_id: [{"product_id": "bad-payload"}],
    )
    invalidated = {"called": False}

    def _delete(_seller_id: str):
        invalidated["called"] = True
        return True

    monkeypatch.setattr(recommendation_router.cache_service, "delete", _delete)

    response = await _call_products()
    payload = response.model_dump()
    assert payload["cache_hit"] is False
    assert payload["recommendations"][0]["product_id"] == 42
    assert invalidated["called"] is True


@pytest.mark.anyio
async def test_clear_cache_requires_api_key():
    with pytest.raises(recommendation_router.HTTPException) as exc_info:
        recommendation_router.require_api_key(None)
    assert exc_info.value.status_code == 401

    recommendation_router.require_api_key("test-admin-key")
    response = await recommendation_router.clear_cache(seller_id=None)
    assert response["status"] == "cleared"


@pytest.mark.anyio
async def test_run_refresh_jobs_endpoint():
    refresh_service = DummyRefreshService()
    original_refresh_builder = recommendation_router.build_refresh_service
    recommendation_router.build_refresh_service = lambda db: refresh_service
    try:
        response = await recommendation_router.run_refresh_jobs(limit=2, db=object())
    finally:
        recommendation_router.build_refresh_service = original_refresh_builder
    payload = response.model_dump()
    assert payload["processed"] == 2
    assert refresh_service.ran_limits == [2]


@pytest.mark.anyio
async def test_metrics_endpoint_exposes_prometheus_payload():
    async with _http_client() as client:
        response = await client.get("/metrics")
    assert response.status_code == 200
    assert "text/plain" in response.headers["content-type"]
    assert "http_requests_total" in response.text
