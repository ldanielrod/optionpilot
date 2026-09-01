"""OptionMandate: the contract between the deterministic core and any executor
(LLM or direct). The core decides WHAT to do and the hard bounds; the executor
only picks WHICH contract inside those bounds.
"""
import math
import uuid
from dataclasses import dataclass, asdict, field
from datetime import date, datetime, timedelta, timezone
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
    # IV floor: realized vol x the required premium ratio. A contract below it
    # is not paying for the risk being taken, whatever the signal says.
    min_iv: Optional[float] = None
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    def to_dict(self) -> dict:
        return asdict(self)


def make_order_id(prefix: str, underlying: str) -> str:
    return f"{prefix}-{date.today():%Y%m%d}-{underlying}-{uuid.uuid4().hex[:8]}"


class MandateBuilder:
    """Turns SignalEvents + account state into mandates, enforcing every
    account-level cap BEFORE anything reaches an executor."""

    def __init__(self, config, ledger=None, corporate_actions=None):
        self.config = config
        self.ledger = ledger
        self.corp = corporate_actions
        self._today: Optional[date] = None
        self._issued_today: List[str] = []   # underlyings with a mandate today

    def _roll_day(self) -> None:
        if self._today != date.today():
            self._today = date.today()
            self._issued_today = []

    def _log_skip(self, symbol: str, reason: str, details: dict) -> None:
        print(f"[mandate] {symbol}: skipped ({reason}) {details}")
        if self.ledger:
            self.ledger.log_decision(symbol, "CSP", "NO_MANDATE", reason,
                                     details=details)

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
        if self.corp is not None:
            self.corp.refresh(list(events.keys()))
        # the furthest expiry any mandate could reach
        horizon = date.today() + timedelta(days=cfg.dte_max)
        budget = cfg.max_new_structures_per_day - open_state.get("structures_opened_today", 0)
        if budget <= 0:
            return mandates

        short_puts: Dict[str, float] = open_state.get("short_puts", {})
        total_put_notional = sum(short_puts.values())
        max_total_notional = equity * cfg.max_total_short_put_notional_pct
        open_delta_notional = open_state.get("short_put_delta_notional", 0.0)
        max_delta_notional = equity * cfg.max_aggregate_delta_pct

        def corporate_block(sym: str) -> Optional[str]:
            """Splits rewrite the contract into a non-standard deliverable —
            never open into one. Earnings are checked separately because
            Alpaca's corporate actions feed does not carry them."""
            if self.corp is not None:
                split = self.corp.split_before(sym, horizon)
                if split:
                    return f"split_{split}"
            earnings = cfg.earnings_dates.get(sym)
            if earnings and date.today() <= date.fromisoformat(earnings) <= horizon:
                return f"earnings_{earnings}"
            return None

        def csp_mandate(sym: str, ev, reason: str) -> Optional[OptionMandate]:
            if sym in self._issued_today or sym in short_puts:
                return None
            if len(short_puts) >= cfg.max_concurrent_short_puts:
                return None
            blocked = corporate_block(sym)
            if blocked:
                self._log_skip(sym, blocked, {"horizon": str(horizon)})
                return None

            # Aggregate delta cap. A new put in the band adds roughly
            # mid_delta x 100 x spot of long-delta equivalent; on correlated
            # megacaps that exposure compounds into a single factor bet that
            # strike notional alone does not reveal.
            mid_delta = sum(cfg.csp_delta_band) / 2
            added_delta = mid_delta * 100 * ev.price
            if open_delta_notional + added_delta > max_delta_notional:
                self._log_skip(sym, "aggregate_delta_cap", {
                    "open_delta_notional": round(open_delta_notional),
                    "would_add": round(added_delta),
                    "cap": round(max_delta_notional)})
                return None

            # Volatility risk premium: the contract must pay more implied vol
            # than the underlying has actually been delivering.
            min_iv = None
            if ev.realized_vol is not None:
                min_iv = ev.realized_vol * cfg.min_iv_over_realized
            elif reason == "income_csp":
                # no vol estimate and no signal to justify the trade
                self._log_skip(sym, "no_realized_vol", {})
                return None

            per_name_cap = equity * cfg.max_csp_notional_pct_per_underlying
            headroom = min(per_name_cap, max_total_notional - total_put_notional)
            # Collateral the broker will actually demand is strike x 100, and a
            # resting order holds it. On day one three unfilled AAPL orders
            # drained options buying power from $100k to $31k and the next
            # mandate came back rejected — check what is really available.
            obp = open_state.get("options_buying_power")
            if obp is not None:
                headroom = min(headroom, obp * cfg.buying_power_safety)
            max_strike = headroom / 100.0  # qty is fixed at 1
            # Is any strike in the delta band actually affordable? The furthest
            # OTM strike the band allows sits at roughly
            #     spot * exp(-z * sigma * sqrt(T))
            # for z = 0.84 (the 0.20-delta end) — about 6% below spot on a
            # 20%-vol name at 35 DTE, not the 20% a flat heuristic assumed. If
            # the collateral cap lands below that, every contract in the band is
            # out of reach and the mandate would only burn a session and come
            # back rejected.
            if ev.realized_vol:
                t_years = cfg.dte_max / 252.0
                z = 0.8416  # standard normal at the 0.20-delta end of the band
                min_reachable = ev.price * math.exp(
                    -z * ev.realized_vol * math.sqrt(t_years))
            else:
                min_reachable = ev.price * 0.90
            if max_strike < min_reachable:
                self._log_skip(sym, "collateral_below_delta_band", {
                    "max_strike": round(max_strike, 2),
                    "need_at_least": round(min_reachable, 2),
                    "spot": round(ev.price, 2)})
                return None
            return OptionMandate(
                strategy="CSP", underlying=sym, qty=1,
                delta_band=cfg.csp_delta_band,
                dte_min=cfg.dte_min, dte_max=cfg.dte_max,
                min_open_interest=cfg.min_open_interest,
                max_spread_pct_of_mid=cfg.max_spread_pct_of_mid,
                max_strike=round(max_strike, 2),
                client_order_id=make_order_id(cfg.client_order_prefix, sym),
                min_iv=round(min_iv, 4) if min_iv else None,
                signal_context={
                    "reason": reason, "signal": ev.signal, "price": ev.price,
                    "rsi": ev.info.get("rsi"), "adx": ev.info.get("adx"),
                    "atr_pct": ev.info.get("atr_pct"),
                    "confidence": ev.info.get("confidence"),
                    "net_score": ev.info.get("net_score"),
                    "realized_vol_20d": (round(ev.realized_vol, 4)
                                         if ev.realized_vol else None),
                    # context, not a veto: the ex-dividend drop is already in
                    # the put's price, but the selector should know it is there
                    "ex_dividend": (str(self.corp.ex_dividend_before(sym, horizon))
                                    if self.corp else None),
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
                continue  # ExitManager.manage_assigned_stock sells it instead
            blocked = corporate_block(sym)
            if blocked:
                self._log_skip(sym, f"cc_{blocked}", {"horizon": str(horizon)})
                continue
            # Unlike a short put, a short CALL carries real early-assignment
            # risk across an ex-dividend date: once extrinsic value falls below
            # the dividend, exercising early to capture it is rational.
            ex_div = (self.corp.ex_dividend_before(sym, horizon)
                      if self.corp else None)
            if ex_div:
                self._log_skip(sym, "cc_ex_dividend_assignment_risk",
                               {"ex_date": str(ex_div)})
                continue
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

        # 3. Premium-harvesting CSP. The directional signal gates entry, but it
        #    is not the source of return: the return is the volatility risk
        #    premium, which is available whether or not the trend model has an
        #    opinion. When no signal fires and the book is below its target
        #    number of structures, hold that exposure on the name with the
        #    cleanest tape. Every other constraint still applies — most
        #    importantly the IV floor, which is what makes this a paid risk
        #    rather than activity for its own sake.
        open_structures = len(short_puts) + len(existing_ccs)
        if not mandates and open_structures < 3:
            candidates = [
                (sym, ev) for sym, ev in events.items()
                if ev.signal == "HOLD"
                and sym not in short_puts and sym not in self._issued_today
                and (ev.info.get("net_score") or 0) >= 0  # never sell puts into a bearish tape
            ]
            candidates.sort(key=lambda kv: -(kv[1].info.get("adx") or 0))
            # Walk the ranking instead of giving up on the first name: the
            # preferred candidate is often refused for a reason that says
            # nothing about the next one (collateral, IV floor, a split), and
            # abandoning the whole branch over it left the book idle with room
            # to spare.
            for sym, ev in candidates:
                m = csp_mandate(sym, ev, "income_csp")
                if m:
                    mandates.append(m)
                    self._issued_today.append(sym)
                    break

        return mandates[:budget]
