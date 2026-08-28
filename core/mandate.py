"""OptionMandate: the contract between the deterministic core and any executor
(LLM or direct). The core decides WHAT to do and the hard bounds; the executor
only picks WHICH contract inside those bounds.
"""
import uuid
from dataclasses import dataclass, asdict, field
from datetime import date, datetime, timezone
from typing import Dict, List, Optional, Tuple


@dataclass
class OptionMandate:
    strategy: str                 # CSP | CC | LONG_CALL
    underlying: str
    qty: int
    delta_band: Tuple[float, float]   # abs(delta) bounds
    dte_min: int
    dte_max: int
    min_open_interest: int
    max_spread_pct_of_mid: float
    max_strike: Optional[float]       # CSP: strike*100*qty must fit notional cap
    client_order_id: str
    signal_context: dict = field(default_factory=dict)
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    def to_dict(self) -> dict:
        return asdict(self)


def make_order_id(prefix: str, underlying: str) -> str:
    return f"{prefix}-{date.today():%Y%m%d}-{underlying}-{uuid.uuid4().hex[:8]}"


class MandateBuilder:
    """Turns SignalEvents + account state into mandates, enforcing every
    account-level cap BEFORE anything reaches an executor."""

    def __init__(self, config, ledger=None):
        self.config = config
        self.ledger = ledger
        self._today: Optional[date] = None
        self._issued_today: List[str] = []   # underlyings with a mandate today

    def _roll_day(self) -> None:
        if self._today != date.today():
            self._today = date.today()
            self._issued_today = []

    def build(self, events: dict, equity: float,
              open_state: dict) -> List[OptionMandate]:
        """
        Args:
            events: {symbol: SignalEvent}
            equity: current account equity
            open_state: {
                "short_puts": {underlying: strike_notional},
                "stock_positions": {underlying: qty},        # >=100 enables CC
                "covered_calls": set of underlyings with an open CC,
                "structures_opened_today": int,
            }
        """
        self._roll_day()
        cfg = self.config
        mandates: List[OptionMandate] = []
        budget = cfg.max_new_structures_per_day - open_state.get("structures_opened_today", 0)
        if budget <= 0:
            return mandates

        short_puts: Dict[str, float] = open_state.get("short_puts", {})
        total_put_notional = sum(short_puts.values())
        max_total_notional = equity * cfg.max_total_short_put_notional_pct

        def csp_mandate(sym: str, ev, reason: str) -> Optional[OptionMandate]:
            if sym in self._issued_today or sym in short_puts:
                return None
            if len(short_puts) >= cfg.max_concurrent_short_puts:
                return None
            per_name_cap = equity * cfg.max_csp_notional_pct_per_underlying
            headroom = min(per_name_cap, max_total_notional - total_put_notional)
            max_strike = headroom / 100.0  # qty is fixed at 1
            # a CSP in the mandated delta band sits near the money; if the cap
            # can't fit ~80% of spot there is no sellable strike (MSFT/META on
            # a $100k account land here — by design)
            if max_strike < ev.price * 0.8:
                return None
            return OptionMandate(
                strategy="CSP", underlying=sym, qty=1,
                delta_band=cfg.csp_delta_band,
                dte_min=cfg.dte_min, dte_max=cfg.dte_max,
                min_open_interest=cfg.min_open_interest,
                max_spread_pct_of_mid=cfg.max_spread_pct_of_mid,
                max_strike=round(max_strike, 2),
                client_order_id=make_order_id(cfg.client_order_prefix, sym),
                signal_context={
                    "reason": reason, "signal": ev.signal, "price": ev.price,
                    "rsi": ev.info.get("rsi"), "adx": ev.info.get("adx"),
                    "atr_pct": ev.info.get("atr_pct"),
                    "confidence": ev.info.get("confidence"),
                    "net_score": ev.info.get("net_score"),
                },
            )

        # 1. Signal-driven CSPs (BUY on the underlying)
        for sym, ev in sorted(events.items(),
                              key=lambda kv: -(kv[1].info.get("confidence") or 0)):
            if len(mandates) >= budget:
                break
            if ev.signal == "BUY" and ev.is_new_bar:
                m = csp_mandate(sym, ev, "buy_signal")
                if m:
                    mandates.append(m)
                    self._issued_today.append(sym)

        # 2. Covered calls on stock positions (e.g. after put assignment)
        stock_pos = open_state.get("stock_positions", {})
        existing_ccs = open_state.get("covered_calls", set())
        for sym, qty in stock_pos.items():
            if len(mandates) >= budget:
                break
            if qty < 100 or sym in existing_ccs or sym in self._issued_today:
                continue
            ev = events.get(sym)
            if ev is not None and ev.signal == "SELL":
                continue  # exiting the stock instead; don't cap it with a call
            mandates.append(OptionMandate(
                strategy="CC", underlying=sym, qty=int(qty // 100),
                delta_band=self.config.cc_delta_band,
                dte_min=cfg.dte_min, dte_max=cfg.dte_max,
                min_open_interest=cfg.min_open_interest,
                max_spread_pct_of_mid=cfg.max_spread_pct_of_mid,
                max_strike=None,
                client_order_id=make_order_id(cfg.client_order_prefix, sym),
                signal_context={"reason": "covered_call_on_stock",
                                "signal": ev.signal if ev else "NONE"},
            ))
            self._issued_today.append(sym)

        # 3. Income CSP: no signal fired, few structures open -> keep theta
        #    flowing (a 7-day judged window cannot sit idle)
        open_structures = len(short_puts) + len(existing_ccs)
        if not mandates and open_structures < 3:
            candidates = [
                (sym, ev) for sym, ev in events.items()
                if ev.signal == "HOLD"
                and sym not in short_puts and sym not in self._issued_today
                and (ev.info.get("net_score") or 0) >= 0  # never sell puts into a bearish tape
            ]
            candidates.sort(key=lambda kv: -(kv[1].info.get("adx") or 0))
            if candidates:
                sym, ev = candidates[0]
                m = csp_mandate(sym, ev, "income_csp")
                if m:
                    mandates.append(m)
                    self._issued_today.append(sym)

        return mandates[:budget]
