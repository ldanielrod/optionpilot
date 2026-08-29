"""Unit tests: OCC parsing, decision schema, and mandate validation."""
import os
import sys
from datetime import date, timedelta
from types import SimpleNamespace

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agent.decision_schema import parse_decision
from core.guardrails import validate_order_against_mandate
from core.mandate import OptionMandate
from core.occ import parse_occ


def make_mandate(**kw):
    base = dict(strategy="CSP", underlying="NVDA", qty=1,
                delta_band=(0.20, 0.35), dte_min=4, dte_max=35,
                min_open_interest=300, max_spread_pct_of_mid=0.10,
                max_strike=376.0, client_order_id="hack-20260829-NVDA-abc12345")
    base.update(kw)
    return OptionMandate(**base)


def occ_for(underlying="NVDA", days=7, cp="P", strike=220.0):
    d = date.today() + timedelta(days=days)
    return f"{underlying}{d:%y%m%d}{cp}{int(strike * 1000):08d}"


def make_order(symbol=None, cid="hack-20260829-NVDA-abc12345", qty=1,
               side="OrderSide.SELL", otype="limit", limit_price=1.85):
    return SimpleNamespace(symbol=symbol or occ_for(), client_order_id=cid,
                           qty=qty, side=side, type=otype,
                           limit_price=limit_price)


def test_occ_parse():
    p = parse_occ("NVDA260904P00220000")
    assert p.underlying == "NVDA" and p.contract_type == "put"
    assert p.strike == 220.0 and p.expiry == date(2026, 9, 4)
    assert parse_occ("NVDA") is None
    assert parse_occ("AAPL240119C00100000").strike == 100.0
    print("test_occ_parse OK")


def test_decision_schema():
    good = '''blah blah
```json
{"action": "placed", "occ_symbol": "NVDA260904P00220000", "strategy": "CSP",
 "qty": 1, "limit_price": 1.84, "thesis": "good theta"}
```'''
    d, err = parse_decision(good)
    assert err is None and d["qty"] == 1
    d, err = parse_decision("no json here")
    assert d is None
    d, err = parse_decision('```json\n{"action": "no_trade", "thesis": "chain too thin"}\n```')
    assert err is None and d["action"] == "no_trade"
    d, err = parse_decision('```json\n{"action": "placed", "qty": 1}\n```')
    assert d is None and "missing" in err
    print("test_decision_schema OK")


def test_clean_order_passes():
    v = validate_order_against_mandate(
        make_order(), make_mandate(),
        quote={"bid": 1.80, "ask": 1.90, "delta": -0.27})
    assert v == [], v
    print("test_clean_order_passes OK")


def test_violations_caught():
    m = make_mandate()
    cases = {
        "foreign_order_id": make_order(cid="my-own-id-123"),
        "wrong_underlying": make_order(symbol=occ_for(underlying="TSLA")),
        "wrong_contract_type": make_order(symbol=occ_for(cp="C")),
        "oversize": make_order(qty=5),
        "wrong_side": make_order(side="OrderSide.BUY"),
        "not_limit": make_order(otype="market"),
        "dte_out_of_band": make_order(symbol=occ_for(days=60)),
        "strike_over_cap": make_order(symbol=occ_for(strike=500.0)),
    }
    for expected, order in cases.items():
        codes = [v.code for v in validate_order_against_mandate(order, m)]
        assert expected in codes, f"{expected} not caught: {codes}"

    # delta out of band + price off market (need quote)
    v = validate_order_against_mandate(
        make_order(limit_price=9.99), m,
        quote={"bid": 1.80, "ask": 1.90, "delta": -0.55})
    codes = [x.code for x in v]
    assert "delta_out_of_band" in codes and "price_off_market" in codes, codes
    print("test_violations_caught OK")


if __name__ == "__main__":
    test_occ_parse()
    test_decision_schema()
    test_clean_order_passes()
    test_violations_caught()
    print("ALL GUARDRAIL TESTS PASSED")
