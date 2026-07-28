-- Market/margin tables for the margins analytics dashboard (OTD-15).
--
-- Runs in the dedicated `analytics` schema (Flyway defaultSchema): commodity/
-- freight/FX series and daily observations, the otter-retail product catalog,
-- the computed per-SKU daily margins, and a log of seed/ingestion runs.

CREATE TABLE IF NOT EXISTS market_series (
    series_code VARCHAR(64) PRIMARY KEY,
    name        VARCHAR(128) NOT NULL,
    unit        VARCHAR(32) NOT NULL,
    currency    VARCHAR(8) NOT NULL,
    category    VARCHAR(16) NOT NULL
);

CREATE TABLE IF NOT EXISTS market_prices (
    series_code VARCHAR(64) NOT NULL REFERENCES market_series(series_code),
    price_date  DATE NOT NULL,
    value       NUMERIC(14,6) NOT NULL,
    source      VARCHAR(16) NOT NULL DEFAULT 'synthetic',
    created_at  TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW(),
    PRIMARY KEY (series_code, price_date)
);

CREATE INDEX IF NOT EXISTS idx_market_prices_series_date
    ON market_prices(series_code, price_date);

CREATE TABLE IF NOT EXISTS products (
    sku                   VARCHAR(32) PRIMARY KEY,
    name                  VARCHAR(128) NOT NULL,
    category              VARCHAR(64) NOT NULL,
    commodity_series_code VARCHAR(64) NOT NULL REFERENCES market_series(series_code),
    content_kg            NUMERIC(10,4) NOT NULL,
    freight_kg            NUMERIC(10,4) NOT NULL,
    overhead_pct          NUMERIC(5,2) NOT NULL,
    list_price_usd        NUMERIC(10,2) NOT NULL,
    supplier              VARCHAR(128) NOT NULL
);

CREATE TABLE IF NOT EXISTS product_margin_daily (
    sku                VARCHAR(32) NOT NULL REFERENCES products(sku),
    margin_date        DATE NOT NULL,
    commodity_cost_usd NUMERIC(12,4) NOT NULL,
    freight_cost_usd   NUMERIC(12,4) NOT NULL,
    overhead_cost_usd  NUMERIC(12,4) NOT NULL,
    cogs_usd           NUMERIC(12,4) NOT NULL,
    margin_pct         NUMERIC(8,4) NOT NULL,
    computed_at        TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW(),
    PRIMARY KEY (sku, margin_date)
);

CREATE INDEX IF NOT EXISTS idx_product_margin_daily_date
    ON product_margin_daily(margin_date);

CREATE TABLE IF NOT EXISTS sync_runs (
    id                 BIGSERIAL PRIMARY KEY,
    run_type           VARCHAR(16) NOT NULL,
    observations_count INTEGER NOT NULL DEFAULT 0,
    started_at         TIMESTAMP WITH TIME ZONE NOT NULL,
    completed_at       TIMESTAMP WITH TIME ZONE,
    status             VARCHAR(16) NOT NULL,
    detail             TEXT
);
