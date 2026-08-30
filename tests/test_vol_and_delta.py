"""IV floor (volatility risk premium) and aggregate delta cap."""
import os
import sys
from datetime import date, timedelta

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config import CONFIG
from core.executor_direct import select_contract
from core.mandate import MandateBuilder, OptionMandate, make_order_id
from core.scanner import SignalEvent, realized_volatility
from data.options import ContractQuote


def bars(vol_daily, n=60, start=100.0):
    rng = np.random.default_rng(7)
    rets = rng.normal(0, vol_daily, n)
    closes = start * np.exp(np.cumsum(rets))
    return pd.DataFrame({
        "timestamp": pd.date_range("2026-06-01", periods=n, freq="D"),
        "close": closes,
    })


def test_realized_vol():
    # 2% daily vol -> ~32% annualized (0.02 * sqrt(252))
    rv = realized_volatility(bars(0.02), 20)
    assert rv is not None and 0.20 < rv < 0.50, rv
    quiet = realized_volatility(bars(0.005), 20)
    assert quiet < rv, "quiet tape must show lower realized vol"
    assert realized_volatility(bars(0.02, n=5), 20) is None, "needs enough bars"
    print(f"test_realized_vol OK (rv={rv:.1%}, quiet={quiet:.1%})")


def contract(iv, delta=-0.27, strike=200.0, oi=1000):
    return ContractQuote(
        occ_symbol=f"NVDA260918P00{int(strike*1000):06d}", underlying="NVDA",
        contract_type="put", strike=strike,
        expiry=date.today() + timedelta(days=14), dte=14, open_interest=oi,
        bid=2.0, ask=2.1, mid=2.05, spread_abs=0.10,
        spread_pct_of_mid=0.05, delta=delta, iv=iv)


def mandate(min_iv=None):
    return OptionMandate(
        strategy="CSP", underlying="NVDA", qty=1, delta_band=(0.20, 0.35),
        dte_min=4, dte_max=35, min_open_interest=300,
        max_spread_pct_of_mid=0.10, max_strike=350.0,
        client_order_id=make_order_id("hack", "NVDA"), min_iv=min_iv)


def test_iv_floor_rejects_cheap_vol():
    m = mandate(min_iv=0.35)
    assert select_contract([contract(iv=0.28)], m) is None, \
        "IV below floor must be rejected"
    assert select_contract([contract(iv=0.40)], m) is not None
    assert select_contract([contract(iv=None)], m) is None, \
        "unknown IV must not pass a floor check"
    # no floor set -> IV is not consulted
    assert select_contract([contract(iv=0.10)], mandate(None)) is not None
    print("test_iv_floor_rejects_cheap_vol OK")


def event(price=200.0, rv=0.30, signal="BUY"):
    return SignalEvent(symbol="NVDA", signal=signal, price=price,
                       info={"confidence": 0.5, "adx": 30, "net_score": 0.4},
                       is_new_bar=True, realized_vol=rv)


def base_state(**kw):
    s = {"short_puts": {}, "short_put_delta_notional": 0.0,
         "stock_positions": {}, "covered_calls": set(),
         "structures_opened_today": 0}
    s.update(kw)
    return s


def test_mandate_sets_iv_floor():
    b = MandateBuilder(CONFIG)
    ms = b.build({"NVDA": event(rv=0.30)}, 100_000, base_state())
    assert ms and ms[0].min_iv is not None
    expected = 0.30 * CONFIG.min_iv_over_realized
    assert abs(ms[0].min_iv - expected) < 1e-3, ms[0].min_iv
    print(f"test_mandate_sets_iv_floor OK (floor={ms[0].min_iv:.1%})")


def test_aggregate_delta_cap_blocks():
    equity = 100_000
    cap = equity * CONFIG.max_aggregate_delta_pct     # 25,000
    # a 0.275-delta put on a $200 name adds ~5,500 of long delta
    b = MandateBuilder(CONFIG)
    assert b.build({"NVDA": event()}, equity,
                   base_state(short_put_delta_notional=0)) , "should fit when empty"

    b2 = MandateBuilder(CONFIG)
    blocked = b2.build({"NVDA": event()}, equity,
                       base_state(short_put_delta_notional=cap - 100))
    assert blocked == [], "must block when the cap would be exceeded"
    print("test_aggregate_delta_cap_blocks OK")


def test_income_csp_needs_vol_estimate():
    b = MandateBuilder(CONFIG)
    ev = event(rv=None, signal="HOLD")
    ms = b.build({"NVDA": ev}, 100_000, base_state())
    assert ms == [], "income CSP without a realized-vol estimate must not fire"
    print("test_income_csp_needs_vol_estimate OK")


if __name__ == "__main__":
    test_realized_vol()
    test_iv_floor_rejects_cheap_vol()
    test_mandate_sets_iv_floor()
    test_aggregate_delta_cap_blocks()
    test_income_csp_needs_vol_estimate()
    print("ALL VOL/DELTA TESTS PASSED")
