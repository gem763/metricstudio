"""Fully numpy + numba backtesting pipeline."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Dict, List, Tuple, Optional
import inspect

import numpy as np
import pandas as pd
from numba import njit
from tqdm.auto import tqdm

from src.db_manager import DB
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


def _load_price_table() -> PriceTable:
    global _PRICE_TABLE
    if _PRICE_TABLE is None:
        # DB 기본 경로: db/stock/close.parquet 또는 db/stock/data/*.parquet
        df = DB().load(field="close")
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


def _infer_pattern_trim(pattern_fn: PatternArrayFn) -> float | None:
    candidate = getattr(pattern_fn, "trim", None)
    if candidate is None:
        keywords = getattr(pattern_fn, "keywords", None)
        if isinstance(keywords, dict):
            candidate = keywords.get("trim")
    if candidate is None:
        return None
    value = float(candidate)
    if not np.isfinite(value) or value < 0.0 or value >= 0.5:
        raise ValueError("trim must be in [0.0, 0.5).")
    return value


def _collect_forward_returns(
    values: np.ndarray,
    mask: np.ndarray,
    start_idx: int,
    end_idx: int,
    horizon_offsets: np.ndarray,
) -> tuple[list[np.ndarray], list[np.ndarray]]:
    prices = np.asarray(values, dtype=np.float64)
    valid_mask = np.asarray(mask, dtype=np.bool_)
    length = prices.shape[0]
    num_h = len(horizon_offsets)

    idx_by_h = [np.empty(0, dtype=np.int32) for _ in range(num_h)]
    ret_by_h = [np.empty(0, dtype=np.float32) for _ in range(num_h)]
    base_ok = valid_mask & np.isfinite(prices) & (prices > 0.0)

    for h_idx, step_raw in enumerate(horizon_offsets):
        step = int(step_raw)
        if step <= 0 or step >= length:
            continue

        valid_len = length - step
        base = prices[:valid_len]
        fwd = prices[step:]
        valid = base_ok[:valid_len] & np.isfinite(fwd) & (fwd > 0.0)

        lo = max(0, int(start_idx))
        hi = min(int(end_idx), valid_len)
        if lo >= valid_len or hi <= 0 or lo >= hi:
            continue
        if lo > 0:
            valid[:lo] = False
        if hi < valid_len:
            valid[hi:] = False

        date_idx = np.flatnonzero(valid)
        if date_idx.size == 0:
            continue

        ret = fwd[date_idx] / base[date_idx] - 1.0
        finite = np.isfinite(ret)
        if not np.all(finite):
            date_idx = date_idx[finite]
            ret = ret[finite]
        if date_idx.size == 0:
            continue

        idx_by_h[h_idx] = date_idx.astype(np.int32, copy=False)
        ret_by_h[h_idx] = ret.astype(np.float32, copy=False)

    return idx_by_h, ret_by_h


class Backtest:
    def __init__(
        self,
        start,
        end,
        benchmark: PatternArrayFn | None = None,
    ):
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
        self._base_trims = {}
        if benchmark is not None:
            base_name = _infer_pattern_label(benchmark, 0)
            trim_quantile = _infer_pattern_trim(benchmark)
            self._base_stats[base_name] = self._run_pattern(
                benchmark,
                keep_event_returns=trim_quantile is not None and trim_quantile > 0.0,
            )
            self._base_trims[base_name] = trim_quantile

    def _run_pattern(
        self,
        pattern_fn: PatternArrayFn,
        keep_event_returns: bool = False,
        progress_desc: str = "codes",
    ) -> Stats:
        stats = Stats.create(self.dates, HORIZONS, keep_event_returns=keep_event_returns)
        num_h = len(self.horizon_offsets)
        if keep_event_returns:
            event_idx_chunks: list[list[np.ndarray]] = [[] for _ in range(num_h)]
            event_ret_chunks: list[list[np.ndarray]] = [[] for _ in range(num_h)]
        for col_idx, code in enumerate(tqdm(self.codes, desc=progress_desc)):
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
            if keep_event_returns:
                idx_by_h, ret_by_h = _collect_forward_returns(
                    values,
                    mask,
                    self.start_idx,
                    self.end_idx,
                    self.horizon_offsets,
                )
                for h_idx in range(num_h):
                    if idx_by_h[h_idx].size == 0:
                        continue
                    event_idx_chunks[h_idx].append(idx_by_h[h_idx])
                    event_ret_chunks[h_idx].append(ret_by_h[h_idx])

        if keep_event_returns:
            event_date_idx_by_horizon: list[np.ndarray] = []
            event_returns_by_horizon: list[np.ndarray] = []
            for h_idx in range(num_h):
                if not event_idx_chunks[h_idx]:
                    event_date_idx_by_horizon.append(np.empty(0, dtype=np.int32))
                    event_returns_by_horizon.append(np.empty(0, dtype=np.float32))
                    continue
                date_idx = np.concatenate(event_idx_chunks[h_idx]).astype(np.int32, copy=False)
                returns = np.concatenate(event_ret_chunks[h_idx]).astype(np.float32, copy=False)
                order = np.argsort(date_idx, kind="mergesort")
                event_date_idx_by_horizon.append(date_idx[order])
                event_returns_by_horizon.append(returns[order])
            stats.event_date_idx_by_horizon = event_date_idx_by_horizon
            stats.event_returns_by_horizon = event_returns_by_horizon
        return stats

    def run(self, *patterns: PatternArrayFn, include_base: bool = True, **shared_kwargs) -> StatsCollection:
        stats_map: Dict[str, Stats] = {}
        pattern_trims: Dict[str, float | None] = {}
        if include_base:
            stats_map.update(self._base_stats)
            pattern_trims.update(self._base_trims)

        for idx, pattern_fn in enumerate(patterns, start=len(stats_map) + 1):
            trim_quantile = _infer_pattern_trim(pattern_fn)
            base_name = _infer_pattern_label(pattern_fn, idx)
            name = base_name
            suffix = 2
            while name in stats_map:
                name = f"{base_name}_{suffix}"
                suffix += 1

            wrapped = pattern_fn
            if shared_kwargs:
                def _wrapped(values, _fn=pattern_fn, _kwargs=shared_kwargs):
                    return _fn(values, **_kwargs)

                wrapped = _wrapped
            stats = self._run_pattern(
                wrapped,
                keep_event_returns=trim_quantile is not None and trim_quantile > 0.0,
                progress_desc=f"codes:{name}",
            )

            stats_map[name] = stats
            pattern_trims[name] = trim_quantile

        if not stats_map:
            raise ValueError("No patterns were executed.")
        return StatsCollection(stats_map, pattern_trims=pattern_trims)
