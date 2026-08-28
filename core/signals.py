# strategy/enhanced.py
"""
Enhanced Trading Strategy with Multi-Indicator Confluence.

Features:
1. Multi-indicator confluence (EMA, RSI, MACD, ADX, Bollinger)
2. Volatility-adjusted signals (ATR-based)
3. Mean reversion + Momentum combination
4. Volume confirmation
5. Dynamic stop-loss (ATR-based trailing)
6. Signal confidence scoring
7. Aggressive/Conservative mode switching
"""
import os
import pandas as pd
import numpy as np
from typing import Tuple, Union, Dict, Optional
from dataclasses import dataclass

from core.indicators import (
    compute_rsi,
    compute_atr,
    compute_adx,
    compute_macd,
    compute_bollinger_bands,
    compute_volume_sma,
)

# Import regime utils - handle case where it might not be available
try:
    from regime.utils import load_regime_state
except ImportError:
    try:
        from regime_utils import load_regime_state
    except ImportError:
        def load_regime_state():
            return {"allowed": True, "risk_multiplier": 1.0, "regime": "NORMAL"}


# =========================================================
# Mode Control (Aggressive/Conservative)
# =========================================================

_paper_aggressive_override: Optional[bool] = None


def get_paper_aggressive() -> bool:
    """Get current trading mode (aggressive or conservative)."""
    if _paper_aggressive_override is not None:
        return _paper_aggressive_override
    return os.getenv("PAPER_AGGRESSIVE", "1") == "1"


def set_paper_aggressive(value: bool) -> None:
    """Set trading mode at runtime."""
    global _paper_aggressive_override
    _paper_aggressive_override = value


def get_mode_name() -> str:
    """Get current mode name as string."""
    return "AGGRESSIVE" if get_paper_aggressive() else "CONSERVATIVE"


# =========================================================
# Signal Scoring System
# =========================================================

@dataclass
class SignalScore:
    """Confluence scoring for trading signals."""
    trend_score: float = 0.0       # -1 to 1 (bearish to bullish)
    momentum_score: float = 0.0    # -1 to 1
    mean_reversion_score: float = 0.0
    volume_score: float = 0.0      # 0 to 1
    volatility_score: float = 0.0  # 0 to 1 (low to high)

    @property
    def total_bullish(self) -> float:
        """Total bullish score (0-5)."""
        return max(0, self.trend_score) + max(0, self.momentum_score) + \
               max(0, self.mean_reversion_score) + self.volume_score

    @property
    def total_bearish(self) -> float:
        """Total bearish score (0-4)."""
        return abs(min(0, self.trend_score)) + abs(min(0, self.momentum_score)) + \
               abs(min(0, self.mean_reversion_score))

    @property
    def net_score(self) -> float:
        """
        Directional conviction in roughly [-2, 2]. Positive = bullish.

        Only trend and momentum belong here — they are the two signed,
        trend-following axes. Deliberately EXCLUDED:

          volume_score          unsigned (0..1), carries no direction.
                                total_bullish includes it, which biases that
                                sum upward by up to +1.0 in any regime.
          mean_reversion_score  contrarian by construction: it sits near -1
                                while price rides the upper Bollinger band in a
                                rally, and near +1 during a crash. Adding it
                                here cancels — and at trend extremes inverts —
                                the very direction this is meant to measure.
                                Mean reversion is a separate axis and is used
                                on its own in the MR condition lists below.
        """
        return self.trend_score + self.momentum_score

    @property
    def confidence(self) -> float:
        """
        Conviction MAGNITUDE (0-1), direction-agnostic — a strong downtrend
        scores as high as a strong uptrend. Use for sizing or reporting only.
        Never use it as a buy condition; `net_score` is the directional one.
        """
        max_score = 4.0
        return min(1.0, max(self.total_bullish, self.total_bearish) / max_score)


def calculate_signal_score(df: pd.DataFrame, config) -> SignalScore:
    """
    Calculate confluence score from multiple indicators.

    Args:
        df: DataFrame with OHLCV data
        config: BotConfig with strategy parameters

    Returns:
        SignalScore with individual and aggregate scores
    """
    close = df['close']
    score = SignalScore()

    # 1. TREND SCORE (EMA + ADX)
    ema_fast = close.ewm(span=config.ema_fast).mean()
    ema_slow = close.ewm(span=config.ema_slow).mean()
    adx = compute_adx(df, 14)

    last_ema_fast = float(ema_fast.iloc[-1])
    last_ema_slow = float(ema_slow.iloc[-1])
    last_adx = float(adx.iloc[-1]) if not np.isnan(adx.iloc[-1]) else 20

    if last_ema_fast > last_ema_slow:
        score.trend_score = min(1.0, last_adx / 40)
    else:
        score.trend_score = -min(1.0, last_adx / 40)

    # 2. MOMENTUM SCORE (RSI + MACD)
    rsi = compute_rsi(close, config.rsi_period)
    macd_line, signal_line, histogram = compute_macd(close)

    last_rsi = float(rsi.iloc[-1])
    last_histogram = float(histogram.iloc[-1])

    # RSI enters here as MOMENTUM: monotonically increasing, +/-0.5 at the
    # extremes, so it matches the sign convention of every other axis.
    #
    # It previously read RSI contrarian-style — "oversold = bullish", +0.5 at
    # RSI 0 — which made momentum_score POSITIVE during a crash (trend -1.0,
    # momentum +1.0, net 0.0). Every surrounding consumer assumes the opposite:
    # `momentum_score < -0.3` is a SELL condition, so it could never fire in the
    # very selloff it was written for. Oversold-bounce logic still exists, in
    # the place it belongs — mean_reversion_score and the `rsi < 45` MR rule.
    rsi_score  = max(-0.5, min(0.5, (last_rsi - 50.0) / 100.0))
    macd_score = 0.5 if last_histogram > 0 else -0.5
    score.momentum_score = (rsi_score + macd_score)

    # 3. MEAN REVERSION SCORE (Bollinger)
    upper, middle, lower = compute_bollinger_bands(close, 20, 2.0)
    last_price = float(close.iloc[-1])
    last_upper = float(upper.iloc[-1])
    last_lower = float(lower.iloc[-1])

    bb_range = last_upper - last_lower
    if bb_range > 0:
        bb_position = (last_price - last_lower) / bb_range
        score.mean_reversion_score = 1 - (2 * bb_position)

    # 4. VOLUME SCORE
    vol_sma = compute_volume_sma(df, 20)
    last_volume = float(df['volume'].iloc[-1])
    last_vol_sma = float(vol_sma.iloc[-1]) if not np.isnan(vol_sma.iloc[-1]) else last_volume

    if last_vol_sma > 0:
        score.volume_score = min(1.0, last_volume / last_vol_sma)

    # 5. VOLATILITY SCORE (ATR-based)
    atr = compute_atr(df, 14)
    last_atr = float(atr.iloc[-1])
    atr_pct = last_atr / last_price if last_price > 0 else 0
    score.volatility_score = min(1.0, atr_pct * 20)

    return score


# =========================================================
# Dynamic Stop-Loss Calculator
# =========================================================

def calculate_dynamic_stops(
    df: pd.DataFrame,
    entry_price: float,
    side: str = "LONG",
    atr_multiplier: float = 2.0
) -> Dict[str, float]:
    """
    Calculate dynamic stop-loss and take-profit based on ATR.

    Args:
        df: DataFrame with OHLCV data
        entry_price: Entry price for the position
        side: Position side ("LONG" or "SHORT")
        atr_multiplier: ATR multiplier for stop distance

    Returns:
        Dict with stop_loss, take_profit, trailing_stop, atr values
    """
    atr = compute_atr(df, 14)
    last_atr = float(atr.iloc[-1])

    if side == "LONG":
        stop_loss = entry_price - (last_atr * atr_multiplier)
        take_profit = entry_price + (last_atr * atr_multiplier * 2.0)
        trailing_stop = last_atr * 1.5
    else:
        stop_loss = entry_price + (last_atr * atr_multiplier)
        take_profit = entry_price - (last_atr * atr_multiplier * 2.0)
        trailing_stop = last_atr * 1.5

    return {
        "stop_loss": round(stop_loss, 4),
        "take_profit": round(take_profit, 4),
        "trailing_stop_distance": round(trailing_stop, 4),
        "atr": round(last_atr, 4),
        "atr_pct": round(last_atr / entry_price * 100, 2),
    }


# =========================================================
# Signal Generation
# =========================================================

def generate_signal_enhanced(
    df: pd.DataFrame,
    config,
    return_regime_info: bool = False
) -> Union[str, Tuple[str, dict]]:
    """
    Generate trading signal using multi-indicator confluence.

    Args:
        df: DataFrame with OHLCV columns
        config: BotConfig with strategy parameters
        return_regime_info: If True, return (signal, info_dict)

    Returns:
        "BUY", "SELL", or "HOLD"
        or Tuple[str, dict] if return_regime_info=True
    """
    # Data sufficiency check
    min_bars = max(50, config.ema_slow + 20)
    if len(df) < min_bars:
        info = {
            "allowed": True,
            "reason": "insufficient_data",
            "bars": len(df),
            "required": min_bars
        }
        return ("HOLD", info) if return_regime_info else "HOLD"

    # Regime filter
    regime = load_regime_state()
    regime_allowed = bool(regime.get("allowed", True))
    risk_mult = float(regime.get("risk_multiplier", 1.0))

    if not regime_allowed and not getattr(config, 'allow_soft_regime', False):
        info = {
            "allowed": False,
            "reason": "regime_blocked",
            "stress": regime.get("stress_percentile"),
            "risk_multiplier": risk_mult,
        }
        return ("HOLD", info) if return_regime_info else "HOLD"

    # Calculate Signal Score
    score = calculate_signal_score(df, config)
    close = df['close']
    last_price = float(close.iloc[-1])

    # Indicators for info
    rsi = compute_rsi(close, config.rsi_period)
    ema_fast = close.ewm(span=config.ema_fast).mean()
    ema_slow = close.ewm(span=config.ema_slow).mean()
    adx = compute_adx(df, 14)
    atr = compute_atr(df, 14)

    last_rsi = float(rsi.iloc[-1])
    last_ema_fast = float(ema_fast.iloc[-1])
    last_ema_slow = float(ema_slow.iloc[-1])
    last_adx = float(adx.iloc[-1]) if not np.isnan(adx.iloc[-1]) else 20
    last_atr = float(atr.iloc[-1])

    # Base info
    base_info = {
        "price": round(last_price, 4),
        "rsi": round(last_rsi, 2),
        "ema_fast": round(last_ema_fast, 4),
        "ema_slow": round(last_ema_slow, 4),
        "adx": round(last_adx, 2),
        "atr": round(last_atr, 4),
        "atr_pct": round(last_atr / last_price * 100, 2),
        "trend_score": round(score.trend_score, 2),
        "momentum_score": round(score.momentum_score, 2),
        "volume_score": round(score.volume_score, 2),
        "confidence": round(score.confidence, 2),
        "regime_allowed": regime_allowed,
        "risk_multiplier": risk_mult,
    }

    # ── Directional gate ──────────────────────────────────────────────────
    # NECESSARY (not sufficient) for a confluence entry on either side.
    #
    # Without it, the condition lists below are dominated by direction-agnostic
    # predicates — volume, ADX, RSI bounds — that hold in essentially any
    # regime, so the required count is reached before direction is ever
    # consulted. That is what collapsed this function to a constant: across
    # 515,635 live evaluations it emitted BUY every single time and SELL zero
    # times, in BOTH modes, including through crashes. The SELL branch below
    # was unreachable, not merely rare.
    NEUTRAL_BAND = 0.15
    trend_up   = score.net_score >  NEUTRAL_BAND
    trend_down = score.net_score < -NEUTRAL_BAND

    if get_paper_aggressive():
        # AGGRESSIVE MODE: relaxed confirmation, but direction still required.
        buy_conditions = [
            score.trend_score > -0.2,
            last_rsi < 80,
            last_rsi > 20,
        ]
        mean_reversion_buy = [
            last_rsi < 50,
            score.volume_score > 0.3,
        ]
        min_buy_conditions = 2
        min_mr_conditions = 2
        # Mirror of buy_conditions. Note these are EXIT confirmations, so the
        # RSI bound points the same way as the buy side (a band, not a floor):
        # requiring a HIGH rsi to sell means never exiting a selloff, which is
        # exactly the state that left 18 positions open and 0 closed.
        sell_conditions = [
            score.trend_score < -0.2,
            score.momentum_score < 0.2,
            last_rsi < 80,
        ]
        mean_reversion_sell = [
            score.mean_reversion_score < -0.6,
            last_rsi > 70,
        ]
        min_sell_conditions = 2
        min_mr_sell = 2
    else:
        # CONSERVATIVE MODE: standard confirmation.
        buy_conditions = [
            score.trend_score > 0.1,
            score.momentum_score > -0.5,
            score.volume_score > 0.5,
            score.net_score > 0.3,      # was score.confidence — direction-blind
            last_adx > 15,
            last_rsi < 75,
        ]
        mean_reversion_buy = [
            score.mean_reversion_score > 0.3,
            last_rsi < 45,
            score.volume_score > 0.5,
        ]
        min_buy_conditions = 3
        min_mr_conditions = 2
        sell_conditions = [
            score.trend_score < -0.1,
            last_rsi > 70,
            score.momentum_score < -0.3,
            last_price < last_ema_fast,
        ]
        mean_reversion_sell = [
            score.mean_reversion_score < -0.3,
            last_rsi > 65,
        ]
        min_sell_conditions = 2
        min_mr_sell = 2

    # Mean-reversion entries are counter-trend by construction, so they are not
    # gated on the trend agreeing — but they must not fight a confirmed trend
    # either, or MR-buy fires on every bar of a downtrend (knife catching).
    buy_confluence  = trend_up   and sum(buy_conditions)  >= min_buy_conditions
    buy_mr          = (not trend_down) and sum(mean_reversion_buy)  >= min_mr_conditions
    sell_confluence = trend_down and sum(sell_conditions) >= min_sell_conditions
    sell_mr         = (not trend_up) and sum(mean_reversion_sell) >= min_mr_sell

    buy_ok, sell_ok = (buy_confluence or buy_mr), (sell_confluence or sell_mr)

    # Both sides firing means the confirmations disagree — let the directional
    # score break the tie rather than letting evaluation order decide silently.
    if buy_ok and sell_ok:
        buy_ok, sell_ok = score.net_score > 0, score.net_score < 0

    prefix = "AGGRESSIVE_" if get_paper_aggressive() else ""
    shared_info = {
        "allowed": True,
        "net_score": round(score.net_score, 3),
        "buy_conditions_met": sum(buy_conditions),
        "sell_conditions_met": sum(sell_conditions),
        "paper_aggressive": get_paper_aggressive(),
    }

    if buy_ok:
        info = {
            **base_info, **shared_info,
            "reason": prefix + ("buy_confluence" if buy_confluence else "buy_mean_reversion"),
            "mr_conditions_met": sum(mean_reversion_buy),
            "stops": calculate_dynamic_stops(df, last_price, "LONG"),
        }
        return ("BUY", info) if return_regime_info else "BUY"

    if sell_ok:
        info = {
            **base_info, **shared_info,
            "reason": prefix + ("sell_confluence" if sell_confluence else "sell_mean_reversion"),
            "mr_conditions_met": sum(mean_reversion_sell),
        }
        return ("SELL", info) if return_regime_info else "SELL"

    info = {**base_info, **shared_info, "reason": "no_confluence"}
    return ("HOLD", info) if return_regime_info else "HOLD"


# =========================================================
# Long-Term Trend Strategy (1-week / 3-week EMA crossover)
# =========================================================

# The validated horizon is CALENDAR time, not a candle count:
# 168h = 1 week, 504h = 3 weeks (strategy_candidates.py:108-116, hourly bars).
# Spans must therefore be derived from the timeframe — hardcoding 168/504 turns
# the strategy into a 28d/84d crossover on 4h bars and a 168d/504d one on daily.
LT_TREND_FAST_HOURS = 168   # 1 week
LT_TREND_SLOW_HOURS = 504   # 3 weeks

_TIMEFRAME_HOURS = {
    "1h": 1, "2h": 2, "4h": 4, "6h": 6, "8h": 8, "12h": 12, "1d": 24,
}


def lt_trend_spans(timeframe: str) -> Tuple[int, int]:
    """
    EMA spans that preserve the validated 1-week / 3-week horizon on any timeframe.

    >>> lt_trend_spans("1h")
    (168, 504)
    >>> lt_trend_spans("4h")
    (42, 126)
    >>> lt_trend_spans("1d")
    (7, 21)

    Raises ValueError on an unknown timeframe — a silent fallback here is what
    produced the 168d/504d crossover that ran in production.
    """
    hours = _TIMEFRAME_HOURS.get(str(timeframe).strip().lower())
    if hours is None:
        raise ValueError(
            f"lt_trend_spans: unsupported timeframe {timeframe!r}. "
            f"Known: {sorted(_TIMEFRAME_HOURS)}"
        )
    fast = max(2, round(LT_TREND_FAST_HOURS / hours))
    slow = max(fast + 1, round(LT_TREND_SLOW_HOURS / hours))
    return fast, slow


def lt_trend_min_bars(slow_span: int) -> int:
    """Bars needed before the slow EMA is converged enough to trade on."""
    return int(slow_span * 1.2)


def generate_signal_lt_trend(
    df: pd.DataFrame,
    regime_state: Optional[dict] = None,
    return_info: bool = False,
    timeframe: str = "4h",
) -> Union[str, Tuple[str, dict]]:
    """
    Long-Term Trend strategy: 1-week vs 3-week EMA crossover.

    Backtest evidence (OOS, 3 periods — strategy_candidates.py, 1h bars):
      Bear 2022:     Cap.Ratio 1.17, Alpha +3.8%/yr, Beta 0.02
      Bull 2024:     Cap.Ratio 1.23, Alpha +3.1%/yr, Beta 0.01
      Sideways 2023: Cap.Ratio 1.63, Alpha +6.0%/yr, Beta 0.01
      Average:       Cap.Ratio 1.34, Alpha +4.3%/yr

    Spans are derived from `timeframe` so the 1w/3w horizon holds regardless of
    bar size (4h → 42/126, 1h → 168/504). Requires ~1.2x the slow span in bars.

    Signal logic:
      BUY  — fast EMA > slow EMA AND regime allowed
      SELL — fast EMA < slow EMA  (trend reversal exit)
      HOLD — fast EMA > slow EMA but regime blocked (no new entry; hold existing)
    """
    fast_span, slow_span = lt_trend_spans(timeframe)
    MIN_BARS = lt_trend_min_bars(slow_span)
    if len(df) < MIN_BARS:
        info = {
            "reason": "insufficient_data", "bars": len(df), "required": MIN_BARS,
            "timeframe": timeframe,
            "ema_fast_span": fast_span, "ema_slow_span": slow_span,
        }
        return ("HOLD", info) if return_info else "HOLD"

    close = df["close"]
    ema_fast = close.ewm(span=fast_span, adjust=False).mean()
    ema_slow = close.ewm(span=slow_span, adjust=False).mean()

    last_ema_fast = float(ema_fast.iloc[-1])
    last_ema_slow = float(ema_slow.iloc[-1])
    last_price    = float(close.iloc[-1])
    in_trend      = last_ema_fast > last_ema_slow

    atr     = compute_atr(df, 14)
    last_atr = float(atr.iloc[-1])

    # Regime state — prefer live RegimeEngine result, fall back to JSON file
    if regime_state is None:
        regime_state = load_regime_state()
    regime_allowed = bool(regime_state.get("allowed", True))
    risk_mult      = float(regime_state.get("risk_multiplier", 1.0))
    regime_name    = regime_state.get("regime", "NORMAL")

    base_info = {
        "price":           round(last_price, 4),
        "ema_fast":        round(last_ema_fast, 4),
        "ema_slow":        round(last_ema_slow, 4),
        "ema_fast_span":   fast_span,
        "ema_slow_span":   slow_span,
        "timeframe":       timeframe,
        "in_trend":        in_trend,
        "atr":             round(last_atr, 4),
        "atr_pct":         round(last_atr / last_price * 100, 2),
        "regime_allowed":  regime_allowed,
        "regime":          regime_name,
        "risk_multiplier": risk_mult,
        "confidence":      1.0 if (in_trend and regime_allowed) else 0.0,
    }

    # SELL: weekly trend has flipped down
    if not in_trend:
        info = {**base_info, "reason": "trend_down_fast_below_slow"}
        return ("SELL", info) if return_info else "SELL"

    # HOLD: trend up but regime blocked — no new entries, keep existing positions
    if not regime_allowed:
        info = {**base_info, "reason": "regime_blocked_hold_only"}
        return ("HOLD", info) if return_info else "HOLD"

    # BUY: trend up and regime allows.
    # Stop placement is handled by the execution layer (catastrophic % stop +
    # percentage-trail ratchet). ATR is included for reference/logging only.
    info = {
        **base_info,
        "reason": "trend_up_fast_above_slow",
    }
    return ("BUY", info) if return_info else "BUY"


# =========================================================
# Alias for backwards compatibility
# =========================================================

# Alias for backwards compatibility
def generate_signal(
    df: pd.DataFrame,
    config,
    return_regime_info: bool = False,
    use_enhanced: bool = True
) -> Union[str, Tuple[str, dict]]:
    """
    Wrapper for backwards compatibility.
    Always uses enhanced strategy (use_enhanced parameter is deprecated).
    """
    return generate_signal_enhanced(df, config, return_regime_info)
