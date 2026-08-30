"""Daily-bar scan: one SignalEvent per symbol per closed bar.

Same discipline as the production bot: signals are evaluated on CLOSED daily
bars only (the still-forming bar of the current session is dropped), and each
closed bar is evaluated exactly once per symbol.
"""
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Dict, Optional

import numpy as np
import pandas as pd

from core.signals import generate_signal_enhanced


@dataclass
class SignalEvent:
    symbol: str
    signal: str          # BUY | SELL | HOLD
    price: float
    info: dict = field(default_factory=dict)
    bar_ts: Optional[str] = None
    is_new_bar: bool = True
    realized_vol: Optional[float] = None   # annualized, from closed daily bars


def realized_volatility(df: pd.DataFrame, window: int = 20) -> Optional[float]:
    """Annualized close-to-close volatility over the last `window` bars.

    This is the benchmark implied vol has to beat: without it, selling a put is
    a directional bet wearing a premium-seller's costume.
    """
    if df is None or len(df) < window + 1:
        return None
    closes = df["close"].astype(float).tail(window + 1)
    rets = np.log(closes / closes.shift(1)).dropna()
    if len(rets) < window // 2 or not rets.std() > 0:
        return None
    return float(rets.std() * np.sqrt(252))


def closed_bars(df: pd.DataFrame) -> pd.DataFrame:
    """Drop the still-forming bar: any daily bar stamped today (UTC date of the
    session) is incomplete while the market is open, and Alpaca returns it."""
    if df is None or df.empty:
        return df
    last_ts = pd.Timestamp(df.iloc[-1]["timestamp"])
    if last_ts.tzinfo is None:
        last_ts = last_ts.tz_localize("UTC")
    if last_ts.date() >= datetime.now(timezone.utc).date():
        return df.iloc[:-1]
    return df


class Scanner:
    def __init__(self, feed, config):
        self.feed = feed
        self.config = config
        self._last_bar: Dict[str, str] = {}  # symbol -> last evaluated bar key

    def scan(self) -> Dict[str, SignalEvent]:
        events: Dict[str, SignalEvent] = {}
        for sym in self.config.symbols:
            try:
                df = self.feed.get_ohlcv(sym, limit=120)
            except Exception as e:
                print(f"[scanner] {sym}: feed error {e}")
                continue
            if df is None or len(df) < 60:
                print(f"[scanner] {sym}: insufficient bars "
                      f"({0 if df is None else len(df)})")
                continue

            df = closed_bars(df)
            if df is None or len(df) < 60:
                continue

            bar_key = str(df.iloc[-1]["timestamp"])
            is_new = self._last_bar.get(sym) != bar_key
            signal, info = generate_signal_enhanced(
                df, self.config, return_regime_info=True)
            self._last_bar[sym] = bar_key

            price = float(df.iloc[-1]["close"])
            rv = realized_volatility(df, self.config.realized_vol_window)
            events[sym] = SignalEvent(
                symbol=sym, signal=signal, price=price, info=info,
                bar_ts=bar_key, is_new_bar=is_new, realized_vol=rv,
            )
        return events
