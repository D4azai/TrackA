import os

from fastapi.testclient import TestClient

# Ensure required settings exist before importing the app modules.
os.environ.setdefault("DATABASE_URL", "sqlite:///./test.db")
os.environ.setdefault("ALLOWED_HOSTS", '["testserver","localhost","127.0.0.1"]')
os.environ.setdefault("CACHE_ENABLED", "false")
os.environ.setdefault("ADMIN_API_KEY", "test-admin-key")

from app.main import app
from app.routers.recommendations import get_recommendation_engine


class DummyRecommendationEngine:
    def compute_recommendations(self, seller_id: str, limit: int):
        return [
            {
                "product_id": 42,
                "score": 88.5,
                "rank": 1,
                "sources": {
                    "popularity": 20.0,
                    "history": 30.0,
                    "recency": 18.0,
                    "newness": 15.0,
                    "engagement": 5.5,
                },
            }
        ][:limit]


def _override_engine():
    return DummyRecommendationEngine()


def _client() -> TestClient:
    app.dependency_overrides[get_recommendation_engine] = _override_engine
    return TestClient(app)


def test_root_endpoint():
    client = _client()
    response = client.get("/")
    assert response.status_code == 200
    payload = response.json()
    assert payload["service"] == "Recommendation Service"


def test_health_endpoint():
    client = _client()
    response = client.get("/api/recommend/health")
    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "healthy"


def test_products_endpoint_returns_expected_shape():
    client = _client()
    response = client.get("/api/recommend/products?seller_id=seller-1&limit=1")
    assert response.status_code == 200
    payload = response.json()
    assert payload["seller_id"] == "seller-1"
    assert payload["count"] == 1
    assert len(payload["recommendations"]) == 1
    assert payload["recommendations"][0]["product_id"] == 42


def test_clear_cache_requires_api_key():
    client = _client()
    response = client.post("/api/recommend/cache/clear")
    assert response.status_code == 401

    authorized = client.post(
        "/api/recommend/cache/clear",
        headers={"X-API-Key": "test-admin-key"},
    )
    assert authorized.status_code == 200


def test_metrics_endpoint_exposes_prometheus_payload():
    client = _client()
    response = client.get("/metrics")
    assert response.status_code == 200
    assert "text/plain" in response.headers["content-type"]
    assert "http_requests_total" in response.text
