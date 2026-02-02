from __future__ import annotations

from typing import Any, Dict

import numpy as np
import pandas as pd

from src.db import DB

_PRICE_CACHE: pd.DataFrame | None = None
_BOLLINGER_CACHE: Dict[str, pd.Series] = {}
_HIGH_CACHE: Dict[str, pd.Series] = {}
USE_52W_HIGH_FILTER = True


def _load_prices() -> pd.DataFrame:
    global _PRICE_CACHE
    if _PRICE_CACHE is None:
        series = DB().load()
        df = series.unstack("code")
        df.index = pd.to_datetime(df.index)
        _PRICE_CACHE = df.sort_index()
    return _PRICE_CACHE


def _bollinger_upper(code: str) -> pd.Series:
    if code in _BOLLINGER_CACHE:
        return _BOLLINGER_CACHE[code]

    prices = _load_prices().get(code)
    if prices is None:
        upper = pd.Series(dtype=float)
    else:
        prices = prices.astype(float)
        ma = prices.rolling(window=20, min_periods=20).mean()
        std = prices.rolling(window=20, min_periods=20).std()
        band_width = 2 * std
        upper = ma + band_width
        narrow_mask = (band_width / ma).abs() <= 0.04
        upper = upper.where(narrow_mask)
    _BOLLINGER_CACHE[code] = upper
    return upper


def _rolling_high(code: str) -> pd.Series:
    if code in _HIGH_CACHE:
        return _HIGH_CACHE[code]

    prices = _load_prices().get(code)
    if prices is None:
        high = pd.Series(dtype=float)
    else:
        prices = prices.astype(float)
        high = prices.rolling(window=252, min_periods=252).max()
    _HIGH_CACHE[code] = high
    return high


def market(code: str, date: Any) -> bool:
    return True


def bollinger(code: str, date: Any) -> bool:
    date_ts = pd.to_datetime(date)
    if pd.isna(date_ts):
        return False

    price_series = _load_prices().get(code)
    if price_series is None or date_ts not in price_series.index:
        return False

    price = price_series.loc[date_ts]
    if not np.isfinite(price):
        return False

    upper = _bollinger_upper(code)
    if date_ts not in upper.index:
        return False
    upper_value = upper.loc[date_ts]
    if not np.isfinite(upper_value):
        return False

    if USE_52W_HIGH_FILTER:
        high = _rolling_high(code)
        if date_ts not in high.index:
            return False
        high_value = high.loc[date_ts]
        if not np.isfinite(high_value) or high_value <= 0:
            return False
        if price < 0.9 * high_value:
            return False

    return bool(price > upper_value)
