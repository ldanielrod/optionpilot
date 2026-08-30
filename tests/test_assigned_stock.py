"""Assigned-stock exit path.

Regression guard for a seam bug: exits.py skipped stock ("handled by the CC
mandate") while mandate.py skipped bearish underlyings ("we're exiting the
stock instead"). Neither actually exited, leaving assigned shares — 20-30% of
this account — completely unmanaged.
"""
import os
import sys
from types import SimpleNamespace

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config import CONFIG
from core.exits import ExitManager
from core.scanner import SignalEvent


class FakeTrading:
    def __init__(self, positions):
        self._positions = positions
        self.closed = []

    def get_all_positions(self):
        return self._positions

    def close_position(self, symbol):
        self.closed.append(symbol)


class FailingOnCall(FakeTrading):
    def close_position(self, symbol):
        if not symbol.isalpha():          # the option leg
            raise RuntimeError("broker rejected")
        self.closed.append(symbol)


def stock(symbol="NVDA", qty=100, plpc=0.0):
    return SimpleNamespace(symbol=symbol, qty=str(qty), unrealized_plpc=str(plpc),
                           current_price="210.0", avg_entry_price="210.0")


def short_call(symbol="NVDA260918C00230000", qty=-1):
    return SimpleNamespace(symbol=symbol, qty=str(qty), unrealized_plpc="0",
                           current_price="1.0", avg_entry_price="1.2")


def ev(signal):
    return SignalEvent(symbol="NVDA", signal=signal, price=210.0,
                       info={}, realized_vol=0.30)


def mgr(trading):
    return ExitManager(trading, options_data=None, config=CONFIG, ledger=None)


def test_sell_signal_liquidates():
    t = FakeTrading([stock()])
    closed = mgr(t).manage_assigned_stock({"NVDA": ev("SELL")}, execute=True)
    assert closed == ["NVDA"] and t.closed == ["NVDA"], (closed, t.closed)
    print("test_sell_signal_liquidates OK")


def test_bullish_signal_holds():
    t = FakeTrading([stock()])
    assert mgr(t).manage_assigned_stock({"NVDA": ev("BUY")}, execute=True) == []
    assert t.closed == []
    print("test_bullish_signal_holds OK")


def test_stop_loss_liquidates_without_signal():
    t = FakeTrading([stock(plpc=-0.12)])   # -12% vs 8% stop
    closed = mgr(t).manage_assigned_stock({}, execute=True)
    assert closed == ["NVDA"] and t.closed == ["NVDA"]
    print("test_stop_loss_liquidates_without_signal OK")


def test_covered_call_closed_before_stock():
    t = FakeTrading([stock(), short_call()])
    mgr(t).manage_assigned_stock({"NVDA": ev("SELL")}, execute=True)
    assert t.closed == ["NVDA260918C00230000", "NVDA"], t.closed
    print("test_covered_call_closed_before_stock OK")


def test_stock_kept_if_call_cannot_be_closed():
    """Never leave a naked short call behind."""
    t = FailingOnCall([stock(), short_call()])
    closed = mgr(t).manage_assigned_stock({"NVDA": ev("SELL")}, execute=True)
    assert "NVDA" not in t.closed, "stock must not be sold under a live call"
    assert closed == [], closed
    print("test_stock_kept_if_call_cannot_be_closed OK")


def test_dry_run_does_not_trade():
    t = FakeTrading([stock()])
    closed = mgr(t).manage_assigned_stock({"NVDA": ev("SELL")}, execute=False)
    assert closed == ["NVDA"] and t.closed == []
    print("test_dry_run_does_not_trade OK")


if __name__ == "__main__":
    test_sell_signal_liquidates()
    test_bullish_signal_holds()
    test_stop_loss_liquidates_without_signal()
    test_covered_call_closed_before_stock()
    test_stock_kept_if_call_cannot_be_closed()
    test_dry_run_does_not_trade()
    print("ALL ASSIGNED-STOCK TESTS PASSED")
