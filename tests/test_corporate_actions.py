"""Corporate-action and earnings blocks in the mandate builder.

Splits are refused outright (the contract becomes a non-standard deliverable).
Ex-dividends block covered calls only — a short call across an ex-date carries
early-assignment risk, a short put does not.
"""
import os
import sys
from dataclasses import replace
from datetime import date, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config import Config
from core.mandate import MandateBuilder
from core.scanner import SignalEvent


class FakeCorp:
    def __init__(self, splits=None, ex_divs=None):
        self.splits = splits or {}
        self.ex_divs = ex_divs or {}
        self.refreshed = False

    def refresh(self, symbols, force=False):
        self.refreshed = True

    def split_before(self, symbol, before):
        d = self.splits.get(symbol)
        return d if d and date.today() <= d <= before else None

    def ex_dividend_before(self, symbol, before):
        d = self.ex_divs.get(symbol)
        return d if d and date.today() <= d <= before else None


def ev(signal="BUY", price=200.0, rv=0.30):
    return SignalEvent(symbol="NVDA", signal=signal, price=price,
                       info={"confidence": 0.5, "adx": 30, "net_score": 0.4},
                       is_new_bar=True, realized_vol=rv)


def state(**kw):
    s = {"short_puts": {}, "short_put_delta_notional": 0.0,
         "stock_positions": {}, "covered_calls": set(),
         "structures_opened_today": 0}
    s.update(kw)
    return s


def test_split_blocks_csp():
    corp = FakeCorp(splits={"NVDA": date.today() + timedelta(days=10)})
    b = MandateBuilder(Config(), None, corp)
    assert b.build({"NVDA": ev()}, 100_000, state()) == []
    assert corp.refreshed, "builder must refresh corporate actions"
    print("test_split_blocks_csp OK")


def test_distant_split_does_not_block():
    corp = FakeCorp(splits={"NVDA": date.today() + timedelta(days=200)})
    assert MandateBuilder(Config(), None, corp).build(
        {"NVDA": ev()}, 100_000, state()), "split beyond the horizon is irrelevant"
    print("test_distant_split_does_not_block OK")


def test_ex_dividend_does_not_block_csp():
    """A short put across an ex-date is priced for it; it is context, not a veto."""
    ex = date.today() + timedelta(days=10)
    corp = FakeCorp(ex_divs={"NVDA": ex})
    ms = MandateBuilder(Config(), None, corp).build({"NVDA": ev()}, 100_000, state())
    assert ms, "CSP must still be issued"
    assert ms[0].signal_context["ex_dividend"] == str(ex), \
        "ex-dividend must reach the selector as context"
    print("test_ex_dividend_does_not_block_csp OK")


def test_ex_dividend_blocks_covered_call():
    """A short call across an ex-date can be assigned early to capture it."""
    corp = FakeCorp(ex_divs={"NVDA": date.today() + timedelta(days=10)})
    b = MandateBuilder(Config(), None, corp)
    ms = b.build({"NVDA": ev(signal="HOLD")}, 100_000,
                 state(stock_positions={"NVDA": 100}))
    assert not any(m.strategy == "CC" for m in ms), \
        "covered call must be blocked before an ex-dividend"
    print("test_ex_dividend_blocks_covered_call OK")


def test_earnings_blocks():
    cfg = replace(Config(),
                  earnings_dates={"NVDA": str(date.today() + timedelta(days=6))})
    assert MandateBuilder(cfg, None, FakeCorp()).build(
        {"NVDA": ev()}, 100_000, state()) == []
    print("test_earnings_blocks OK")


def test_works_without_corporate_actions():
    """The feed is best-effort; its absence must not stop trading."""
    assert MandateBuilder(Config(), None, None).build(
        {"NVDA": ev()}, 100_000, state())
    print("test_works_without_corporate_actions OK")


if __name__ == "__main__":
    test_split_blocks_csp()
    test_distant_split_does_not_block()
    test_ex_dividend_does_not_block_csp()
    test_ex_dividend_blocks_covered_call()
    test_earnings_blocks()
    test_works_without_corporate_actions()
    print("ALL CORPORATE-ACTION TESTS PASSED")
