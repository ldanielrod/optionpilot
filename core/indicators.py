# strategy/indicators.py
"""
Technical indicators for trading strategy.

All indicators are implemented as pure functions operating on pandas Series/DataFrames.
"""
import pandas as pd
import numpy as np
from typing import Tuple


def compute_rsi(series: pd.Series, period: int = 14) -> pd.Series:
    """
    Compute Wilder's RSI (Relative Strength Index).

    Args:
        series: Price series (typically close prices)
        period: RSI period (default 14)

    Returns:
        RSI values as Series (0-100 scale)
    """
    delta = series.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)

    # Use EWM for Wilder's smoothing
    avg_gain = gain.ewm(alpha=1/period, min_periods=period).mean()
    avg_loss = loss.ewm(alpha=1/period, min_periods=period).mean()

    rs = avg_gain / avg_loss
    rsi = 100 - (100 / (1 + rs))
    return rsi


def compute_atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
    """
    Compute Average True Range - volatility measure.

    Args:
        df: DataFrame with 'high', 'low', 'close' columns
        period: ATR period (default 14)

    Returns:
        ATR values as Series
    """
    high = df['high']
    low = df['low']
    close = df['close']

    tr1 = high - low
    tr2 = abs(high - close.shift(1))
    tr3 = abs(low - close.shift(1))

    tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
    atr = tr.ewm(span=period, adjust=False).mean()
    return atr


def compute_adx(df: pd.DataFrame, period: int = 14) -> pd.Series:
    """
    Compute Average Directional Index - trend strength indicator.

    ADX > 25: Strong trend
    ADX < 20: Sideways/weak trend

    Args:
        df: DataFrame with 'high', 'low', 'close' columns
        period: ADX period (default 14)

    Returns:
        ADX values as Series
    """
    high = df['high']
    low = df['low']
    close = df['close']

    # Directional Movement
    up_move = high - high.shift(1)
    down_move = low.shift(1) - low

    plus_dm = np.where((up_move > down_move) & (up_move > 0), up_move, 0)
    minus_dm = np.where((down_move > up_move) & (down_move > 0), down_move, 0)

    # True Range
    tr1 = high - low
    tr2 = abs(high - close.shift(1))
    tr3 = abs(low - close.shift(1))
    tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)

    # Smoothed values
    atr = tr.ewm(span=period, adjust=False).mean()
    plus_di = 100 * pd.Series(plus_dm, index=high.index).ewm(span=period, adjust=False).mean() / atr
    minus_di = 100 * pd.Series(minus_dm, index=high.index).ewm(span=period, adjust=False).mean() / atr

    # ADX
    dx = 100 * abs(plus_di - minus_di) / (plus_di + minus_di + 1e-10)
    adx = dx.ewm(span=period, adjust=False).mean()

    return adx


def compute_macd(
    series: pd.Series,
    fast: int = 12,
    slow: int = 26,
    signal: int = 9
) -> Tuple[pd.Series, pd.Series, pd.Series]:
    """
    Compute MACD (Moving Average Convergence Divergence).

    Args:
        series: Price series (typically close prices)
        fast: Fast EMA period (default 12)
        slow: Slow EMA period (default 26)
        signal: Signal line period (default 9)

    Returns:
        Tuple of (macd_line, signal_line, histogram)
    """
    ema_fast = series.ewm(span=fast, adjust=False).mean()
    ema_slow = series.ewm(span=slow, adjust=False).mean()

    macd_line = ema_fast - ema_slow
    signal_line = macd_line.ewm(span=signal, adjust=False).mean()
    histogram = macd_line - signal_line

    return macd_line, signal_line, histogram


def compute_bollinger_bands(
    series: pd.Series,
    period: int = 20,
    std_dev: float = 2.0
) -> Tuple[pd.Series, pd.Series, pd.Series]:
    """
    Compute Bollinger Bands for mean reversion analysis.

    Args:
        series: Price series (typically close prices)
        period: SMA period (default 20)
        std_dev: Number of standard deviations (default 2.0)

    Returns:
        Tuple of (upper_band, middle_band, lower_band)
    """
    sma = series.rolling(period).mean()
    std = series.rolling(period).std()

    upper = sma + (std_dev * std)
    lower = sma - (std_dev * std)

    return upper, sma, lower


def compute_volume_sma(df: pd.DataFrame, period: int = 20) -> pd.Series:
    """
    Compute Simple Moving Average of volume.

    Args:
        df: DataFrame with 'volume' column
        period: SMA period (default 20)

    Returns:
        Volume SMA as Series
    """
    return df['volume'].rolling(period).mean()


def compute_momentum(series: pd.Series, period: int = 10) -> pd.Series:
    """
    Compute Rate of Change (ROC) - simple momentum indicator.

    Args:
        series: Price series
        period: Lookback period (default 10)

    Returns:
        ROC values as percentage
    """
    return (series / series.shift(period) - 1) * 100


def compute_ema(series: pd.Series, period: int) -> pd.Series:
    """
    Compute Exponential Moving Average.

    Args:
        series: Input series
        period: EMA period

    Returns:
        EMA values as Series
    """
    return series.ewm(span=period, adjust=False).mean()


def compute_sma(series: pd.Series, period: int) -> pd.Series:
    """
    Compute Simple Moving Average.

    Args:
        series: Input series
        period: SMA period

    Returns:
        SMA values as Series
    """
    return series.rolling(period).mean()
