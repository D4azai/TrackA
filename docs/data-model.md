# Data model and queries

This service uses SQLAlchemy ORM models in `app/models.py`. Only tables needed by the recommendation flow are modeled.

## Core entities (high level)

- **User** (`User`)
  - sellers are represented as users with `id`
- **Product** (`Product`)
  - includes `categoryId`, `createdAt`, `status`, `isPublic`, ratings
- **Order** (`Order`)
  - includes `sellerId`, `status`, `createdAt`
- **OrderItem** (`OrderItem`)
  - links orders to products and quantities
- **ProductReaction** / **ProductComment**
  - engagement signals
- **Category**
  - used for category affinity scoring

## Query patterns

All queries are designed to avoid N+1:
- candidate IDs are computed first
- signals are fetched with batch queries using `IN (candidate_ids)`

## Signal queries (where to look)

- **Popularity**: `DataService.get_popular_products`
  - counts orders and quantities over a lookback window
  - filters out non-available and non-public products

- **Seller history**: `DataService.get_seller_order_history`
  - seller-scoped order aggregation

- **Category affinity**: `DataService.get_category_affinity_scores`
  - query 1: seller order counts by category
  - query 2: candidate product categories

- **Engagement**: `DataService.get_engagement_scores_batch`
  - outer joins reactions and comments on `Product`

- **Recency**: `DataService.get_recency_scores_batch`
  - `MAX(createdAt)` per product for the given seller

- **Newness**: `DataService.get_newness_scores_batch`
  - uses `Product.createdAt` to produce an age-based score

- **Catalog fallback**: `DataService.get_catalog_fallback_products`
  - fetches additional candidates ordered by rating and creation date

