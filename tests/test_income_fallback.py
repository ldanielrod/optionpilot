"""The premium-harvesting branch must walk its ranking, not stop at the top.

Day two: JPM ranked first on ADX, was refused twice for collateral, and the
branch gave up — leaving the book idle with room for a cheaper name.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config import Config
from core.mandate import MandateBuilder
from core.scanner import SignalEvent

CONFIG = Config()


def hold(symbol, price, adx, rv=0.25, net=0.1):
    return SignalEvent(symbol=symbol, signal="HOLD", price=price,
                       info={"confidence": 0.4, "adx": adx, "net_score": net},
                       is_new_bar=True, realized_vol=rv)


def state(**kw):
    s = {"short_puts": {}, "short_put_delta_notional": 0.0,
         "stock_positions": {}, "covered_calls": set(),
         "structures_opened_today": 0, "options_buying_power": 100_000.0}
    s.update(kw)
    return s


def test_falls_through_to_affordable_name():
    """Top-ranked JPM cannot be collateralised; NVDA behind it can."""
    events = {"JPM": hold("JPM", 354.0, adx=30),      # needs ~$33k
              "NVDA": hold("NVDA", 210.0, adx=25)}    # needs ~$20k
    # one open put already consumes most of the 60% notional budget
    ms = MandateBuilder(CONFIG).build(
        events, 100_000, state(short_puts={"AAPL": 31_000.0}))
    assert len(ms) == 1, ms
    assert ms[0].underlying == "NVDA", \
        f"should fall through to the affordable name, got {ms[0].underlying}"
    print("test_falls_through_to_affordable_name OK")


def test_prefers_top_of_ranking_when_it_fits():
    events = {"JPM": hold("JPM", 354.0, adx=30),
              "NVDA": hold("NVDA", 210.0, adx=25)}
    ms = MandateBuilder(CONFIG).build(events, 100_000, state())
    assert ms and ms[0].underlying == "JPM", \
        "ranking still decides when the top name is viable"
    print("test_prefers_top_of_ranking_when_it_fits")


def test_issues_nothing_when_no_candidate_fits():
    events = {"MSFT": hold("MSFT", 513.0, adx=30),
              "META": hold("META", 578.0, adx=25)}
    assert MandateBuilder(CONFIG).build(events, 100_000, state()) == [], \
        "walking the list must not lower the bar"
    print("test_issues_nothing_when_no_candidate_fits OK")


def test_only_one_income_mandate_per_cycle():
    events = {"JPM": hold("JPM", 354.0, adx=30),
              "NVDA": hold("NVDA", 210.0, adx=25),
              "AMZN": hold("AMZN", 240.0, adx=20)}
    ms = MandateBuilder(CONFIG).build(events, 100_000, state())
    assert len(ms) == 1, f"one at a time, got {len(ms)}"
    print("test_only_one_income_mandate_per_cycle OK")


if __name__ == "__main__":
    test_falls_through_to_affordable_name()
    test_prefers_top_of_ranking_when_it_fits()
    test_issues_nothing_when_no_candidate_fits()
    test_only_one_income_mandate_per_cycle()
    print("ALL INCOME-FALLBACK TESTS PASSED")
