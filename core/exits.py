"""Exit management for open option positions.

Rules (config-driven):
  - short options: buy-to-close at >= short_profit_take_pct of collected premium,
    stop out when the option trades at short_stop_loss_mult x entry premium
  - long (debit) options: sell-to-close at +debit_profit_take_pct
  - anything at <= force_close_dte days to expiry is closed after
    force_close_hour_et:force_close_minute_et ET (no assignment mechanics on
    judging days)

Entry premium comes from the position's avg_entry_price (broker truth), so
exits work even for positions the LLM opened.
"""
import time
from dataclasses import dataclass
from datetime import datetime
from typing import List, Optional
from zoneinfo import ZoneInfo

from alpaca.trading.client import TradingClient
from alpaca.trading.requests import LimitOrderRequest
from alpaca.trading.enums import OrderSide, TimeInForce

import notify
from core.occ import parse_occ

ET = ZoneInfo("America/New_York")


@dataclass
class ExitAction:
    occ_symbol: str
    reason: str          # profit_take | stop_loss | expiry_close
    qty: float
    side: str            # buy_to_close | sell_to_close


class ExitManager:
    def __init__(self, trading: TradingClient, options_data, config, ledger):
        self.trading = trading
        self.options = options_data
        self.config = config
        self.ledger = ledger

    def check_and_execute(self, execute: bool = True) -> List[ExitAction]:
        cfg = self.config
        now_et = datetime.now(ET)
        past_force_close = (now_et.hour, now_et.minute) >= (
            cfg.force_close_hour_et, cfg.force_close_minute_et)
        actions: List[ExitAction] = []

        try:
            positions = self.trading.get_all_positions()
        except Exception as e:
            print(f"[exits] positions read failed: {e}")
            return actions

        for pos in positions:
            occ = parse_occ(pos.symbol)
            if occ is None:
                continue  # stock from assignment: see manage_assigned_stock()
            qty = float(pos.qty)
            is_short = qty < 0
            entry = abs(float(pos.avg_entry_price or 0))
            dte = (occ.expiry - now_et.date()).days

            quote = self.options.get_quotes([pos.symbol]).get(pos.symbol)
            # closing a short = buy at ask; closing a long = sell at bid
            mark = None
            if quote:
                mark = quote["ask"] if is_short else quote["bid"]

            reason = None
            if dte <= cfg.force_close_dte and past_force_close:
                reason = "expiry_close"
            elif mark is not None and mark > 0 and entry > 0:
                if is_short:
                    if mark <= entry * (1 - cfg.short_profit_take_pct):
                        reason = "profit_take"
                    elif mark >= entry * cfg.short_stop_loss_mult:
                        reason = "stop_loss"
                else:
                    if mark >= entry * (1 + cfg.debit_profit_take_pct):
                        reason = "profit_take"
            if reason is None:
                continue

            action = ExitAction(
                occ_symbol=pos.symbol, reason=reason, qty=abs(qty),
                side="buy_to_close" if is_short else "sell_to_close")
            actions.append(action)
            print(f"[exits] {pos.symbol}: {reason} (entry={entry} mark={mark} dte={dte})")

            if execute:
                self._close(pos, action, mark)
        return actions

    def manage_assigned_stock(self, events: dict,
                              execute: bool = True) -> List[str]:
        """Exit stock that arrived by put assignment.

        This path exists because the covered-call mandate deliberately declines
        to write calls over a bearish underlying — which left assigned shares
        (100 shares is 20-30% of this account) with no exit at all. A short call
        against the position is closed first, otherwise selling the stock would
        leave a naked call behind.
        """
        cfg = self.config
        closed: List[str] = []
        try:
            positions = self.trading.get_all_positions()
        except Exception as e:
            print(f"[exits] positions read failed: {e}")
            return closed

        stocks = {p.symbol: p for p in positions if parse_occ(p.symbol) is None}
        short_calls = {}
        for p in positions:
            occ = parse_occ(p.symbol)
            if occ and occ.contract_type == "call" and float(p.qty) < 0:
                short_calls.setdefault(occ.underlying, []).append(p)

        for symbol, pos in stocks.items():
            qty = float(pos.qty)
            if qty <= 0:
                continue
            ev = events.get(symbol)
            pnl_pct = float(getattr(pos, "unrealized_plpc", 0) or 0)

            reason = None
            if ev is not None and ev.signal == "SELL":
                reason = "sell_signal"
            elif pnl_pct <= -cfg.assigned_stock_stop_pct:
                reason = f"stop_loss {pnl_pct:.1%}"
            if reason is None:
                continue

            print(f"[exits] assigned stock {symbol}: {reason} ({qty} shares)")
            closed.append(symbol)
            if not execute:
                continue

            # unwind the covered call first — selling the shares underneath an
            # open short call would leave it naked
            for call in short_calls.get(symbol, []):
                try:
                    print(f"[exits] closing covering call {call.symbol} first")
                    self.trading.close_position(call.symbol)
                    time.sleep(2)
                except Exception as e:
                    print(f"[exits] could not close {call.symbol}: {e} "
                          f"— leaving {symbol} alone to avoid a naked call")
                    closed.remove(symbol)
                    break
            else:
                try:
                    self.trading.close_position(symbol)
                    if self.ledger:
                        self.ledger.log_decision(
                            symbol, "EXIT", "sell_stock", reason,
                            price=float(getattr(pos, "current_price", 0) or 0))
                    notify.on_stock_liquidation(symbol, qty, reason)
                except Exception as e:
                    print(f"[exits] stock close {symbol} failed: {e}")
        return closed

    def _close(self, pos, action: ExitAction, mark: Optional[float]) -> None:
        """Submit the closing order and record the round trip.

        The close is waited on rather than fired and forgotten: without the
        actual fill there is no realized P&L in our own ledger, only the
        broker's equity number, and the report and attribution both read from
        the ledger.
        """
        entry = abs(float(pos.avg_entry_price or 0))
        is_short = float(pos.qty) < 0
        try:
            if mark and mark > 0 and action.reason != "expiry_close":
                side = OrderSide.BUY if action.side == "buy_to_close" else OrderSide.SELL
                order = self.trading.submit_order(LimitOrderRequest(
                    symbol=action.occ_symbol, qty=action.qty, side=side,
                    time_in_force=TimeInForce.DAY, limit_price=round(mark, 2),
                    client_order_id=f"exit-{action.reason[:6]}-{int(time.time())}"))
            else:
                # forced close or dead quote: take liquidity via close_position
                order = self.trading.close_position(action.occ_symbol)

            fill = self._await_fill(getattr(order, "id", None))
            exit_price = fill if fill is not None else mark
            pnl = None
            if exit_price is not None and entry > 0:
                # a short is opened for a credit and closed for a debit
                per_contract = (entry - exit_price) if is_short else (exit_price - entry)
                pnl = per_contract * 100 * action.qty

            if self.ledger:
                occ = parse_occ(action.occ_symbol)
                self.ledger.log_trade(
                    symbol=occ.underlying if occ else action.occ_symbol,
                    occ_symbol=action.occ_symbol,
                    strategy="CSP" if (occ and occ.contract_type == "put") else "CC",
                    side=action.side, qty=action.qty,
                    price=exit_price or 0,
                    premium=-(exit_price or 0) * 100 * action.qty,
                    delta_at_entry=None,
                    dte=(occ.expiry - datetime.now(ET).date()).days if occ else None,
                    reason=action.reason,
                    client_order_id=getattr(order, "client_order_id", "") or "",
                    broker_order_id=str(getattr(order, "id", "") or ""),
                    equity_before=0.0, pnl=pnl)
                self.ledger.log_decision(
                    symbol=action.occ_symbol, signal="EXIT", action=action.side,
                    reason=action.reason, price=exit_price)
            print(f"[exits] closed {action.occ_symbol} @ {exit_price} "
                  f"(entry {entry}) pnl={pnl}")
            notify.on_exit(action.occ_symbol, action.reason, action.qty,
                           exit_price)
        except Exception as e:
            print(f"[exits] close {action.occ_symbol} failed: {e}")

    def _await_fill(self, order_id, seconds: int = 45) -> Optional[float]:
        """Filled average price, or None if it has not filled in time."""
        if order_id is None:
            return None
        deadline = time.time() + seconds
        while time.time() < deadline:
            try:
                o = self.trading.get_order_by_id(order_id)
                if o.filled_avg_price:
                    return float(o.filled_avg_price)
                if str(o.status).lower().endswith(("canceled", "rejected", "expired")):
                    return None
            except Exception as e:
                print(f"[exits] fill poll failed: {e}")
                return None
            time.sleep(3)
        return None
