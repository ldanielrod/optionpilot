"""Deterministic executor: fulfills an OptionMandate without any LLM.

Selection rule: among contracts passing every mandate constraint, pick the one
whose |delta| is closest to the midpoint of the mandated band (tie-break:
higher open interest). Orders are limit at mid; if unfilled after
FILL_WAIT_S the order is cancelled and resubmitted once at the marketable
side of the book (bid for sells, ask for buys).

This is the day-1 MVP path and the permanent fallback when the LLM layer is
disabled by the kill switch.
"""
import time
from dataclasses import dataclass
from typing import List, Optional

from alpaca.trading.client import TradingClient
from alpaca.trading.requests import LimitOrderRequest
from alpaca.trading.enums import OrderSide, TimeInForce

from core.mandate import OptionMandate
from data.options import OptionsData, ContractQuote

FILL_WAIT_S = 75
REPRICE_WAIT_S = 60
TERMINAL = {"filled", "canceled", "expired", "rejected"}


@dataclass
class ExecutionResult:
    ok: bool
    reason: str
    occ_symbol: Optional[str] = None
    order_id: Optional[str] = None
    client_order_id: Optional[str] = None
    filled_qty: float = 0.0
    fill_price: Optional[float] = None
    delta: Optional[float] = None
    dte: Optional[int] = None
    status: Optional[str] = None


def select_contract(candidates: List[ContractQuote],
                    mandate: OptionMandate) -> Optional[ContractQuote]:
    lo, hi = mandate.delta_band
    target = (lo + hi) / 2
    viable = []
    for c in candidates:
        if c.delta is None:
            continue
        d = abs(c.delta)
        if not (lo <= d <= hi):
            continue
        if c.spread_pct_of_mid > mandate.max_spread_pct_of_mid and c.spread_abs > 0.15:
            continue
        if mandate.max_strike is not None and c.strike > mandate.max_strike:
            continue
        viable.append(c)
    if not viable:
        return None
    viable.sort(key=lambda c: (abs(abs(c.delta) - target), -c.open_interest))
    return viable[0]


class DirectExecutor:
    def __init__(self, api_key: str, api_secret: str, options_data: OptionsData,
                 paper: bool = True):
        self.trading = TradingClient(api_key, api_secret, paper=paper)
        self.options = options_data

    def execute(self, mandate: OptionMandate, execute: bool = True) -> ExecutionResult:
        contract_type = "put" if mandate.strategy == "CSP" else "call"
        side = OrderSide.BUY if mandate.strategy == "LONG_CALL" else OrderSide.SELL

        candidates = self.options.get_candidates(
            mandate.underlying, contract_type,
            mandate.dte_min, mandate.dte_max, mandate.min_open_interest)
        pick = select_contract(candidates, mandate)
        if pick is None:
            return ExecutionResult(ok=False, reason="no_contract_in_mandate")

        limit = round(pick.mid, 2)
        if not execute:
            return ExecutionResult(
                ok=True, reason="dry_run", occ_symbol=pick.occ_symbol,
                client_order_id=mandate.client_order_id, fill_price=limit,
                delta=pick.delta, dte=pick.dte, status="dry_run")

        try:
            order = self.trading.submit_order(LimitOrderRequest(
                symbol=pick.occ_symbol, qty=mandate.qty, side=side,
                time_in_force=TimeInForce.DAY, limit_price=limit,
                client_order_id=mandate.client_order_id))
        except Exception as e:
            return ExecutionResult(ok=False, reason=f"submit_failed: {e}",
                                   occ_symbol=pick.occ_symbol)

        final = self._wait_fill(order.id, FILL_WAIT_S)
        if final and str(final.status).lower().endswith("filled"):
            return self._result_from_order(final, pick, mandate)

        # one reprice at the marketable side of the book
        try:
            self.trading.cancel_order_by_id(order.id)
            time.sleep(1.5)
            fresh = self.options.get_quotes([pick.occ_symbol]).get(pick.occ_symbol)
            reprice = round((fresh["bid"] if side == OrderSide.SELL else fresh["ask"])
                            if fresh else limit, 2)
            if reprice <= 0:
                return ExecutionResult(ok=False, reason="dead_quote_on_reprice",
                                       occ_symbol=pick.occ_symbol)
            order2 = self.trading.submit_order(LimitOrderRequest(
                symbol=pick.occ_symbol, qty=mandate.qty, side=side,
                time_in_force=TimeInForce.DAY, limit_price=reprice,
                client_order_id=f"{mandate.client_order_id}-r1"))
        except Exception as e:
            return ExecutionResult(ok=False, reason=f"reprice_failed: {e}",
                                   occ_symbol=pick.occ_symbol)

        final2 = self._wait_fill(order2.id, REPRICE_WAIT_S)
        if final2 and str(final2.status).lower().endswith("filled"):
            return self._result_from_order(final2, pick, mandate)

        # leave nothing resting: an unfilled entry is a skipped trade
        try:
            self.trading.cancel_order_by_id(order2.id)
        except Exception:
            pass
        return ExecutionResult(ok=False, reason="unfilled_after_reprice",
                               occ_symbol=pick.occ_symbol,
                               status=str(final2.status) if final2 else None)

    def _wait_fill(self, order_id, seconds: int):
        deadline = time.time() + seconds
        last = None
        while time.time() < deadline:
            try:
                last = self.trading.get_order_by_id(order_id)
                s = str(last.status).lower()
                if any(s.endswith(t) for t in TERMINAL):
                    return last
            except Exception as e:
                print(f"[executor] poll error: {e}")
            time.sleep(3)
        return last

    @staticmethod
    def _result_from_order(order, pick: ContractQuote,
                           mandate: OptionMandate) -> ExecutionResult:
        return ExecutionResult(
            ok=True, reason="filled", occ_symbol=pick.occ_symbol,
            order_id=str(order.id), client_order_id=order.client_order_id,
            filled_qty=float(order.filled_qty or 0),
            fill_price=float(order.filled_avg_price or 0),
            delta=pick.delta, dte=pick.dte, status=str(order.status))
