from __future__ import annotations

from pathlib import Path
from typing import Callable, Iterable, List, Tuple

import numpy as np
import pandas as pd
from tqdm.auto import tqdm

from src.db import DB
from IPython.core.debugger import set_trace

PatternFn = Callable[[str, pd.Timestamp], bool]

DEFAULT_HORIZONS: List[Tuple[str, int]] = [
    ("1D", 1),
    ("1W", 5),
    ("2W", 10),
    ("3W", 15),
    ("6W", 30),
    ("3M", 60),
    ("6M", 120),
]


def _load_prices(prices_pkl: str | Path | None) -> pd.DataFrame:
    if prices_pkl is None:
        series = DB().load()
    else:
        series = pd.read_pickle(prices_pkl)
    if not isinstance(series, pd.Series):
        raise TypeError("Expected a pandas Series for adjclose data.")
    if series.index.nlevels != 2:
        raise ValueError("Series index must be a (date, code) MultiIndex.")

    df = series.unstack("code")
    df.index = pd.to_datetime(df.index)
    df = df.sort_index()
    return df


def _normalize_horizons(
    horizons: Iterable[int] | Iterable[Tuple[str, int]] | dict[str, int] | None,
) -> List[Tuple[str, int]]:
    if horizons is None:
        return DEFAULT_HORIZONS

    if isinstance(horizons, dict):
        return [(label, int(days)) for label, days in horizons.items()]

    horizons_list = list(horizons)
    if not horizons_list:
        return []

    if isinstance(horizons_list[0], tuple):
        return [(str(label), int(days)) for label, days in horizons_list]

    return [(f"{int(days)}D", int(days)) for days in horizons_list]


def _geom_mean(returns: List[float]) -> float:
    if not returns:
        return float("nan")
    arr = np.asarray(returns, dtype=float)
    if np.any(arr <= -1.0):
        return float("nan")

    return np.exp(np.log(arr+1.0).sum() / len(arr)) - 1.0


def backtest(
    pattern_fn: PatternFn,
    prices_pkl: str | Path | None = None,
    horizons: Iterable[int] | Iterable[Tuple[str, int]] | dict[str, int] | None = None,
    use_tqdm: bool = True,
    start_date: str | pd.Timestamp | None = None,
    end_date: str | pd.Timestamp | None = None,
) -> pd.DataFrame:
    if prices_pkl is not None:
        prices_pkl = Path(prices_pkl)

    horizons_list = _normalize_horizons(horizons)

    price_df = _load_prices(prices_pkl)
    # set_trace()
    dates = price_df.index
    start_ts = pd.to_datetime(start_date) if start_date is not None else None
    end_ts = pd.to_datetime(end_date) if end_date is not None else None

    returns_by_horizon = {label: [] for label, _ in horizons_list}

    code_iter = price_df.columns
    if use_tqdm:
        code_iter = tqdm(code_iter, desc="codes", unit="code")

    for code in code_iter:
        series = price_df[code]
        values = series.to_numpy(dtype=float)
        if np.all(np.isnan(values)):
            continue

        for i, price in enumerate(values):
            if not np.isfinite(price) or price <= 0:
                continue
            date = dates[i]
            if start_ts is not None and date < start_ts:
                continue
            if end_ts is not None and date > end_ts:
                continue
            if not pattern_fn(str(code), date):
                continue

            for label, days in horizons_list:
                j = i + int(days)
                if j >= len(values):
                    continue
                fwd = values[j]
                if not np.isfinite(fwd) or fwd <= 0:
                    continue
                ret = fwd / price - 1.0
                if not np.isfinite(ret) or ret <= -1.0:
                    continue
                returns_by_horizon[label].append(float(ret))

    rows = []
    for label, _ in horizons_list:
        values = returns_by_horizon[label]
        arith_mean = float(np.mean(values)) if values else float("nan")
        geom_mean = _geom_mean(values)
        rise_prob = float(np.mean([v > 0 for v in values])) if values else float("nan")
        rows.append(
            {
                "period": label,
                "count": len(values),
                "arith_mean": arith_mean,
                "geom_mean": geom_mean,
                "rise_prob": rise_prob,
            }
        )

    return pd.DataFrame(rows).set_index("period"), returns_by_horizon
