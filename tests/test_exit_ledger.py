"""Exits must land in the trades ledger with realized P&L, and transient LLM
failures must be retried.

Day three closed a profitable round trip that existed only as a broker equity
number: the close was written to `decisions` and never to `trades`, so the
report and the attribution had no realized result to read. Two sessions were
also lost to 529 Overloaded, an error the API itself calls temporary.
"""
import os
import sys
from types import SimpleNamespace

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config import Config
from core.exits import ExitAction, ExitManager

CONFIG = Config()


class FakeLedger:
    def __init__(self):
        self.trades = []
        self.decisions = []

    def log_trade(self, **kw):
        self.trades.append(kw)
        return len(self.trades)

    def log_decision(self, *a, **kw):
        self.decisions.append(kw or a)


class FakeTrading:
    def __init__(self, fill_price="1.39"):
        self.fill_price = fill_price
        self.submitted = []

    def submit_order(self, req):
        self.submitted.append(req)
        return SimpleNamespace(id="oid1", client_order_id=req.client_order_id)

    def get_order_by_id(self, oid):
        return SimpleNamespace(id=oid, filled_avg_price=self.fill_price,
                               status="filled")

    def close_position(self, symbol):
        return SimpleNamespace(id="oid2", client_order_id="cp")


def short_put_position():
    return SimpleNamespace(symbol="AAPL260918P00310000", qty="-1",
                           avg_entry_price="2.82", current_price="1.39",
                           unrealized_plpc="0")


def test_exit_writes_trade_with_realized_pnl():
    """The real round trip: sold at 2.82, bought back at 1.39."""
    led, tr = FakeLedger(), FakeTrading("1.39")
    mgr = ExitManager(tr, options_data=None, config=CONFIG, ledger=led)
    action = ExitAction(occ_symbol="AAPL260918P00310000", reason="profit_take",
                        qty=1, side="buy_to_close")
    mgr._close(short_put_position(), action, mark=1.41)

    assert len(led.trades) == 1, "the close must reach the trades ledger"
    t = led.trades[0]
    assert t["side"] == "buy_to_close" and t["symbol"] == "AAPL"
    assert abs(t["price"] - 1.39) < 1e-6, t["price"]
    assert abs(t["pnl"] - 143.0) < 0.01, f"expected +143, got {t['pnl']}"
    assert t["reason"] == "profit_take"
    print(f"test_exit_writes_trade_with_realized_pnl OK (pnl=${t['pnl']:.2f})")


def test_uses_actual_fill_not_the_mark():
    led, tr = FakeLedger(), FakeTrading("1.50")
    mgr = ExitManager(tr, None, CONFIG, led)
    mgr._close(short_put_position(),
               ExitAction("AAPL260918P00310000", "profit_take", 1, "buy_to_close"),
               mark=1.41)
    assert abs(led.trades[0]["price"] - 1.50) < 1e-6, "must record the fill"
    assert abs(led.trades[0]["pnl"] - 132.0) < 0.01
    print("test_uses_actual_fill_not_the_mark OK")


def test_falls_back_to_mark_when_unfilled():
    class Unfilled(FakeTrading):
        def get_order_by_id(self, oid):
            return SimpleNamespace(id=oid, filled_avg_price=None,
                                   status="canceled")
    led = FakeLedger()
    mgr = ExitManager(Unfilled(), None, CONFIG, led)
    mgr._close(short_put_position(),
               ExitAction("AAPL260918P00310000", "profit_take", 1, "buy_to_close"),
               mark=1.41)
    assert led.trades and abs(led.trades[0]["price"] - 1.41) < 1e-6
    print("test_falls_back_to_mark_when_unfilled OK")


# ── transient LLM retry ──────────────────────────────────────────────

def make_trader(side_effects):
    from agent import llm_trader as mod
    t = object.__new__(mod.LLMTrader)
    t.config = CONFIG
    calls = {"n": 0}

    def fake_run(prompt, execute):
        i = calls["n"]
        calls["n"] += 1
        outcome = side_effects[min(i, len(side_effects) - 1)]
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    import asyncio
    original = asyncio.run
    asyncio.run = lambda coro: coro          # coro is already the value/raise
    t._run_session = fake_run
    mod.TRANSIENT_BACKOFF_S = 0
    return t, calls, (asyncio, original)


def test_retries_on_529():
    t, calls, (aio, orig) = make_trader(
        [RuntimeError("API Error: 529 Overloaded"), ("ok", 0.5, 3)])
    try:
        text, cost, turns = t._session_with_retry("p", True)
        assert text == "ok" and calls["n"] == 2, calls
    finally:
        aio.run = orig
    print("test_retries_on_529 OK")


def test_does_not_retry_real_errors():
    t, calls, (aio, orig) = make_trader([ValueError("bad mandate")])
    try:
        raised = False
        try:
            t._session_with_retry("p", True)
        except ValueError:
            raised = True
        assert raised and calls["n"] == 1, "a real error must fail immediately"
    finally:
        aio.run = orig
    print("test_does_not_retry_real_errors OK")


def test_gives_up_after_limit():
    from agent import llm_trader as mod
    t, calls, (aio, orig) = make_trader([RuntimeError("529 overloaded")])
    try:
        raised = False
        try:
            t._session_with_retry("p", True)
        except RuntimeError:
            raised = True
        assert raised and calls["n"] == mod.TRANSIENT_RETRIES, calls
    finally:
        aio.run = orig
    print("test_gives_up_after_limit OK")


if __name__ == "__main__":
    test_exit_writes_trade_with_realized_pnl()
    test_uses_actual_fill_not_the_mark()
    test_falls_back_to_mark_when_unfilled()
    test_retries_on_529()
    test_does_not_retry_real_errors()
    test_gives_up_after_limit()
    print("ALL EXIT-LEDGER / RETRY TESTS PASSED")
