from __future__ import annotations
from typing import Callable, Iterable, List, Optional, Tuple, Dict
import multiprocessing as mp
from tqdm.auto import tqdm
from src.db import DB
from IPython.core.debugger import set_trace

import numpy as np
import pandas as pd
from numba import njit

PatternFn = Callable[[str, pd.Timestamp], bool]
_price_df_cache: Optional[pd.DataFrame] = None

DEFAULT_HORIZONS: List[Tuple[str, int]] = [
    ("1D", 1),
    ("1W", 5),
    ("2W", 10),
    ("3W", 15),
    ("6W", 30),
    ("3M", 60),
    ("6M", 120),
]


def _load_prices():
    global _price_df_cache

    if _price_df_cache is None:
        series = DB().load()
        df = series.unstack("code")
        df.index = pd.to_datetime(df.index)
        _price_df_cache = df.sort_index()
    
    return _price_df_cache


def _geom_mean(returns: List[float]) -> float:
    if not returns:
        return float("nan")
    arr = np.asarray(returns, dtype=float)
    if np.any(arr <= -1.0):
        return float("nan")
    return np.exp(np.log(arr+1.0).sum() / len(arr)) - 1.0


@njit(cache=True)
def _numba_returns(values, valid_indices, horizon_offsets):
    num_h = len(horizon_offsets)
    num_idx = len(valid_indices)
    out = np.empty((num_h, num_idx), dtype=np.float64)
    out.fill(np.nan)
    length = len(values)

    for idx_pos in range(num_idx):
        i = valid_indices[idx_pos]
        price = values[i]
        if not np.isfinite(price) or price <= 0:
            continue
        for h_idx in range(num_h):
            step = horizon_offsets[h_idx]
            j = i + step
            if j >= length:
                continue
            fwd = values[j]
            if not np.isfinite(fwd) or fwd <= 0:
                continue
            out[h_idx, idx_pos] = fwd / price - 1.0

    return out


def _calc_stats(values: List[float]) -> tuple[float, float, float, float]:
    count = len(values)
    if count == 0:
        return 0.0, float("nan"), float("nan"), float("nan")
    arith_mean = float(np.mean(values))
    geom_mean = _geom_mean(values)
    rise_prob = float(np.mean([v > 0 for v in values]))
    return float(count), arith_mean, geom_mean, rise_prob


def measure(returns_by_horizon):
    rows = []
    for label, entries in returns_by_horizon.items():
        values = [ret for _, ret in entries]
        count, arith_mean, geom_mean, rise_prob = _calc_stats(values)
        rows.append(
            {
                "period": label,
                "scope": "overall",
                "count": count,
                "arith_mean": arith_mean,
                "geom_mean": geom_mean,
                "rise_prob": rise_prob,
            }
        )

        yearly: Dict[str, List[float]] = {}
        for year, ret in entries:
            yearly.setdefault(str(year), []).append(ret)
        for year in sorted(yearly.keys()):
            vals = yearly[year]
            count_y, arith_y, geom_y, rise_y = _calc_stats(vals)
            rows.append(
                {
                    "period": label,
                    "scope": year,
                    "count": count_y,
                    "arith_mean": arith_y,
                    "geom_mean": geom_y,
                    "rise_prob": rise_y,
                }
            )

    return pd.DataFrame(rows).set_index(["period", "scope"])


def _compute_code_returns(args):
    (
        code,
        values,
        dates,
        start_idx,
        end_idx,
        horizons_list,
        horizon_offsets,
        pattern_fn,
        use_numba,
    ) = args

    partial = {label: [] for label, _ in horizons_list}

    if np.all(np.isnan(values)):
        return partial

    valid_indices = []
    for i in range(start_idx, end_idx):
        price = values[i]
        if not np.isfinite(price) or price <= 0:
            continue
        date = dates[i]

        if not pattern_fn(code, date):
            continue
        valid_indices.append(i)

    if not valid_indices:
        return partial
    years = [int(dates[i].year) for i in valid_indices]

    if use_numba:
        valid_idx_arr = np.asarray(valid_indices, dtype=np.int64)
        ret_matrix = _numba_returns(values, valid_idx_arr, horizon_offsets)
        for h_idx, (label, _) in enumerate(horizons_list):
            horizon_returns = ret_matrix[h_idx]
            for idx_pos, ret in enumerate(horizon_returns):
                if not np.isfinite(ret):
                    continue
                partial[label].append((years[idx_pos], float(ret)))
        return partial

    for idx_pos, i in enumerate(valid_indices):
        price = values[i]
        for label, days in horizons_list:
            j = i + int(days)
            if j >= len(values):
                continue
            fwd = values[j]
            if not np.isfinite(fwd) or fwd <= 0:
                continue
            ret = fwd / price - 1.0
            partial[label].append((years[idx_pos], float(ret)))

    return partial


def backtest(
    pattern_fn: PatternFn,
    start,
    end,
    core: Optional[int] = None,
    chunksize: Optional[int] = None,
    use_numba: bool = False,
):
    horizons_list = DEFAULT_HORIZONS
    price_df = _load_prices()

    total_codes = len(price_df.columns)
    if core is None:
        core = mp.cpu_count() or 1

    dates = price_df.index
    start = pd.to_datetime(start)
    end = pd.to_datetime(end)

    start_idx = int(dates.searchsorted(start, side="left"))
    end_idx = int(dates.searchsorted(end, side="right"))
    end_idx = min(end_idx, len(dates))

    returns_by_horizon = {label: [] for label, _ in horizons_list}
    horizon_offsets = np.asarray([int(days) for _, days in horizons_list], dtype=np.int64)

    def build_args(code):
        return (
            str(code),
            price_df[code].to_numpy(dtype=float),
            dates,
            start_idx,
            end_idx,
            horizons_list,
            horizon_offsets,
            pattern_fn,
            use_numba,
        )

    if core <= 1:
        print("serial processing")
        for code in tqdm(price_df.columns):
            partial = _compute_code_returns(build_args(code))
            for label in returns_by_horizon:
                returns_by_horizon[label].extend(partial[label])
        return measure(returns_by_horizon)

    print("parallel processing")
    if chunksize is None:
        denom = max(1, core * 8)
        chunksize = max(1, (total_codes + denom - 1) // denom)

    ctx = mp.get_context("spawn")
    tasks = (build_args(code) for code in price_df.columns)

    with ctx.Pool(processes=core) as pool:
        iterator = pool.imap_unordered(_compute_code_returns, tasks, max(chunksize, 1))
        for partial in tqdm(iterator, total=total_codes):
            for label in returns_by_horizon:
                returns_by_horizon[label].extend(partial[label])

    return measure(returns_by_horizon)
