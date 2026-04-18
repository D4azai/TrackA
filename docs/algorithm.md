# Recommendation algorithm

## Goal

Rank products for a seller using multiple signals with configurable weights.

## Signals

The algorithm is a weighted ensemble of five signals:
- **Popularity**: global order volume in recent period
- **History**: seller’s past orders (strong personalization signal)
- **Recency**: when the seller last ordered the product (seller-scoped)
- **Newness**: product age (newer tends to score higher)
- **Engagement**: likes/reactions and comments

## Candidate selection

Candidate pool is built from:
1. **Popular products** (global)
2. **Seller order history** (personalized)
3. **Catalog fallback** to pad the pool if popularity + history are too small

Typically, the engine computes over `limit * 2` candidates, then returns top `limit`.

## Cold start handling

If the seller has never ordered a specific product, the history signal falls back to
**category affinity** (discounted) to boost products from categories the seller buys often.

## Scoring and ranking

For each candidate product:
- fetch all signals in batch
- compute \(score = \sum_i w_i \cdot signal_i\)
- drop low scores under `MIN_SCORE_THRESHOLD`
- sort descending by score and assign ranks

## Tunable settings

Configured via env vars (see `.env.example`):
- `WEIGHT_POPULARITY`, `WEIGHT_HISTORY`, `WEIGHT_RECENCY`, `WEIGHT_NEWNESS`, `WEIGHT_ENGAGEMENT`
- `MAX_LIMIT`
- `MIN_SCORE_THRESHOLD`

