-- Durable storage for precomputed recommendations and refresh jobs.
-- Apply this in PostgreSQL before enabling the background refresh flow.

CREATE TABLE IF NOT EXISTS "SellerRecommendation" (
  id BIGSERIAL PRIMARY KEY,
  "sellerId" VARCHAR NOT NULL,
  "productId" INTEGER NOT NULL REFERENCES "Product"(id),
  score DOUBLE PRECISION NOT NULL,
  rank INTEGER NOT NULL,
  "isPersonalized" BOOLEAN NOT NULL DEFAULT FALSE,
  sources JSONB NOT NULL DEFAULT '{}'::jsonb,
  "computedAt" TIMESTAMP WITHOUT TIME ZONE NOT NULL,
  "algorithmVersion" VARCHAR NOT NULL,
  "createdAt" TIMESTAMP WITHOUT TIME ZONE NOT NULL DEFAULT NOW(),
  "updatedAt" TIMESTAMP WITHOUT TIME ZONE NOT NULL DEFAULT NOW(),
  CONSTRAINT uq_sellerrecommendation_seller_product UNIQUE ("sellerId", "productId")
);

CREATE INDEX IF NOT EXISTS ix_sellerrecommendation_seller_rank
  ON "SellerRecommendation" ("sellerId", rank);

CREATE TABLE IF NOT EXISTS "RecommendationRefreshJob" (
  id BIGSERIAL PRIMARY KEY,
  "sellerId" VARCHAR NOT NULL,
  trigger VARCHAR NOT NULL DEFAULT 'manual',
  status VARCHAR NOT NULL DEFAULT 'PENDING',
  priority INTEGER NOT NULL DEFAULT 100,
  "requestedBy" VARCHAR NULL,
  details JSONB NOT NULL DEFAULT '{}'::jsonb,
  "resultCount" INTEGER NOT NULL DEFAULT 0,
  "attemptCount" INTEGER NOT NULL DEFAULT 0,
  "algorithmVersion" VARCHAR NULL,
  "requestedAt" TIMESTAMP WITHOUT TIME ZONE NOT NULL DEFAULT NOW(),
  "startedAt" TIMESTAMP WITHOUT TIME ZONE NULL,
  "completedAt" TIMESTAMP WITHOUT TIME ZONE NULL,
  "lastError" TEXT NULL
);

CREATE INDEX IF NOT EXISTS ix_recommendationrefreshjob_status_requested
  ON "RecommendationRefreshJob" (status, "requestedAt");

CREATE INDEX IF NOT EXISTS ix_recommendationrefreshjob_seller_requested
  ON "RecommendationRefreshJob" ("sellerId", "requestedAt");
