"""Fill management and collateral limits.

Day one produced zero fills: the LLM path placed limit orders at mid and
cancelled them 120s later with no second attempt, while each resting order
held collateral and starved the next mandate of buying power.
"""
import os
import sys
from datetime import date, timedelta
from types import SimpleNamespace

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config import Config
from core.mandate import MandateBuilder, OptionMandate, make_order_id
from core.scanner import SignalEvent

CONFIG = Config()


# ── collateral ───────────────────────────────────────────────────────

def ev(price=354.0, rv=0.20):
    return SignalEvent(symbol="JPM", signal="BUY", price=price,
                       info={"confidence": 0.5, "adx": 25, "net_score": 0.3},
                       is_new_bar=True, realized_vol=rv)


def state(**kw):
    s = {"short_puts": {}, "short_put_delta_notional": 0.0,
         "stock_positions": {}, "covered_calls": set(),
         "structures_opened_today": 0, "options_buying_power": 100_000.0}
    s.update(kw)
    return s


def test_full_buying_power_allows_mandate():
    ms = MandateBuilder(CONFIG).build({"JPM": ev()}, 100_000, state())
    assert ms, "should issue with full buying power"
    assert ms[0].max_strike > 300, ms[0].max_strike
    print(f"test_full_buying_power_allows_mandate OK (max_strike={ms[0].max_strike})")


def test_depleted_buying_power_blocks_mandate():
    """The day-one failure: $31k free cannot collateralise a $350 strike."""
    ms = MandateBuilder(CONFIG).build(
        {"JPM": ev()}, 100_000, state(options_buying_power=31_252.0))
    assert ms == [], "must not issue a mandate the broker will reject"
    print("test_depleted_buying_power_blocks_mandate OK")


def test_buying_power_caps_max_strike():
    ms = MandateBuilder(CONFIG).build(
        {"JPM": ev(price=300.0)}, 100_000, state(options_buying_power=40_000.0))
    assert ms, "a $300 name still fits in $40k"
    cap = 40_000 * CONFIG.buying_power_safety / 100
    assert ms[0].max_strike <= cap + 0.01, (ms[0].max_strike, cap)
    print(f"test_buying_power_caps_max_strike OK (max_strike={ms[0].max_strike})")


def test_missing_buying_power_falls_back_to_caps():
    s = state()
    del s["options_buying_power"]
    assert MandateBuilder(CONFIG).build({"JPM": ev()}, 100_000, s), \
        "an unavailable reading must not halt trading"
    print("test_missing_buying_power_falls_back_to_caps OK")


# ── repricing ────────────────────────────────────────────────────────

class FakeTrading:
    def __init__(self):
        self.submitted = []
        self.cancelled = []

    def submit_order(self, req):
        self.submitted.append(req)
        return SimpleNamespace(id=f"oid{len(self.submitted)}",
                               client_order_id=req.client_order_id,
                               symbol=req.symbol, status="accepted")

    def cancel_order_by_id(self, oid):
        self.cancelled.append(oid)


class FakeOptions:
    def __init__(self, quote):
        self.quote = quote

    def get_quotes(self, symbols):
        return {s: self.quote for s in symbols} if self.quote else {}


def trader(trading, options):
    from agent.llm_trader import LLMTrader
    t = object.__new__(LLMTrader)
    t.config, t.ledger, t.options, t.trading = CONFIG, None, options, trading
    t.market = "TEST"
    return t


def mandate(strategy="CSP"):
    return OptionMandate(
        strategy=strategy, underlying="AAPL", qty=1, delta_band=(0.20, 0.35),
        dte_min=4, dte_max=35, min_open_interest=300,
        max_spread_pct_of_mid=0.10, max_strike=350.0,
        client_order_id=make_order_id("hack", "AAPL"))


def order():
    return SimpleNamespace(id="oid0", symbol="AAPL260904P00310000",
                           client_order_id="hack-x", qty=1)


def test_reprice_sells_at_the_bid():
    """Selling at mid does not fill; the bid does."""
    t = FakeTrading()
    m = mandate()
    new = trader(t, FakeOptions({"bid": 1.60, "ask": 1.86})).\
        _reprice(order(), m)
    assert new is not None
    req = t.submitted[0]
    assert float(req.limit_price) == 1.60, req.limit_price
    assert req.client_order_id.startswith(m.client_order_id), \
        "reprice must stay recognisable to verification"
    print("test_reprice_sells_at_the_bid OK")


def test_reprice_buys_at_the_ask():
    t = FakeTrading()
    trader(t, FakeOptions({"bid": 1.60, "ask": 1.86}))._reprice(
        order(), mandate(strategy="LONG_CALL"))
    assert float(t.submitted[0].limit_price) == 1.86
    print("test_reprice_buys_at_the_ask OK")


def test_no_reprice_on_dead_quote():
    t = FakeTrading()
    assert trader(t, FakeOptions({"bid": 0.0, "ask": 1.0}))._reprice(
        order(), mandate()) is None
    assert not t.submitted, "must not chase a market with no bid"
    print("test_no_reprice_on_dead_quote OK")


if __name__ == "__main__":
    test_full_buying_power_allows_mandate()
    test_depleted_buying_power_blocks_mandate()
    test_buying_power_caps_max_strike()
    test_missing_buying_power_falls_back_to_caps()
    test_reprice_sells_at_the_bid()
    test_reprice_buys_at_the_ask()
    test_no_reprice_on_dead_quote()
    print("ALL FILL/COLLATERAL TESTS PASSED")
