"""PostgreSQL persistence for OptionPilot.

Schema adapted from the production trading_bot db.py, trimmed to what this
agent uses, plus options/LLM-specific tables:
  - trades gains option columns (occ_symbol, strategy, premium, delta, dte, legs)
  - llm_decisions stores every LLM session (mandate, decision, reasoning, cost)
  - guardrail_events stores every violation the validator catches
  - risk_state persists PositionManager drawdown/halt state across restarts
"""
import os
from typing import Optional

import psycopg2
from psycopg2.extras import Json


def get_db_config():
    return {
        'host': os.getenv("POSTGRES_HOST", "localhost"),
        'port': os.getenv("POSTGRES_PORT", "5432"),
        'user': os.getenv("POSTGRES_USER", "optionpilot"),
        'password': os.getenv("POSTGRES_PASSWORD", "optionpilot"),
        'database': os.getenv("POSTGRES_DB", "optionpilot"),
    }


def get_connection():
    return psycopg2.connect(**get_db_config())


def init_db():
    conn = get_connection()
    c = conn.cursor()

    c.execute("""
    CREATE TABLE IF NOT EXISTS trades (
        id SERIAL PRIMARY KEY,
        ts TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        symbol TEXT,            -- underlying
        occ_symbol TEXT,        -- option contract (NULL for stock legs)
        strategy TEXT,          -- CSP | CC | BULL_CALL_SPREAD | LONG_CALL | STOCK
        side TEXT,              -- sell_to_open | buy_to_close | buy_to_open | sell_to_close
        qty REAL,
        price REAL,             -- per-contract premium (or share price for stock)
        premium REAL,           -- total premium = price * 100 * qty (signed: + collected)
        delta_at_entry REAL,
        dte INTEGER,
        legs JSONB,             -- multi-leg structures
        pnl REAL,
        reason TEXT,
        client_order_id TEXT,
        broker_order_id TEXT,
        equity_before REAL
    )
    """)

    c.execute("""
    CREATE TABLE IF NOT EXISTS equity (
        id SERIAL PRIMARY KEY,
        ts TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        equity REAL,
        cash REAL,
        options_buying_power REAL
    )
    """)

    c.execute("""
    CREATE TABLE IF NOT EXISTS decisions (
        id SERIAL PRIMARY KEY,
        ts TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        symbol TEXT,
        signal TEXT,
        action TEXT,
        reason TEXT,
        price REAL,
        details JSONB
    )
    """)

    c.execute("""
    CREATE TABLE IF NOT EXISTS llm_decisions (
        id SERIAL PRIMARY KEY,
        ts TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        session_id TEXT,
        symbol TEXT,
        mandate JSONB,
        decision JSONB,
        reasoning TEXT,
        num_turns INTEGER,
        cost_usd REAL,
        validated BOOLEAN,
        validation_errors JSONB
    )
    """)

    c.execute("""
    CREATE TABLE IF NOT EXISTS guardrail_events (
        id SERIAL PRIMARY KEY,
        ts TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        session_id TEXT,
        symbol TEXT,
        event TEXT,             -- e.g. out_of_band_delta, foreign_order_id, oversize
        details JSONB,
        action_taken TEXT       -- cancelled | flattened | llm_disabled
    )
    """)

    c.execute("""
    CREATE TABLE IF NOT EXISTS risk_state (
        market TEXT PRIMARY KEY,
        peak_equity REAL,
        trading_halted BOOLEAN,
        halt_reason TEXT,
        llm_violations INTEGER DEFAULT 0,
        llm_enabled BOOLEAN DEFAULT TRUE,
        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
    """)

    for idx in (
        "CREATE INDEX IF NOT EXISTS idx_trades_ts ON trades(ts DESC)",
        "CREATE INDEX IF NOT EXISTS idx_trades_symbol ON trades(symbol)",
        "CREATE INDEX IF NOT EXISTS idx_equity_ts ON equity(ts DESC)",
        "CREATE INDEX IF NOT EXISTS idx_decisions_ts ON decisions(ts DESC)",
        "CREATE INDEX IF NOT EXISTS idx_llm_decisions_ts ON llm_decisions(ts DESC)",
    ):
        c.execute(idx)

    conn.commit()
    conn.close()
    print("PostgreSQL schema initialized")


# ── risk_state helpers (PositionManager persistence) ─────────────────

def load_risk_state(market: str) -> Optional[dict]:
    conn = get_connection()
    try:
        c = conn.cursor()
        c.execute("SELECT peak_equity, trading_halted, halt_reason, "
                  "llm_violations, llm_enabled FROM risk_state WHERE market=%s",
                  (market,))
        row = c.fetchone()
        if not row:
            return None
        return {
            "peak_equity": row[0], "trading_halted": row[1],
            "halt_reason": row[2] or "", "llm_violations": row[3],
            "llm_enabled": row[4],
        }
    finally:
        conn.close()


def save_risk_state(market: str, peak_equity: float, trading_halted: bool,
                    halt_reason: str) -> None:
    conn = get_connection()
    try:
        c = conn.cursor()
        c.execute("""
        INSERT INTO risk_state (market, peak_equity, trading_halted, halt_reason, updated_at)
        VALUES (%s, %s, %s, %s, CURRENT_TIMESTAMP)
        ON CONFLICT (market) DO UPDATE SET
            peak_equity=EXCLUDED.peak_equity,
            trading_halted=EXCLUDED.trading_halted,
            halt_reason=EXCLUDED.halt_reason,
            updated_at=CURRENT_TIMESTAMP
        """, (market, peak_equity, trading_halted, halt_reason))
        conn.commit()
    finally:
        conn.close()


def record_llm_violation(market: str, limit: int) -> bool:
    """Increment the persisted violation counter. Returns the new llm_enabled
    state (False once the limit is reached — sticky until manually reset)."""
    conn = get_connection()
    try:
        c = conn.cursor()
        c.execute("""
        INSERT INTO risk_state (market, peak_equity, trading_halted, halt_reason, llm_violations)
        VALUES (%s, 0, FALSE, '', 1)
        ON CONFLICT (market) DO UPDATE SET
            llm_violations = risk_state.llm_violations + 1,
            updated_at = CURRENT_TIMESTAMP
        RETURNING llm_violations
        """, (market,))
        violations = c.fetchone()[0]
        enabled = violations < limit
        if not enabled:
            c.execute("UPDATE risk_state SET llm_enabled=FALSE WHERE market=%s", (market,))
        conn.commit()
        return enabled
    finally:
        conn.close()


def llm_is_enabled(market: str) -> bool:
    state = load_risk_state(market)
    return True if state is None else bool(state.get("llm_enabled", True))
