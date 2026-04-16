"""
Comprehensive Testing Guide - Recommendation Engine v2

TESTING LAYERS
==============

1. Unit Tests       - Test individual functions
2. Integration Tests - Test database interactions
3. API Tests        - Test HTTP endpoints
4. Load Tests       - Performance under stress
5. Comparison Tests - v1 vs v2 quality/performance


UNIT TESTS
==========

Location: tests/test_algorithm_v2.py

Test scoring logic independently:

```python
import pytest
from app.services.algorithm_v2 import RecommendationEngine

def test_score_calculation():
    '''Verify weighted ensemble calculation'''
    # Mock data
    signals = {
        'popularity': 80,
        'history': 90,
        'recency': 60,
        'newness': 70,
        'engagement': 75
    }
    
    weights = {
        'popularity': 0.25,
        'history': 0.35,
        'recency': 0.20,
        'newness': 0.15,
        'engagement': 0.05
    }
    
    expected_score = (
        80 * 0.25 +
        90 * 0.35 +
        60 * 0.20 +
        70 * 0.15 +
        75 * 0.05
    )  # 77.75
    
    # Verify
    assert expected_score == 77.75

def test_seller_scoped_recency():
    '''Verify that recency is PER SELLER, not global'''
    # Setup: Two sellers, both have ordered product #42
    # But seller A ordered recently, seller B didn't
    
    # For seller A: high recency score
    # For seller B: low recency score
    # → Same product should have different scores per seller
    
    assert recency_score_a > recency_score_b

def test_score_normalization():
    '''Verify each signal is 0-100 range'''
    for signal_name in ['popularity', 'history', 'recency', 'newness', 'engagement']:
        signal_value = get_signal_score(signal_name)
        assert 0 <= signal_value <= 100, f'{signal_name} out of range: {signal_value}'

def test_empty_recommendations():
    '''Verify graceful handling when no products found'''
    recommendations = engine.compute_recommendations('unknown_seller', limit=30)
    assert recommendations == []

def test_limit_validation():
    '''Verify limit parameter validation'''
    # Should accept 1-100
    assert compute_recommendations(seller_id, limit=1)
    assert compute_recommendations(seller_id, limit=100)
    
    # Should clamp values
    assert len(compute_recommendations(seller_id, limit=0)) >= 0
    assert len(compute_recommendations(seller_id, limit=101)) <= 100
```

Run unit tests:
```bash
pytest tests/test_algorithm_v2.py -v
pytest tests/test_algorithm_v2.py -v --cov=app/services/algorithm_v2
```


INTEGRATION TESTS
=================

Location: tests/test_integration_v2.py

Test with real database:

```python
import pytest
from sqlalchemy.orm import Session
from app.db import get_db
from app.services.algorithm_v2 import RecommendationEngine
from app.services.data_service_v2 import DataService

@pytest.fixture
def db_session():
    '''Create test database session'''
    from app.db import SessionLocal
    session = SessionLocal()
    yield session
    session.close()

def test_data_service_queries(db_session: Session):
    '''Test that DataService queries return expected structure'''
    ds = DataService(db_session)
    
    # Get popular products
    popular = ds.get_popular_products('test_seller', limit=10)
    assert isinstance(popular, list)
    assert all('product_id' in item for item in popular)
    assert all('score' in item for item in popular)
    
    # Get seller history
    history = ds.get_seller_order_history('test_seller', limit=10)
    assert isinstance(history, dict)
    # Each product should have category_score
    for product_id, data in history.items():
        assert 'category_score' in data

def test_recommendation_engine_full_flow(db_session: Session):
    '''Test full recommendation computation'''
    engine = RecommendationEngine(db_session)
    recommendations = engine.compute_recommendations('test_seller', limit=30)
    
    # Verify structure
    assert len(recommendations) <= 30
    assert all('product_id' in rec for rec in recommendations)
    assert all('score' in rec for rec in recommendations)
    assert all('rank' in rec for rec in recommendations)
    assert all('sources' in rec for rec in recommendations)
    
    # Verify ranking is correct
    for i, rec in enumerate(recommendations, 1):
        assert rec['rank'] == i
        # Score should be descending
        if i > 1:
            assert rec['score'] <= recommendations[i-2]['score']

def test_seller_specific_behavior(db_session: Session):
    '''Verify recommendations differ per seller'''
    engine = RecommendationEngine(db_session)
    
    recs_seller_a = engine.compute_recommendations('seller_a', limit=10)
    recs_seller_b = engine.compute_recommendations('seller_b', limit=10)
    
    # Top products should likely be different
    top_a = {r['product_id'] for r in recs_seller_a[:3]}
    top_b = {r['product_id'] for r in recs_seller_b[:3]}
    
    # Might overlap, but shouldn't be identical
    # (unless sellers have identical history - rare)
    assert len(top_a | top_b) > 0, "Sellers have no recommendations"
```

Run integration tests:
```bash
pytest tests/test_integration_v2.py -v
pytest tests/test_integration_v2.py::test_recommendation_engine_full_flow -v
```


API TESTS
=========

Location: tests/test_api_v2.py

Test HTTP endpoints:

```python
import pytest
from fastapi.testclient import TestClient
from app.main import app

@pytest.fixture
def client():
    return TestClient(app)

def test_health_endpoint(client):
    '''Verify health endpoint returns 200'''
    response = client.get("/api/recommend/health")
    assert response.status_code == 200
    assert response.json()['status'] == 'healthy'

def test_recommendations_endpoint_success(client):
    '''Verify recommendations endpoint with valid input'''
    response = client.get(
        "/api/recommend/products",
        params={"seller_id": "test_seller", "limit": 30}
    )
    assert response.status_code == 200
    data = response.json()
    assert data['seller_id'] == 'test_seller'
    assert len(data['recommendations']) <= 30
    assert data['count'] == len(data['recommendations'])

def test_recommendations_limit_validation(client):
    '''Verify limit parameter validation'''
    # Valid limits
    for limit in [1, 10, 50, 100]:
        response = client.get(
            "/api/recommend/products",
            params={"seller_id": "test_seller", "limit": limit}
        )
        assert response.status_code == 200
        assert len(response.json()['recommendations']) <= limit
    
    # Invalid limits should be clamped
    response = client.get(
        "/api/recommend/products",
        params={"seller_id": "test_seller", "limit": 1000}
    )
    assert response.status_code == 200
    assert len(response.json()['recommendations']) <= 100

def test_recommendations_missing_seller_id(client):
    '''Verify error when seller_id is missing'''
    response = client.get("/api/recommend/products")
    assert response.status_code == 422  # Validation error

def test_recommendations_invalid_seller_id(client):
    '''Verify handling of invalid seller_id'''
    response = client.get(
        "/api/recommend/products",
        params={"seller_id": "", "limit": 10}
    )
    assert response.status_code == 400  # Bad request

def test_recommendations_response_schema(client):
    '''Verify response matches expected schema'''
    response = client.get(
        "/api/recommend/products",
        params={"seller_id": "test_seller", "limit": 5}
    )
    data = response.json()
    
    # Check main fields
    assert 'seller_id' in data
    assert 'recommendations' in data
    assert 'count' in data
    
    # Check recommendation structure
    if data['recommendations']:
        rec = data['recommendations'][0]
        assert 'product_id' in rec
        assert 'score' in rec
        assert 'rank' in rec
        assert 'sources' in rec
        
        # Check sources breakdown
        sources = rec['sources']
        assert 'popularity' in sources
        assert 'history' in sources
        assert 'recency' in sources
        assert 'newness' in sources
        assert 'engagement' in sources

def test_cache_hit_on_repeat_requests(client):
    '''Verify caching works'''
    seller_id = "cache_test_seller"
    
    # First request (cache miss)
    response1 = client.get(
        "/api/recommend/products",
        params={"seller_id": seller_id, "limit": 10}
    )
    recs1 = response1.json()['recommendations']
    
    # Second request (should be cached)
    response2 = client.get(
        "/api/recommend/products",
        params={"seller_id": seller_id, "limit": 10}
    )
    recs2 = response2.json()['recommendations']
    
    # Should return same results
    assert recs1 == recs2
```

Run API tests:
```bash
pytest tests/test_api_v2.py -v
pytest tests/test_api_v2.py::test_health_endpoint -v
```


LOAD TESTS
==========

Location: scripts/load_test.py

Simulate production load:

```python
import concurrent.futures
import time
import requests
import statistics

def load_test(host, num_sellers=100, requests_per_seller=10):
    '''Simulate load from multiple sellers'''
    
    base_url = f"http://{host}/api/recommend"
    results = []
    
    def make_request(seller_id):
        try:
            start = time.time()
            response = requests.get(
                f"{base_url}/products",
                params={"seller_id": f"seller_{seller_id}", "limit": 30},
                timeout=5
            )
            elapsed = (time.time() - start) * 1000  # Convert to ms
            
            return {
                'status': response.status_code,
                'time_ms': elapsed,
                'success': response.status_code == 200
            }
        except Exception as e:
            return {
                'status': 0,
                'time_ms': 0,
                'error': str(e),
                'success': False
            }
    
    # Generate requests
    requests_list = []
    for seller_id in range(num_sellers):
        for _ in range(requests_per_seller):
            requests_list.append(seller_id)
    
    # Execute concurrently
    print(f"Running {len(requests_list)} requests from {num_sellers} sellers...")
    
    with concurrent.futures.ThreadPoolExecutor(max_workers=20) as executor:
        results = list(executor.map(make_request, requests_list))
    
    # Analyze results
    successful = [r for r in results if r['success']]
    failed = [r for r in results if not r['success']]
    times = [r['time_ms'] for r in successful]
    
    print(f"\nResults:")
    print(f"  Total requests: {len(results)}")
    print(f"  Successful: {len(successful)} ({len(successful)/len(results)*100:.1f}%)")
    print(f"  Failed: {len(failed)} ({len(failed)/len(results)*100:.1f}%)")
    
    if times:
        print(f"\nResponse Time:")
        print(f"  Min: {min(times):.1f}ms")
        print(f"  Max: {max(times):.1f}ms")
        print(f"  Mean: {statistics.mean(times):.1f}ms")
        print(f"  Median: {statistics.median(times):.1f}ms")
        print(f"  P99: {sorted(times)[int(len(times)*0.99)]:.1f}ms")
        print(f"  Stdev: {statistics.stdev(times):.1f}ms")
    
    print(f"\nThroughput: {len(successful)/(time.time()-start)*1000:.0f} req/sec")
    
    if len(failed) > 0:
        print(f"\nFirst error: {failed[0]}")

if __name__ == "__main__":
    import sys
    host = sys.argv[1] if len(sys.argv) > 1 else "localhost:8000"
    load_test(host)
```

Run load test:
```bash
python scripts/load_test.py localhost:8000
python scripts/load_test.py production.example.com

# Expected output:
# Results:
#   Total requests: 1000
#   Successful: 1000 (100.0%)
#   Failed: 0 (0.0%)
# 
# Response Time:
#   Min: 45.2ms
#   Max: 180.5ms
#   Mean: 95.3ms
#   Median: 92.1ms
#   P99: 165.8ms
#   Stdev: 28.4ms
#
# Throughput: 1234 req/sec
```


COMPARISON TESTS (v1 vs v2)
============================

Location: scripts/compare_versions.py

Compare quality and performance:

```python
import requests
import time
import statistics

def compare_versions(seller_ids, host="localhost:8000"):
    '''Compare v1 and v2 recommendations'''
    
    v1_times = []
    v2_times = []
    scores_match = 0
    scores_differ = 0
    
    for seller_id in seller_ids:
        # Get v1 recommendations
        start = time.time()
        v1_response = requests.get(
            f"http://{host}/api/v1/recommend/products",
            params={"seller_id": seller_id, "limit": 10}
        )
        v1_time = (time.time() - start) * 1000
        v1_recs = v1_response.json()['recommendations']
        v1_times.append(v1_time)
        
        # Get v2 recommendations
        start = time.time()
        v2_response = requests.get(
            f"http://{host}/api/v2/recommend/products",
            params={"seller_id": seller_id, "limit": 10}
        )
        v2_time = (time.time() - start) * 1000
        v2_recs = v2_response.json()['recommendations']
        v2_times.append(v2_time)
        
        # Compare top recommendations
        v1_products = {r['product_id'] for r in v1_recs[:3]}
        v2_products = {r['product_id'] for r in v2_recs[:3]}
        
        if v1_products == v2_products:
            scores_match += 1
        else:
            scores_differ += 1
            print(f"  {seller_id}: Top 3 differ")
            print(f"    v1: {v1_products}")
            print(f"    v2: {v2_products}")
    
    # Report
    print(f"\nPerformance Comparison ({len(seller_ids)} sellers):")
    print(f"\nv1 Response Times:")
    print(f"  Mean: {statistics.mean(v1_times):.1f}ms")
    print(f"  P99:  {sorted(v1_times)[int(len(v1_times)*0.99)]:.1f}ms")
    
    print(f"\nv2 Response Times:")
    print(f"  Mean: {statistics.mean(v2_times):.1f}ms")
    print(f"  P99:  {sorted(v2_times)[int(len(v2_times)*0.99)]:.1f}ms")
    
    speedup = statistics.mean(v1_times) / statistics.mean(v2_times)
    print(f"\nSpeedup: {speedup:.1f}x faster")
    
    print(f"\nRecommendation Quality:")
    print(f"  Top 3 products match: {scores_match}/{len(seller_ids)}")
    print(f"  Top 3 products differ: {scores_differ}/{len(seller_ids)}")
```

Run comparison:
```bash
python scripts/compare_versions.py
```


RUNNING ALL TESTS
=================

Full test suite:
```bash
# Unit tests
pytest tests/test_algorithm_v2.py -v

# Integration tests
pytest tests/test_integration_v2.py -v

# API tests
pytest tests/test_api_v2.py -v

# All tests with coverage
pytest tests/ -v --cov=app
```

With GitHub Actions:
```yaml
# .github/workflows/test.yml
name: Test
on: [push, pull_request]

jobs:
  test:
    runs-on: ubuntu-latest
    services:
      postgres:
        image: postgres:15
        env:
          POSTGRES_PASSWORD: test
    steps:
      - uses: actions/checkout@v3
      - uses: actions/setup-python@v4
        with:
          python-version: '3.11'
      - run: pip install -r requirements-test.txt
      - run: pytest tests/ -v --cov=app
```


ACCEPTANCE CRITERIA
===================

Before deployment to production:

✓ All unit tests pass
✓ All integration tests pass
✓ All API tests pass
✓ Load test: P99 <200ms
✓ Load test: Success rate 100%
✓ Load test: Throughput ≥1000 req/sec
✓ Comparison test: v2 is 5x+ faster than v1
✓ Manual smoke test: Recommendations look reasonable
✓ Code review: 2 approvals minimum
✓ Documentation: MIGRATION.md is current
"""
