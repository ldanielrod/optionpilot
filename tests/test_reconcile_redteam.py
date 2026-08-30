"""Red-team the LLM reconcile path with a fake broker: violating orders must
be cancelled, recorded, and trip the kill switch after the limit.

Uses the real DB (local dev postgres) for the kill-switch persistence.
"""
import os
import sys
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv
load_dotenv()

import db
from config import CONFIG
from core.mandate import OptionMandate, make_order_id
from ledger import Ledger

MARKET = "REDTEAM_TEST"


class FakeTrading:
    def __init__(self, orders):
        self.orders = orders
        self.cancelled, self.closed = [], []

    def get_orders(self, req=None):
        return self.orders

    def get_order_by_id(self, oid):
        for o in self.orders:
            if o.id == oid:
                return o
        raise KeyError(oid)

    def cancel_order_by_id(self, oid):
        self.cancelled.append(oid)

    def close_position(self, symbol):
        self.closed.append(symbol)


class FakeOptions:
    def __init__(self, quotes=None):
        self.quotes = quotes or {}

    def get_quotes(self, symbols):
        return {s: self.quotes[s] for s in symbols if s in self.quotes}


def make_trader(trading, options):
    from agent.llm_trader import LLMTrader
    t = object.__new__(LLMTrader)  # skip __init__ (no MCP binary needed)
    t.config, t.ledger, t.options, t.trading = CONFIG, Ledger(), options, trading
    t.market = MARKET
    t.api_key = t.api_secret = "x"
    return t


def order(symbol, cid, qty=1, side="sell", otype="limit", limit_price=2.0,
          status="accepted", filled_qty=0):
    now = datetime.now(timezone.utc)
    return SimpleNamespace(id=f"oid-{cid[-8:]}", symbol=symbol,
                           client_order_id=cid, qty=qty, side=side, type=otype,
                           limit_price=limit_price, status=status,
                           filled_qty=filled_qty, filled_avg_price=None,
                           submitted_at=now)


def expiry(days):
    return f"{datetime.now(timezone.utc).date() + timedelta(days=days):%y%m%d}"


def reset_state():
    conn = db.get_connection()
    c = conn.cursor()
    c.execute("DELETE FROM risk_state WHERE market=%s", (MARKET,))
    conn.commit()
    conn.close()


def mandate():
    return OptionMandate(
        strategy="CSP", underlying="NVDA", qty=1,
        delta_band=CONFIG.csp_delta_band, dte_min=4, dte_max=35,
        min_open_interest=300, max_spread_pct_of_mid=0.10, max_strike=376.0,
        client_order_id=make_order_id("hack", "NVDA"))


def test_violation_cancels_and_counts():
    reset_state()
    m = mandate()
    bad = order(f"NVDA{expiry(7)}P00500000", m.client_order_id)  # strike 500 > cap
    trading = FakeTrading([bad])
    trader = make_trader(trading, FakeOptions())
    session_start = datetime.now(timezone.utc) - timedelta(minutes=5)

    result, violations = trader._reconcile_session(m, session_start, None)
    assert not result.ok and result.reason == "guardrail_violation"
    assert any(v.code == "strike_over_cap" for v in violations)
    assert bad.id in trading.cancelled, "violating order must be cancelled"
    print("test_violation_cancels_and_counts OK")


def test_foreign_order_cancelled():
    reset_state()
    m = mandate()
    foreign = order(f"TSLA{expiry(7)}P00300000", "rogue-id-123")
    trading = FakeTrading([foreign])
    trader = make_trader(trading, FakeOptions())
    result, violations = trader._reconcile_session(
        m, datetime.now(timezone.utc) - timedelta(minutes=5), None)
    assert foreign.id in trading.cancelled
    assert any(v.code == "foreign_order_id" for v in violations)
    print("test_foreign_order_cancelled OK")


def test_filled_violation_gets_flattened():
    reset_state()
    m = mandate()
    bad = order(f"NVDA{expiry(60)}P00210000", m.client_order_id,  # dte 60 > 35
                status="filled", filled_qty=1)
    trading = FakeTrading([bad])
    trader = make_trader(trading, FakeOptions())
    result, violations = trader._reconcile_session(
        m, datetime.now(timezone.utc) - timedelta(minutes=5), None)
    assert any(v.code == "dte_out_of_band" for v in violations)
    assert bad.symbol in trading.closed, "filled violating position must be flattened"
    print("test_filled_violation_gets_flattened OK")


def test_kill_switch_after_limit():
    reset_state()
    assert db.llm_is_enabled(MARKET)
    for i in range(CONFIG.llm_violation_limit):
        db.record_llm_violation(MARKET, CONFIG.llm_violation_limit)
    assert not db.llm_is_enabled(MARKET), "kill switch must persist"
    state = db.load_risk_state(MARKET)
    assert state["llm_violations"] == CONFIG.llm_violation_limit
    print("test_kill_switch_after_limit OK")


def test_clean_no_trade():
    reset_state()
    m = mandate()
    trader = make_trader(FakeTrading([]), FakeOptions())
    result, violations = trader._reconcile_session(
        m, datetime.now(timezone.utc), {"action": "no_trade", "thesis": "chain too thin"})
    assert result.ok and result.reason == "no_trade" and not violations
    print("test_clean_no_trade OK")


if __name__ == "__main__":
    test_violation_cancels_and_counts()
    test_foreign_order_cancelled()
    test_filled_violation_gets_flattened()
    test_kill_switch_after_limit()
    test_clean_no_trade()
    reset_state()
    print("ALL RED-TEAM TESTS PASSED")
