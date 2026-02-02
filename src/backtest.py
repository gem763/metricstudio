"""Fully numpy + numba backtesting pipeline."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Dict, List, Tuple, Optional
import inspect

import numpy as np
import pandas as pd
from numba import njit
from tqdm.auto import tqdm

from src.db import DB
from src.stats import Stats, StatsCollection

PatternArrayFn = Callable[[np.ndarray], np.ndarray]

HORIZONS: List[Tuple[str, int]] = [
    ("1D", 1),
    ("1W", 5),
    ("2W", 10),
    ("3W", 15),
    ("6W", 30),
    ("3M", 60),
    ("6M", 120),
]


@dataclass
class PriceTable:
    dates: np.ndarray  # shape (T,)
    prices: np.ndarray  # shape (T, N)
    codes: List[str]


_PRICE_TABLE: Optional[PriceTable] = None


def _load_price_table() -> PriceTable:
    global _PRICE_TABLE
    if _PRICE_TABLE is None:
        series = DB().load()
        df = series.unstack("code").sort_index()
        df.index = pd.to_datetime(df.index)
        dates = df.index.to_numpy(dtype="datetime64[ns]")
        prices = df.to_numpy(dtype=np.float64, copy=True)
        codes = [str(c) for c in df.columns]
        _PRICE_TABLE = PriceTable(dates=dates, prices=prices, codes=codes)
    return _PRICE_TABLE




@njit(cache=True)
def _numba_accumulate_returns(
    values,
    mask,
    start_idx,
    end_idx,
    horizon_offsets,
    counts,
    sum_ret,
    sum_log,
    pos_counts,
    geom_invalid,
):
    if end_idx < start_idx:
        end_idx = start_idx
    length = len(values)
    num_h = len(horizon_offsets)

    for i in range(start_idx, end_idx):
        if not mask[i]:
            continue
        base = values[i]
        if not np.isfinite(base) or base <= 0:
            continue
        for h_idx in range(num_h):
            step = horizon_offsets[h_idx]
            j = i + step
            if j >= length:
                continue
            fwd = values[j]
            if not np.isfinite(fwd) or fwd <= 0:
                continue
            ret = fwd / base - 1.0
            counts[h_idx, i] += 1
            sum_ret[h_idx, i] += ret
            if ret > 0:
                pos_counts[h_idx, i] += 1
            if ret <= -1.0:
                geom_invalid[h_idx, i] = True
            else:
                sum_log[h_idx, i] += np.log1p(ret)


def _backtest_single(pattern_fn: PatternArrayFn, start, end) -> Stats:
    table = _load_price_table()
    dates = table.dates
    prices = table.prices
    codes = table.codes

    horizon_offsets = np.asarray([int(days) for _, days in HORIZONS], dtype=np.int64)

    start_ts = pd.Timestamp(start).to_datetime64()
    end_ts = pd.Timestamp(end).to_datetime64()

    start_idx = int(np.searchsorted(dates, start_ts, side="left"))
    end_idx = int(np.searchsorted(dates, end_ts, side="right"))
    end_idx = min(end_idx, len(dates))

    stats = Stats.create(dates, HORIZONS)

    for col_idx, code in enumerate(tqdm(codes)):
        values = prices[:, col_idx]
        mask = pattern_fn(values)
        if mask is None:
            continue
        if mask.shape != values.shape:
            raise ValueError(f"pattern mask shape mismatch for code {code}")

        _numba_accumulate_returns(
            values,
            mask,
            start_idx,
            end_idx,
            horizon_offsets,
            stats.counts,
            stats.sum_ret,
            stats.sum_log,
            stats.pos_counts,
            stats.geom_invalid,
        )

    return stats


def _infer_pattern_label(pattern_fn: PatternArrayFn, idx: int) -> str:
    import inspect

    keywords = getattr(pattern_fn, "keywords", None)
    if isinstance(keywords, dict):
        provided = keywords.get("name")
        if isinstance(provided, str) and provided:
            return provided
    attr_name = getattr(pattern_fn, "__name__", None)
    if attr_name and attr_name != "<lambda>":
        return attr_name
    # attempt to find the variable name used at the call site
    frame_records = inspect.stack()
    try:
        for frame_info in frame_records[2:6]:
            frame = frame_info.frame
            try:
                for var_name, value in frame.f_locals.items():
                    if value is pattern_fn and var_name not in {"pattern_fn", "pattern_fns"}:
                        return var_name
            finally:
                del frame
    finally:
        del frame_records
    return f"pattern_{idx}"


def backtest(*pattern_fns: PatternArrayFn, start, end) -> StatsCollection:
    if not pattern_fns:
        raise ValueError("At least one pattern function must be provided.")

    stats_map: Dict[str, Stats] = {}
    for idx, pattern_fn in enumerate(pattern_fns, start=1):
        stats = _backtest_single(pattern_fn, start, end)
        base = _infer_pattern_label(pattern_fn, idx)
        name = base
        base = name
        suffix = 2
        while name in stats_map:
            name = f"{base}_{suffix}"
            suffix += 1
        stats_map[name] = stats

    return StatsCollection(stats_map)
