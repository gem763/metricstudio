"""Fully numpy + numba backtesting pipeline."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Tuple, Optional

import numpy as np
import pandas as pd
from numba import njit
from tqdm.auto import tqdm

from src.db_manager import DB
from src.pattern import Pattern
from src.stats import Stats, StatsCollection

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


@njit(cache=True)
def _numba_accumulate_occurrences(mask, start_idx, end_idx, occurrence_counts):
    if end_idx < start_idx:
        end_idx = start_idx
    length = len(mask)
    lo = max(0, start_idx)
    hi = min(end_idx, length)
    for i in range(lo, hi):
        if mask[i]:
            occurrence_counts[i] += 1


@njit(cache=True)
def _numba_quantile_linear_sorted(sorted_vals, n, q):
    if n <= 0:
        return np.nan
    if q <= 0.0:
        return sorted_vals[0]
    if q >= 1.0:
        return sorted_vals[n - 1]
    pos = (n - 1) * q
    lo = int(np.floor(pos))
    hi = int(np.ceil(pos))
    if lo == hi:
        return sorted_vals[lo]
    w = pos - lo
    return sorted_vals[lo] * (1.0 - w) + sorted_vals[hi] * w


@njit(cache=True)
def _numba_accumulate_trim_for_date(
    prices,
    mask_row,
    date_idx,
    horizon_offsets,
    trim_q,
    counts,
    sum_ret,
    sum_log,
    pos_counts,
    geom_invalid,
    daily_arith,
    daily_geom,
    daily_rise,
):
    num_dates = prices.shape[0]
    num_codes = prices.shape[1]
    num_h = len(horizon_offsets)
    returns_buf = np.empty(num_codes, dtype=np.float64)

    for h_idx in range(num_h):
        step = int(horizon_offsets[h_idx])
        fwd_idx = date_idx + step
        if fwd_idx >= num_dates:
            continue

        n = 0
        for code_idx in range(num_codes):
            if not mask_row[code_idx]:
                continue

            base = prices[date_idx, code_idx]
            if not np.isfinite(base) or base <= 0.0:
                continue

            fwd = prices[fwd_idx, code_idx]
            if not np.isfinite(fwd) or fwd <= 0.0:
                continue

            returns_buf[n] = fwd / base - 1.0
            n += 1

        if n == 0:
            continue

        sorted_vals = np.sort(returns_buf[:n])
        low = _numba_quantile_linear_sorted(sorted_vals, n, trim_q)
        high = _numba_quantile_linear_sorted(sorted_vals, n, 1.0 - trim_q)

        kept_count = 0
        kept_pos = 0
        kept_sum_ret = 0.0
        kept_sum_log = 0.0
        has_geom_invalid = False

        for k in range(n):
            ret = returns_buf[k]
            if ret < low or ret > high:
                continue
            kept_count += 1
            kept_sum_ret += ret
            if ret > 0.0:
                kept_pos += 1
            if ret <= -1.0:
                has_geom_invalid = True
            else:
                kept_sum_log += np.log1p(ret)

        if kept_count == 0:
            continue

        counts[h_idx, date_idx] = kept_count
        pos_counts[h_idx, date_idx] = kept_pos
        sum_ret[h_idx, date_idx] = kept_sum_ret
        daily_arith[h_idx, date_idx] = kept_sum_ret / kept_count
        daily_rise[h_idx, date_idx] = kept_pos / kept_count

        if has_geom_invalid:
            geom_invalid[h_idx, date_idx] = True
            continue

        sum_log[h_idx, date_idx] = kept_sum_log
        daily_geom[h_idx, date_idx] = np.exp(kept_sum_log / kept_count) - 1.0


def _infer_pattern_label(pattern_fn: Pattern, idx: int) -> str:
    name = getattr(pattern_fn, "name", None)
    if isinstance(name, str) and name:
        return name
    return f"pattern_{idx}"


def _normalize_trim_quantile(trim: float | None) -> float | None:
    if trim is None:
        return None
    value = float(trim)
    if not np.isfinite(value) or value < 0.0 or value >= 0.5:
        raise ValueError("trim 값은 [0.0, 0.5) 범위여야 합니다.")
    return value


def _infer_pattern_trim(pattern_fn: Pattern) -> float | None:
    return _normalize_trim_quantile(getattr(pattern_fn, "trim", None))


class Backtest:
    def __init__(
        self,
        start,
        end,
        benchmark: Pattern | None = None,
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
        if benchmark is not None and not isinstance(benchmark, Pattern):
            raise TypeError("benchmark는 Pattern 객체여야 합니다.")
        self.benchmark = benchmark
        self._base_stats = {}
        if benchmark is not None:
            base_name = _infer_pattern_label(benchmark, 0)
            base_trim = _infer_pattern_trim(benchmark)
            self._base_stats[base_name] = self._run_pattern(
                benchmark,
                trim_quantile=base_trim,
                progress_label=base_name,
            )

    @staticmethod
    def _compute_mask(pattern_fn: Pattern, values: np.ndarray, code: str) -> np.ndarray | None:
        mask = pattern_fn(values)
        if mask is None:
            return None
        mask_arr = np.asarray(mask, dtype=np.bool_)
        if mask_arr.shape != values.shape:
            raise ValueError(f"패턴 mask shape이 종목 코드 {code}의 가격 배열 shape과 일치하지 않습니다.")
        return mask_arr

    def _run_pattern_normal(self, pattern_fn: Pattern, progress_label: str) -> Stats:
        stats = Stats.create(self.dates, HORIZONS)
        for col_idx, code in enumerate(tqdm(self.codes, desc=f"{progress_label} | codes")):
            values = self.prices[:, col_idx]
            mask = self._compute_mask(pattern_fn, values, code)
            if mask is None:
                continue
            _numba_accumulate_occurrences(
                mask,
                self.start_idx,
                self.end_idx,
                stats.occurrence_counts,
            )
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

    def _build_mask_matrix(self, pattern_fn: Pattern, eval_len: int) -> np.ndarray:
        num_codes = len(self.codes)
        mask_matrix = np.zeros((eval_len, num_codes), dtype=np.bool_)
        if eval_len == 0:
            return mask_matrix

        for col_idx, code in enumerate(self.codes):
            values = self.prices[:, col_idx]
            mask = self._compute_mask(pattern_fn, values, code)
            if mask is None:
                continue
            mask_matrix[:, col_idx] = mask[self.start_idx:self.end_idx]
        return mask_matrix

    def _accumulate_trim_dates(
        self,
        mask_matrix: np.ndarray,
        trim_q: float,
        stats: Stats,
        progress_label: str,
    ) -> None:
        daily_arith = stats.daily_arith
        daily_geom = stats.daily_geom
        daily_rise = stats.daily_rise
        if daily_arith is None or daily_geom is None or daily_rise is None:
            raise ValueError("trim 모드에서는 daily 통계 버퍼가 필요합니다.")

        for i_local in tqdm(range(mask_matrix.shape[0]), desc=f"{progress_label} | trim"):
            i = self.start_idx + i_local
            _numba_accumulate_trim_for_date(
                self.prices,
                mask_matrix[i_local],
                i,
                self.horizon_offsets,
                trim_q,
                stats.counts,
                stats.sum_ret,
                stats.sum_log,
                stats.pos_counts,
                stats.geom_invalid,
                daily_arith,
                daily_geom,
                daily_rise,
            )

    def _run_pattern_trim(self, pattern_fn: Pattern, trim_q: float, progress_label: str) -> Stats:
        stats = Stats.create_daily(self.dates, HORIZONS)
        eval_len = max(0, self.end_idx - self.start_idx)
        mask_matrix = self._build_mask_matrix(pattern_fn, eval_len)
        if eval_len > 0:
            stats.occurrence_counts[self.start_idx:self.end_idx] = np.sum(
                mask_matrix,
                axis=1,
                dtype=np.int64,
            )
        self._accumulate_trim_dates(mask_matrix, trim_q, stats, progress_label)
        return stats

    def _run_pattern(
        self,
        pattern_fn: Pattern,
        trim_quantile: float | None = None,
        progress_label: str = "pattern",
    ) -> Stats:
        trim_q = _normalize_trim_quantile(trim_quantile)
        if trim_q is None or trim_q <= 0.0:
            return self._run_pattern_normal(pattern_fn, progress_label)
        return self._run_pattern_trim(pattern_fn, trim_q, progress_label)

    def run(self, *patterns: Pattern, include_base: bool = True) -> StatsCollection:
        if not patterns and include_base and self.benchmark is not None:
            return StatsCollection(
                dict(self._base_stats),
                benchmark_names=set(self._base_stats.keys()),
            )

        stats_map: Dict[str, Stats] = {}
        benchmark_names: set[str] = set()
        if include_base:
            stats_map.update(self._base_stats)
            benchmark_names = set(self._base_stats.keys())

        for idx, pattern_fn in enumerate(patterns, start=len(stats_map) + 1):
            if not isinstance(pattern_fn, Pattern):
                raise TypeError("run()에 전달한 모든 패턴은 Pattern 객체여야 합니다.")
            base_name = _infer_pattern_label(pattern_fn, idx)
            trim_q = _infer_pattern_trim(pattern_fn)
            stats = self._run_pattern(
                pattern_fn,
                trim_quantile=trim_q,
                progress_label=base_name,
            )
            name = base_name
            suffix = 2
            while name in stats_map:
                name = f"{base_name}_{suffix}"
                suffix += 1
            stats_map[name] = stats

        if not stats_map:
            raise ValueError("실행된 패턴이 없습니다.")
        return StatsCollection(stats_map, benchmark_names=benchmark_names)
