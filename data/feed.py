# feeds/alpaca.py
"""
Alpaca data feed for stock OHLCV data.
"""
import pandas as pd
from typing import Optional
from datetime import datetime, timedelta, timezone

from alpaca.common.enums import Sort
from alpaca.data import StockHistoricalDataClient
from alpaca.data.enums import DataFeed
from alpaca.data.requests import (
    StockBarsRequest,
    StockLatestBarRequest,
    StockLatestTradeRequest,
)
from alpaca.data.timeframe import TimeFrame


class AlpacaFeed:
    """
    Data feed for fetching OHLCV data from Alpaca.
    """

    def __init__(
        self,
        api_key: str,
        api_secret: str,
        timeframe: TimeFrame = TimeFrame.Day
    ):
        """
        Initialize Alpaca data feed.

        Args:
            api_key: Alpaca API key
            api_secret: Alpaca API secret
            timeframe: Data timeframe (default: Day)

        Daily, not hourly: the RSI/EMA strategy this feeds was measured on
        intraday bars at Sharpe 0.02-0.18 with ~900 trades (BTC 1h, net of
        fees and slippage) versus Sharpe ~1.0 with ~40 trades on daily bars.
        The edge does not survive the churn at intraday frequency. eToro's
        feed is already daily; this aligns Alpaca with it.
        """
        self.client = StockHistoricalDataClient(api_key, api_secret)
        self.timeframe = timeframe
        print(f"[AlpacaFeed] Initialized | timeframe={timeframe}")

    # US market: ~6.5 trading hours per session, ~252 sessions per 365 calendar
    # days. Both are needed to turn "N bars" into "how far back to ask".
    _TRADING_HOURS_PER_SESSION = 6.5
    _CALENDAR_PER_TRADING_DAY  = 365.0 / 252.0

    def _bars_per_session(self) -> float:
        """How many bars of the configured size fit in one trading session."""
        try:
            unit   = str(getattr(self.timeframe, "unit_value", None)
                         or getattr(self.timeframe, "unit", "")).lower()
            amount = float(getattr(self.timeframe, "amount_value", None)
                           or getattr(self.timeframe, "amount", 1) or 1)
        except Exception:
            return 1.0

        if "min" in unit:
            return max(1.0, (self._TRADING_HOURS_PER_SESSION * 60.0) / amount)
        if "hour" in unit:
            return max(1.0, self._TRADING_HOURS_PER_SESSION / amount)
        if "day" in unit:
            return 1.0 / amount
        if "week" in unit:
            return 1.0 / (5.0 * amount)
        if "month" in unit:
            return 1.0 / (21.0 * amount)
        return 1.0   # unknown unit → assume one bar per session (conservative)

    def _lookback_days(self, limit: int) -> int:
        """
        Calendar days to request so `limit` bars actually come back.

        Converts bars → sessions → calendar days, then adds 40% plus a week of
        slack for holidays and long weekends. Over-requesting is free (the API
        caps at `limit`); under-requesting silently starves the strategy.
        """
        sessions = max(1.0, limit / self._bars_per_session())
        return max(10, int(sessions * self._CALENDAR_PER_TRADING_DAY * 1.4) + 7)

    def get_ohlcv(self, symbol: str, limit: int = 200) -> Optional[pd.DataFrame]:
        """
        Fetch OHLCV candles for a symbol.

        Args:
            symbol: Stock symbol (e.g., 'AAPL')
            limit: Number of candles to fetch

        Returns:
            DataFrame with columns: timestamp, open, high, low, close, volume
            or None if failed
        """
        try:
            end = datetime.now(timezone.utc)
            # US equities only trade during market hours, so `limit` bars span a
            # much wider calendar window than `limit` days. The window must scale
            # with the BAR SIZE: the old `limit/4 + 10` was calibrated for hourly
            # bars, and on daily bars it asked for 60 calendar days to get 200
            # bars — returning 41, below the 50 the strategy needs, so every
            # STOCKS cycle failed validation with "Only 41 bars, need 50".
            start = end - timedelta(days=self._lookback_days(limit))

            req = StockBarsRequest(
                symbol_or_symbols=symbol,
                timeframe=self.timeframe,
                start=start,
                end=end,
                limit=limit,
                sort=Sort.DESC,
                feed=DataFeed.IEX,
            )

            bars = self.client.get_stock_bars(req).df
            if bars is None or bars.empty:
                return None

            df = bars.reset_index()

            # Normalize column names
            rename_map = {}
            if "timestamp" in df.columns:
                rename_map["timestamp"] = "timestamp"
            if "time" in df.columns:
                rename_map["time"] = "timestamp"

            for c in ["open", "high", "low", "close", "volume"]:
                if c in df.columns:
                    rename_map[c] = c

            df = df.rename(columns=rename_map)

            needed = ["timestamp", "open", "high", "low", "close", "volume"]
            if not all(c in df.columns for c in needed):
                return None

            df = df[needed]
            df = df.sort_values("timestamp").tail(limit).reset_index(drop=True)
            return df

        except Exception as e:
            print(f"[AlpacaFeed] Error fetching OHLCV for {symbol}: {e}")
            return None

    def get_last_price(self, symbol: str) -> Optional[float]:
        """
        Get the last price for a symbol.

        Args:
            symbol: Stock symbol (e.g., 'AAPL')

        Returns:
            Last close price as float, or None if failed
        """
        try:
            req = StockLatestTradeRequest(
                symbol_or_symbols=symbol,
                feed=DataFeed.IEX,
            )
            trades = self.client.get_stock_latest_trade(req)
            trade = trades.get(symbol) if isinstance(trades, dict) else None
            if trade is not None and getattr(trade, "price", None) is not None:
                return float(trade.price)
        except Exception as e:
            print(f"[AlpacaFeed] Error fetching latest trade for {symbol}: {e}")

        try:
            req = StockLatestBarRequest(
                symbol_or_symbols=symbol,
                feed=DataFeed.IEX,
            )
            bars = self.client.get_stock_latest_bar(req)
            bar = bars.get(symbol) if isinstance(bars, dict) else None
            if bar is not None and getattr(bar, "close", None) is not None:
                return float(bar.close)
        except Exception as e:
            print(f"[AlpacaFeed] Error fetching latest bar for {symbol}: {e}")

        df = self.get_ohlcv(symbol, limit=1)
        if df is None or df.empty:
            return None
        return float(df.iloc[-1]["close"])
