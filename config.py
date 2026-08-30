"""OptionPilot configuration.

All knobs in one dataclass; env vars override the risky ones. The LLM never
sees or changes these — they are the hard bounds the core enforces.
"""
import os
from dataclasses import dataclass, field
from typing import Tuple


def _env_bool(name: str, default: str = "0") -> bool:
    return os.getenv(name, default).strip() in ("1", "true", "True")


@dataclass
class Config:
    # Universe: liquid megacaps with penny-increment weekly options
    symbols: Tuple[str, ...] = (
        "AAPL", "MSFT", "GOOGL", "AMZN", "TSLA", "META", "NVDA", "JPM",
    )

    # Master switches
    execute: bool = field(default_factory=lambda: _env_bool("EXECUTE", "0"))
    llm_enabled: bool = field(default_factory=lambda: _env_bool("LLM_ENABLED", "0"))

    # Signal engine (same defaults the production bot trades daily bars with)
    ema_fast: int = 15
    ema_slow: int = 40
    rsi_period: int = 14
    rsi_buy_max: float = 70.0
    rsi_sell_min: float = 25.0
    atr_period: int = 14

    # Option mandate bands (the LLM must pick inside these)
    csp_delta_band: Tuple[float, float] = (0.20, 0.35)   # short put, abs(delta)
    cc_delta_band: Tuple[float, float] = (0.15, 0.30)    # covered call
    long_call_delta_band: Tuple[float, float] = (0.55, 0.70)
    dte_min: int = 4
    dte_max: int = 35
    min_open_interest: int = 300
    max_spread_pct_of_mid: float = 0.10
    max_spread_abs_low_priced: float = 0.15  # for options under $1.50

    # Volatility risk premium filter. Selling a put is a bet that implied vol
    # is rich relative to what the underlying actually delivers. Without this
    # check the strategy sells premium on a directional signal alone, which is
    # a different and unsupported claim. Require IV to beat 20-day realized
    # vol by this ratio, or no mandate is issued.
    min_iv_over_realized: float = 1.10
    realized_vol_window: int = 20

    # Account-level caps
    max_new_structures_per_day: int = 3
    max_structures_per_underlying_per_day: int = 1
    max_concurrent_short_puts: int = 6
    # Strike notional understates the real exposure: six 0.30-delta puts on
    # correlated megacaps carry ~40% of equity in long-delta equivalent, all in
    # one factor. Cap the aggregate delta, not just the cash committed.
    max_aggregate_delta_pct: float = 0.25
    # One CSP contract on a megacap is 20-35% of a $100k account in strike
    # notional — a tighter per-name cap can't fit a single contract (found in
    # dry run). The binding constraint is the 60% total cap below.
    max_csp_notional_pct_per_underlying: float = 0.35
    max_total_short_put_notional_pct: float = 0.60
    max_debit_premium_pct_per_trade: float = 0.015
    max_total_debit_premium_pct: float = 0.06

    # Used by PositionManager (core/risk.py)
    cooldown_seconds: int = 3600          # one structure per symbol per hour max
    max_exposure_pct: float = 0.35        # per-underlying (mirrors CSP notional cap)
    max_total_exposure_pct: float = 0.60  # mirrors total short-put notional cap
    min_trade_usd: float = 25.0           # min premium; irrelevant for sizing, kept for API
    max_drawdown_pct: float = 0.12        # 7-day sprint: halt earlier than prod (20%)
    resume_drawdown_pct: float = 0.06

    # Exits
    short_profit_take_pct: float = 0.50   # buy-to-close at 50% of premium collected
    short_stop_loss_mult: float = 2.2     # stop when option price >= 2.2x premium
    debit_profit_take_pct: float = 0.75
    force_close_dte: int = 1              # close anything <=1 DTE
    force_close_hour_et: int = 15         # ...by 15:30 ET
    force_close_minute_et: int = 30
    # Shares arriving by put assignment are a large unhedged directional
    # position (100 shares is 20-30% of this account). Exit on a bearish
    # signal, or on this loss from the assignment price.
    assigned_stock_stop_pct: float = 0.08

    # Cadence
    decision_times_et: Tuple[str, ...] = ("09:45", "12:30", "15:15")
    reconcile_seconds: int = 600

    # LLM
    llm_model: str = os.getenv("LLM_MODEL", "claude-opus-5")
    llm_max_turns: int = 12
    llm_violation_limit: int = 2  # violations before LLM_ENABLED is forced off

    # Earnings blackout: symbol -> ISO date of next earnings report. A mandate
    # is never emitted if the report falls inside the contract's DTE window.
    # Verified 2026-08-27: NVDA reported Aug 26 (before the window); none of
    # the 8 names report Aug 28 - Sep 4; the rest report late October.
    earnings_dates: dict = field(default_factory=dict)

    client_order_prefix: str = "hack"


CONFIG = Config()
