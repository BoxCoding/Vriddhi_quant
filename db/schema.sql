-- ═══════════════════════════════════════════════════════════════════════════
-- NSE Options Trader — TimescaleDB Schema
-- Run once after container startup: make db-init
-- ═══════════════════════════════════════════════════════════════════════════

-- Enable TimescaleDB extension
CREATE EXTENSION IF NOT EXISTS timescaledb;

-- ── OHLCV Candles ──────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS candles (
    time        TIMESTAMPTZ     NOT NULL,
    symbol      TEXT            NOT NULL,   -- e.g. NIFTY, BANKNIFTY
    exchange    TEXT            NOT NULL DEFAULT 'NSE',
    timeframe   TEXT            NOT NULL,   -- 1m, 5m, 15m, 1d
    open        NUMERIC(12,2)   NOT NULL,
    high        NUMERIC(12,2)   NOT NULL,
    low         NUMERIC(12,2)   NOT NULL,
    close       NUMERIC(12,2)   NOT NULL,
    volume      BIGINT          NOT NULL DEFAULT 0,
    oi          BIGINT                      -- Open Interest (F&O only)
);
SELECT create_hypertable('candles', 'time', if_not_exists => TRUE);
CREATE UNIQUE INDEX IF NOT EXISTS candles_symbol_tf_time_idx
    ON candles (symbol, timeframe, time DESC);

-- ── Option Chain Snapshots ─────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS option_chain_snapshots (
    time            TIMESTAMPTZ     NOT NULL,
    underlying      TEXT            NOT NULL,   -- NIFTY | BANKNIFTY
    expiry          DATE            NOT NULL,
    strike          NUMERIC(10,2)   NOT NULL,
    option_type     CHAR(2)         NOT NULL,   -- CE | PE
    ltp             NUMERIC(10,2),
    bid             NUMERIC(10,2),
    ask             NUMERIC(10,2),
    oi              BIGINT,
    volume          BIGINT,
    iv              NUMERIC(8,4),               -- Implied Volatility (annualised %)
    delta           NUMERIC(8,6),
    gamma           NUMERIC(10,8),
    theta           NUMERIC(8,4),
    vega            NUMERIC(8,4),
    rho             NUMERIC(8,4)
);
SELECT create_hypertable('option_chain_snapshots', 'time', if_not_exists => TRUE);
CREATE INDEX IF NOT EXISTS option_chain_underlying_expiry_idx
    ON option_chain_snapshots (underlying, expiry, strike, option_type, time DESC);

-- ── Orders ─────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS orders (
    id              UUID            PRIMARY KEY DEFAULT gen_random_uuid(),
    broker_order_id TEXT            UNIQUE,
    created_at      TIMESTAMPTZ     NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ     NOT NULL DEFAULT NOW(),
    symbol          TEXT            NOT NULL,
    exchange        TEXT            NOT NULL DEFAULT 'NFO',
    order_type      TEXT            NOT NULL,   -- MARKET | LIMIT | SL | SL-M
    transaction_type TEXT          NOT NULL,   -- BUY | SELL
    quantity        INTEGER         NOT NULL,
    price           NUMERIC(10,2),
    trigger_price   NUMERIC(10,2),
    status          TEXT            NOT NULL DEFAULT 'PENDING',
    filled_qty      INTEGER         NOT NULL DEFAULT 0,
    avg_price       NUMERIC(10,2),
    strategy        TEXT,
    tag             TEXT,
    trading_mode    TEXT            NOT NULL DEFAULT 'paper'
);

-- ── Positions ──────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS positions (
    id              UUID            PRIMARY KEY DEFAULT gen_random_uuid(),
    symbol          TEXT            NOT NULL,
    exchange        TEXT            NOT NULL DEFAULT 'NFO',
    quantity        INTEGER         NOT NULL,
    avg_entry_price NUMERIC(10,2)   NOT NULL,
    current_price   NUMERIC(10,2),
    unrealised_pnl  NUMERIC(12,2),
    realised_pnl    NUMERIC(12,2)   NOT NULL DEFAULT 0,
    strategy        TEXT,
    opened_at       TIMESTAMPTZ     NOT NULL DEFAULT NOW(),
    closed_at       TIMESTAMPTZ,
    is_open         BOOLEAN         NOT NULL DEFAULT TRUE,
    trading_mode    TEXT            NOT NULL DEFAULT 'paper'
);

-- ── Trade History ──────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS trades (
    id              UUID            PRIMARY KEY DEFAULT gen_random_uuid(),
    order_id        UUID            REFERENCES orders(id),
    position_id     UUID            REFERENCES positions(id),
    executed_at     TIMESTAMPTZ     NOT NULL DEFAULT NOW(),
    symbol          TEXT            NOT NULL,
    transaction_type TEXT           NOT NULL,
    quantity        INTEGER         NOT NULL,
    price           NUMERIC(10,2)   NOT NULL,
    brokerage       NUMERIC(8,2)    NOT NULL DEFAULT 0,
    pnl             NUMERIC(12,2),
    strategy        TEXT
);

-- ── Signals ────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS signals (
    id              UUID            PRIMARY KEY DEFAULT gen_random_uuid(),
    generated_at    TIMESTAMPTZ     NOT NULL DEFAULT NOW(),
    underlying      TEXT            NOT NULL,
    strategy        TEXT            NOT NULL,
    direction       TEXT,           -- BULLISH | BEARISH | NEUTRAL
    confidence      NUMERIC(5,2),   -- 0-100
    legs            JSONB,          -- strategy legs as JSON
    status          TEXT            NOT NULL DEFAULT 'PENDING',
    llm_reasoning   TEXT,
    metadata        JSONB
);

-- ── Portfolio Snapshots (daily EOD) ────────────────────────────────────────
CREATE TABLE IF NOT EXISTS portfolio_snapshots (
    time            TIMESTAMPTZ     NOT NULL,
    total_capital   NUMERIC(14,2)   NOT NULL,
    used_margin     NUMERIC(14,2),
    free_margin     NUMERIC(14,2),
    unrealised_pnl  NUMERIC(14,2),
    realised_pnl    NUMERIC(14,2),
    net_delta       NUMERIC(10,4),
    net_gamma       NUMERIC(10,6),
    net_theta       NUMERIC(10,4),
    net_vega        NUMERIC(10,4)
);
SELECT create_hypertable('portfolio_snapshots', 'time', if_not_exists => TRUE);

-- ── Indexes ────────────────────────────────────────────────────────────────
CREATE INDEX IF NOT EXISTS orders_status_idx ON orders (status);
CREATE INDEX IF NOT EXISTS orders_strategy_idx ON orders (strategy);
CREATE INDEX IF NOT EXISTS positions_open_idx ON positions (is_open) WHERE is_open = TRUE;
CREATE INDEX IF NOT EXISTS signals_status_idx ON signals (status);
CREATE INDEX IF NOT EXISTS signals_generated_at_idx ON signals (generated_at DESC);
