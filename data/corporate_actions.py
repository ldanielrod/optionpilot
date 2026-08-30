"""Upcoming corporate actions for the universe, from Alpaca.

Two things here matter to an options seller, and they matter differently:

  - **Splits** rewrite the contract. The result is a non-standard deliverable
    with poor liquidity and confusing assignment. Never open into one.
  - **Ex-dividend dates** create early-assignment risk on short CALLS: when a
    call's extrinsic value drops below the dividend, exercising early to
    capture it becomes rational for the holder. For short PUTS the effect is
    the ordinary price adjustment, already in the option's price — that is
    context for the selector, not grounds to refuse the trade.

Alpaca's corporate actions feed does NOT include earnings announcements
(the available types are splits, dividends, mergers, spin-offs, redemptions,
name changes and rights distributions), so the earnings blackout remains a
separately maintained control. See Config.earnings_dates.
"""
from datetime import date, timedelta
from typing import Dict, List, Optional

from alpaca.data.historical.corporate_actions import CorporateActionsClient
from alpaca.data.requests import CorporateActionsRequest
from alpaca.data.enums import CorporateActionsType

SPLIT_TYPES = [CorporateActionsType.FORWARD_SPLIT,
               CorporateActionsType.REVERSE_SPLIT,
               CorporateActionsType.UNIT_SPLIT]
DIVIDEND_TYPES = [CorporateActionsType.CASH_DIVIDEND,
                  CorporateActionsType.STOCK_DIVIDEND]


class CorporateActions:
    def __init__(self, api_key: str, api_secret: str, lookahead_days: int = 45):
        self.client = CorporateActionsClient(api_key, api_secret)
        self.lookahead_days = lookahead_days
        self._cached_on: Optional[date] = None
        self._splits: Dict[str, List[date]] = {}
        self._ex_divs: Dict[str, List[date]] = {}

    def refresh(self, symbols: List[str], force: bool = False) -> None:
        """Fetch once per calendar day; announcements do not change intraday."""
        today = date.today()
        if not force and self._cached_on == today:
            return
        try:
            resp = self.client.get_corporate_actions(CorporateActionsRequest(
                symbols=list(symbols),
                types=SPLIT_TYPES + DIVIDEND_TYPES,
                start=today,
                end=today + timedelta(days=self.lookahead_days)))
        except Exception as e:
            # Never fail the trading loop over this; an empty map means the
            # checks below simply do not fire, and that is logged upstream.
            print(f"[corp_actions] fetch failed: {e}")
            return

        data = getattr(resp, "data", resp)
        splits: Dict[str, List[date]] = {}
        ex_divs: Dict[str, List[date]] = {}
        for kind, items in (data or {}).items():
            bucket = splits if "split" in kind else ex_divs
            for item in items:
                sym = getattr(item, "symbol", None)
                ex = getattr(item, "ex_date", None) or getattr(item, "process_date", None)
                if sym and ex:
                    bucket.setdefault(sym, []).append(ex)
        self._splits, self._ex_divs, self._cached_on = splits, ex_divs, today
        if splits or ex_divs:
            print(f"[corp_actions] splits={ {k: [str(d) for d in v] for k, v in splits.items()} } "
                  f"ex_div={ {k: [str(d) for d in v] for k, v in ex_divs.items()} }")

    def split_before(self, symbol: str, before: date) -> Optional[date]:
        for d in self._splits.get(symbol, []):
            if date.today() <= d <= before:
                return d
        return None

    def ex_dividend_before(self, symbol: str, before: date) -> Optional[date]:
        for d in self._ex_divs.get(symbol, []):
            if date.today() <= d <= before:
                return d
        return None
