# Diagrams (Mermaid)

## Architecture image

Standalone SVG image:
- [recommendation-architecture.svg](/home/aymane/TrackA-1/docs/recommendation-architecture.svg)

## Component diagram

```mermaid
flowchart LR
  client[Client / Next.js] -->|HTTP| api[FastAPI Recommendation Service]
  api -->|SQLAlchemy| db[(PostgreSQL)]
  api -->|Redis| redis[(Redis Cache)]
  prom[Prometheus] -->|scrape /metrics| api
  graf[Grafana] -->|query| prom
```

## Sequence diagram (GET /products)

```mermaid
sequenceDiagram
  autonumber
  participant C as Client
  participant A as API (FastAPI)
  participant R as Redis
  participant E as RecommendationEngine
  participant D as DataService
  participant P as PostgreSQL

  C->>A: GET /api/recommend/products?seller_id=S&limit=L
  A->>R: GET rec:products:S
  alt Cache hit
    R-->>A: cached list
    A-->>C: 200 recommendations (sliced to L)
  else Cache miss
    R-->>A: null
    A->>E: compute_recommendations(S, L)
    E->>D: get_popular_products()
    D->>P: query
    P-->>D: results
    E->>D: get_seller_order_history(S)
    D->>P: query
    P-->>D: results
    E->>D: batch signals for candidate_ids
    D->>P: queries (engagement, recency, newness, affinity)
    P-->>D: results
    E-->>A: ranked list
    A->>R: SETEX rec:products:S (TTL)
    A-->>C: 200 recommendations
  end
```

## “Class diagram” (logical modules)

```mermaid
classDiagram
  class RecommendationEngine {
    +compute_recommendations(seller_id, limit) List
  }
  class DataService {
    +get_popular_products()
    +get_seller_order_history()
    +get_category_affinity_scores()
    +get_engagement_scores_batch()
    +get_recency_scores_batch()
    +get_newness_scores_batch()
    +get_catalog_fallback_products()
  }
  class CacheService {
    +get_recommendations(seller_id)
    +set_recommendations(seller_id, recs, ttl)
    +delete(seller_id)
    +clear_all()
    +is_healthy()
  }

  RecommendationEngine --> DataService : uses
  RecommendationEngine ..> CacheService : called by API layer
```
