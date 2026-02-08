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
from src.pattern import Default
from src.stats import Stats, StatsCollection

PatternArrayFn = Callable[[np.ndarray], np.ndarray]

HORIZONS: List[Tuple[str, int]] = [
    # ("1D", 1),
    ("1W", 5),
    ("2W", 10),
    ("3W", 15),
    ("1M", 20),
    ("2M", 40),
    ("3M", 60),
    ("6M", 120),
]


@dataclass
class PriceTable:
    dates: np.ndarray  # shape (T,)
    prices: np.ndarray  # shape (T, N)
    codes: List[str]


_PRICE_TABLE: Optional[PriceTable] = None


def _filter_bad_codes(
    df: pd.DataFrame,
    max_daily_ret: float = 2.0,
    min_price: float = 1.0,
) -> pd.DataFrame:
    # 비정상 급등락 또는 비정상 가격(예: 1원)을 포함한 종목 제거
    daily_ret = df.pct_change()
    bad_ret = daily_ret.abs() > max_daily_ret
    bad_price = df <= min_price
    bad_codes = bad_ret.any() | bad_price.any()
    if bad_codes.any():
        df = df.loc[:, ~bad_codes]
    return df


def _load_price_table() -> PriceTable:
    global _PRICE_TABLE
    if _PRICE_TABLE is None:
        series = DB().load()
        df = series.unstack("code").sort_index()
        df.index = pd.to_datetime(df.index)
        df = _filter_bad_codes(df)
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


class Backtest:
    def __init__(
        self,
        start,
        end,
        benchmark: PatternArrayFn | None = None,
    ):
        if benchmark is None:
            benchmark = Default(name='benchmark')

        self.start = pd.Timestamp(start)
        self.end = pd.Timestamp(end)
        table = _load_price_table()
        self.dates = table.dates
        self.prices = table.prices
        self.codes = table.codes
        self.horizon_offsets = np.asarray([int(days) for _, days in HORIZONS], dtype=np.int64)
        self.start_idx = int(np.searchsorted(self.dates, self.start.to_datetime64(), side="left"))
        self.end_idx = int(np.searchsorted(self.dates, self.end.to_datetime64(), side="right"))
        self.end_idx = min(self.end_idx, len(self.dates))
        self.benchmark = benchmark
        self._base_stats = {}
        base_name = _infer_pattern_label(benchmark, 0)
        self._base_stats[base_name] = self._run_pattern(benchmark)

    def _run_pattern(self, pattern_fn: PatternArrayFn) -> Stats:
        stats = Stats.create(self.dates, HORIZONS)
        for col_idx, code in enumerate(tqdm(self.codes, desc="codes")):
            values = self.prices[:, col_idx]
            mask = pattern_fn(values)
            if mask is None:
                continue
            if mask.shape != values.shape:
                raise ValueError(f"pattern mask shape mismatch for code {code}")
            _numba_accumulate_returns(
                values,
                mask,
                self.start_idx,
                self.end_idx,
                self.horizon_offsets,
                stats.counts,
                stats.sum_ret,
                stats.sum_log,
                stats.pos_counts,
                stats.geom_invalid,
            )
        return stats

    def run(self, *patterns: PatternArrayFn, include_base: bool = True, **shared_kwargs) -> StatsCollection:
        stats_map: Dict[str, Stats] = {}
        if include_base:
            stats_map.update(self._base_stats)

        for idx, pattern_fn in enumerate(patterns, start=len(stats_map) + 1):
            wrapped = pattern_fn
            if shared_kwargs:
                def _wrapped(values, _fn=pattern_fn, _kwargs=shared_kwargs):
                    return _fn(values, **_kwargs)

                wrapped = _wrapped
            stats = self._run_pattern(wrapped)
            base_name = _infer_pattern_label(pattern_fn, idx)
            name = base_name
            suffix = 2
            while name in stats_map:
                name = f"{base_name}_{suffix}"
                suffix += 1
            stats_map[name] = stats

        if not stats_map:
            raise ValueError("No patterns were executed.")
        return StatsCollection(stats_map)
