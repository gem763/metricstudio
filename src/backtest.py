"""Fully numpy + numba backtesting pipeline."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Tuple, Optional
import re

import numpy as np
import pandas as pd
from numba import njit
from tqdm.auto import tqdm

from src.db_manager import DB
from src.pattern import Pattern
from src.simulate import Simulator
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

TRIM_MODE_REMOVE = 0
TRIM_MODE_WINSORIZE = 1


@dataclass
class StockTable:
    dates: np.ndarray  # shape (T,)
    prices: np.ndarray  # shape (T, N)
    codes: List[str]


_STOCK_TABLE: Optional[StockTable] = None
_MARKET_TABLE: Dict[str, pd.DataFrame] = {}


def _load_stock_table() -> StockTable:
    global _STOCK_TABLE
    if _STOCK_TABLE is None:
        # DB 기본 경로: db/stock/close.parquet 또는 db/stock/data/*.parquet
        df = DB().load_stock(field="close")
        dates = df.index.to_numpy(dtype="datetime64[ns]")
        prices = df.to_numpy(dtype=np.float64, copy=True)
        codes = [str(c) for c in df.columns]
        _STOCK_TABLE = StockTable(dates=dates, prices=prices, codes=codes)
    return _STOCK_TABLE


def _load_market_table(market: str) -> pd.DataFrame:
    key = str(market).strip().lower()
    if not key:
        raise ValueError("market은 비어 있을 수 없습니다.")

    if key not in _MARKET_TABLE:
        df = DB().load_market(market=key)
        if not isinstance(df, pd.DataFrame):
            raise TypeError("load_market()은 DataFrame을 반환해야 합니다.")
        _MARKET_TABLE[key] = df

    return _MARKET_TABLE[key]


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
    trim_mode,
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

            if trim_mode == TRIM_MODE_REMOVE:
                if ret < low or ret > high:
                    continue
                adjusted = ret
            else:
                if ret < low:
                    adjusted = low
                elif ret > high:
                    adjusted = high
                else:
                    adjusted = ret

            kept_count += 1
            kept_sum_ret += adjusted
            if adjusted > 0.0:
                kept_pos += 1
            if adjusted <= -1.0:
                has_geom_invalid = True
            else:
                kept_sum_log += np.log1p(adjusted)

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


def _normalize_trim_method(method: str | None) -> str:
    method_text = str(method or "remove").lower()
    if method_text not in {"remove", "winsorize"}:
        raise ValueError("trim method는 'remove' 또는 'winsorize'여야 합니다.")
    return method_text


def _trim_mode_from_method(method: str) -> int:
    if method == "remove":
        return TRIM_MODE_REMOVE
    if method == "winsorize":
        return TRIM_MODE_WINSORIZE
    raise ValueError("trim method는 'remove' 또는 'winsorize'여야 합니다.")


def _infer_pattern_trim_config(pattern_fn: Pattern) -> tuple[float | None, str]:
    trim_q = _normalize_trim_quantile(getattr(pattern_fn, "trim_quantile", None))
    trim_method = _normalize_trim_method(getattr(pattern_fn, "trim_method", "remove"))
    return trim_q, trim_method


def _parse_lookback_window(lookback: int | str) -> int:
    if isinstance(lookback, (int, np.integer)):
        if lookback <= 0:
            raise ValueError("lookback은 1 이상이어야 합니다.")
        return int(lookback)

    text = str(lookback).strip().upper()
    m = re.fullmatch(r"(\d+)([DWMY])", text)
    if m is None:
        raise ValueError("lookback은 양의 정수 또는 '20D'/'12W'/'6M'/'1Y' 형식이어야 합니다.")
    value = int(m.group(1))
    unit = m.group(2)
    if value <= 0:
        raise ValueError("lookback 값은 1 이상이어야 합니다.")
    if unit == "D":
        return value
    if unit == "W":
        return value * 5
    if unit == "M":
        return value * 21
    if unit == "Y":
        return value * 252
    raise ValueError("지원하지 않는 lookback 단위입니다. D/W/M/Y만 사용 가능합니다.")


class Backtest:
    def __init__(
        self,
        start,
        end,
        benchmark: Pattern | None = None,
    ):
        self.start = pd.Timestamp(start)
        self.end = pd.Timestamp(end)
        table = _load_stock_table()
        self.dates = table.dates
        self.prices = table.prices
        self.codes = table.codes
        self._market_values_cache: Dict[tuple[str, str], np.ndarray] = {}
        self.horizon_offsets = np.asarray([int(days) for _, days in HORIZONS], dtype=np.int64)
        self.start_idx = int(np.searchsorted(self.dates, self.start.to_datetime64(), side="left"))
        self.end_idx = int(np.searchsorted(self.dates, self.end.to_datetime64(), side="right"))
        self.end_idx = min(self.end_idx, len(self.dates))
        if benchmark is not None and not isinstance(benchmark, Pattern):
            raise TypeError("benchmark는 Pattern 객체여야 합니다.")
        self.benchmark = benchmark
        self._base_stats = {}
        self._analyzed_patterns: Dict[str, Pattern] = {}
        self._analyzed_stats: Dict[str, Stats] = {}
        self._last_stats_collection: StatsCollection | None = None
        self._pattern_mask_cache: Dict[str, np.ndarray] = {}
        self._all_stock_geom_cache: Dict[tuple[int, int], np.ndarray] = {}
        if benchmark is not None:
            base_name = _infer_pattern_label(benchmark, 0)
            base_trim_q, base_trim_method = _infer_pattern_trim_config(benchmark)
            self._base_stats[base_name] = self._run_pattern(
                benchmark,
                trim_quantile=base_trim_q,
                trim_method=base_trim_method,
                progress_label=base_name,
            )
            self._analyzed_patterns[base_name] = benchmark
            self._analyzed_stats[base_name] = self._base_stats[base_name]

    @staticmethod
    def _compute_mask(pattern_fn: Pattern, values: np.ndarray, code: str) -> np.ndarray | None:
        mask = pattern_fn(values)
        if mask is None:
            return None
        mask_arr = np.asarray(mask, dtype=np.bool_)
        if mask_arr.shape != values.shape:
            raise ValueError(f"패턴 mask shape이 종목 코드 {code}의 가격 배열 shape과 일치하지 않습니다.")
        return mask_arr

    def _get_market_values(self, market: str, field: str) -> np.ndarray:
        key = (str(market).strip().lower(), str(field).strip().lower())
        if not key[0]:
            raise ValueError("market은 비어 있을 수 없습니다.")
        if not key[1]:
            raise ValueError("field는 비어 있을 수 없습니다.")

        if key not in self._market_values_cache:
            df = _load_market_table(key[0])
            if key[1] not in df.columns:
                raise ValueError(
                    f"market='{key[0]}' 데이터에 field='{key[1]}' 컬럼이 없습니다."
                )
            series = pd.to_numeric(df[key[1]], errors="coerce")
            aligned = series.reindex(pd.DatetimeIndex(self.dates)).to_numpy(
                dtype=np.float64,
                copy=True,
            )
            self._market_values_cache[key] = aligned
        return self._market_values_cache[key]

    def _iter_pattern_nodes(self, pattern_fn: Pattern):
        seen: set[int] = set()
        stack: list[Pattern] = [pattern_fn]
        while stack:
            node = stack.pop()
            node_id = id(node)
            if node_id in seen:
                continue
            seen.add(node_id)
            yield node

            left = getattr(node, "left", None)
            right = getattr(node, "right", None)
            if isinstance(left, Pattern):
                stack.append(left)
            if isinstance(right, Pattern):
                stack.append(right)

    def _prepare_market_sources(self, pattern_fn: Pattern) -> None:
        for node in self._iter_pattern_nodes(pattern_fn):
            market_name = getattr(node, "market_name", None)
            if market_name is None:
                node._set_market_values(None)
                continue
            market_field = getattr(node, "market_field", "close")
            market_values = self._get_market_values(market_name, market_field)
            node._set_market_values(market_values)

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
        trim_mode: int,
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
                trim_mode,
                stats.counts,
                stats.sum_ret,
                stats.sum_log,
                stats.pos_counts,
                stats.geom_invalid,
                daily_arith,
                daily_geom,
                daily_rise,
            )

    def _run_pattern_trim(
        self,
        pattern_fn: Pattern,
        trim_q: float,
        trim_method: str,
        progress_label: str,
    ) -> Stats:
        stats = Stats.create_daily(self.dates, HORIZONS)
        eval_len = max(0, self.end_idx - self.start_idx)
        mask_matrix = self._build_mask_matrix(pattern_fn, eval_len)
        if eval_len > 0:
            stats.occurrence_counts[self.start_idx:self.end_idx] = np.sum(
                mask_matrix,
                axis=1,
                dtype=np.int64,
            )
        trim_mode = _trim_mode_from_method(trim_method)
        self._accumulate_trim_dates(mask_matrix, trim_q, trim_mode, stats, progress_label)
        return stats

    def _run_pattern(
        self,
        pattern_fn: Pattern,
        trim_quantile: float | None = None,
        trim_method: str = "remove",
        progress_label: str = "pattern",
    ) -> Stats:
        self._prepare_market_sources(pattern_fn)
        trim_q = _normalize_trim_quantile(trim_quantile)
        trim_method_text = _normalize_trim_method(trim_method)
        if trim_q is None or trim_q <= 0.0:
            return self._run_pattern_normal(pattern_fn, progress_label)
        return self._run_pattern_trim(pattern_fn, trim_q, trim_method_text, progress_label)

    @staticmethod
    def _resolve_horizon(h: str | int) -> tuple[str, int]:
        labels = [label for label, _ in HORIZONS]
        offsets = [int(days) for _, days in HORIZONS]

        if isinstance(h, str):
            key = str(h).strip()
            if key not in labels:
                raise ValueError(f"알 수 없는 horizon 입니다: {h}")
            idx = labels.index(key)
            return labels[idx], offsets[idx]

        h_int = int(h)
        if h_int in offsets:
            idx = offsets.index(h_int)
            return labels[idx], offsets[idx]
        if 0 <= h_int < len(HORIZONS):
            return labels[h_int], offsets[h_int]
        raise ValueError(
            f"h={h}는 지원되지 않습니다. horizon 라벨({labels}) 또는 offset({offsets})을 사용하세요."
        )

    def _build_pattern_mask_matrix(self, pattern_name: str, pattern_fn: Pattern) -> np.ndarray:
        if pattern_name in self._pattern_mask_cache:
            return self._pattern_mask_cache[pattern_name]

        self._prepare_market_sources(pattern_fn)
        num_dates, num_codes = self.prices.shape
        mask_matrix = np.zeros((num_dates, num_codes), dtype=np.bool_)
        for col_idx, code in enumerate(tqdm(self.codes, desc=f"{pattern_name} | mask")):
            values = self.prices[:, col_idx]
            mask = self._compute_mask(pattern_fn, values, code)
            if mask is None:
                continue
            mask_matrix[:, col_idx] = mask
        self._pattern_mask_cache[pattern_name] = mask_matrix
        return mask_matrix

    def _all_stock_geom_history(self, horizon_days: int, lookback_window: int) -> np.ndarray:
        cache_key = (int(horizon_days), int(lookback_window))
        if cache_key in self._all_stock_geom_cache:
            return self._all_stock_geom_cache[cache_key]

        prices = self.prices
        num_dates, _ = prices.shape
        counts = np.zeros(num_dates, dtype=np.float64)
        sum_log = np.zeros(num_dates, dtype=np.float64)
        invalid = np.zeros(num_dates, dtype=np.bool_)

        for i in range(0, max(0, num_dates - horizon_days)):
            base = prices[i]
            fwd = prices[i + horizon_days]
            valid = np.isfinite(base) & np.isfinite(fwd) & (base > 0.0) & (fwd > 0.0)
            if not np.any(valid):
                continue
            ret = fwd[valid] / base[valid] - 1.0
            cnt = ret.shape[0]
            if cnt <= 0:
                continue
            counts[i] = float(cnt)
            if np.any(ret <= -1.0):
                invalid[i] = True
            else:
                sum_log[i] = float(np.log1p(ret).sum())

        window = int(max(1, lookback_window))
        roll_counts = (
            pd.Series(counts).rolling(window=window, min_periods=1).sum().to_numpy(dtype=np.float64)
        )
        roll_sum_log = (
            pd.Series(sum_log).rolling(window=window, min_periods=1).sum().to_numpy(dtype=np.float64)
        )
        roll_invalid = (
            pd.Series(invalid.astype(np.float64))
            .rolling(window=window, min_periods=1)
            .sum()
            .to_numpy(dtype=np.float64)
            > 0.0
        )

        geom = np.full(num_dates, np.nan, dtype=np.float64)
        valid_geom = (roll_counts > 0.0) & (~roll_invalid)
        geom[valid_geom] = np.exp(roll_sum_log[valid_geom] / roll_counts[valid_geom]) - 1.0
        support = np.arange(num_dates) >= (window - 1)
        geom[~support] = np.nan

        self._all_stock_geom_cache[cache_key] = geom
        return geom

    def run(
        self,
        start=None,
        end=None,
        pattern: str | None = None,
        target_horizon: str | int = "1M",
        aggregate_lookback: int | str = 252,
        fallback_exposure: float = 0.5,
        max_weight_per_stock: float = 0.03,
    ) -> Simulator:
        if pattern is None:
            raise ValueError("run()에는 analyze()에서 계산한 pattern 이름(pattern)이 필요합니다.")
        if pattern not in self._analyzed_patterns or pattern not in self._analyzed_stats:
            available = sorted(self._analyzed_patterns.keys())
            raise ValueError(
                f"analyze() 결과에서 pattern '{pattern}'을 찾을 수 없습니다. "
                f"사용 가능: {available}"
            )

        horizon_label, horizon_days = self._resolve_horizon(target_horizon)
        lookback_window = _parse_lookback_window(aggregate_lookback)

        run_start = pd.Timestamp(self.start if start is None else start)
        run_end = pd.Timestamp(self.end if end is None else end)
        if run_end < run_start:
            raise ValueError("end는 start보다 빠를 수 없습니다.")

        start_idx = int(np.searchsorted(self.dates, run_start.to_datetime64(), side="left"))
        end_idx = int(np.searchsorted(self.dates, run_end.to_datetime64(), side="right"))
        end_idx = min(end_idx, len(self.dates))
        if end_idx - start_idx < 2:
            raise ValueError("run 구간에 최소 2개 이상의 거래일이 필요합니다.")

        pattern_fn = self._analyzed_patterns[pattern]
        pattern_stats = self._analyzed_stats[pattern]
        pattern_mask = self._build_pattern_mask_matrix(pattern, pattern_fn)

        pattern_hist = pattern_stats.to_frame_history(
            horizon=horizon_label,
            start=None,
            end=None,
            history_window=lookback_window,
            min_count=1,
            require_full_window=True,
        )
        pattern_geom_series = (
            pattern_hist["geom_mean"].reindex(pd.DatetimeIndex(self.dates)).to_numpy(dtype=np.float64)
        )
        all_stock_geom_series = self._all_stock_geom_history(horizon_days, lookback_window)
        simulator = Simulator(
            dates=self.dates,
            prices=self.prices,
        )
        return simulator.run(
            start_idx=start_idx,
            end_idx=end_idx,
            pattern=pattern,
            target_horizon=horizon_label,
            target_horizon_days=horizon_days,
            aggregate_lookback=aggregate_lookback,
            pattern_mask=pattern_mask,
            pattern_geom_series=pattern_geom_series,
            all_stock_geom_series=all_stock_geom_series,
            fallback_exposure=fallback_exposure,
            max_weight_per_stock=max_weight_per_stock,
        )

    def analyze(self, *patterns: Pattern, include_base: bool = True) -> StatsCollection:
        if not patterns and include_base and self.benchmark is not None:
            result = StatsCollection(
                dict(self._base_stats),
                benchmark_names=set(self._base_stats.keys()),
            )
            self._last_stats_collection = result
            return result

        stats_map: Dict[str, Stats] = {}
        benchmark_names: set[str] = set()
        if include_base:
            stats_map.update(self._base_stats)
            benchmark_names = set(self._base_stats.keys())

        for idx, pattern_fn in enumerate(patterns, start=len(stats_map) + 1):
            if not isinstance(pattern_fn, Pattern):
                raise TypeError("analyze()에 전달한 모든 패턴은 Pattern 객체여야 합니다.")
            base_name = _infer_pattern_label(pattern_fn, idx)
            trim_q, trim_method = _infer_pattern_trim_config(pattern_fn)
            stats = self._run_pattern(
                pattern_fn,
                trim_quantile=trim_q,
                trim_method=trim_method,
                progress_label=base_name,
            )
            name = base_name
            suffix = 2
            while name in stats_map:
                name = f"{base_name}_{suffix}"
                suffix += 1
            stats_map[name] = stats
            self._analyzed_patterns[name] = pattern_fn
            self._analyzed_stats[name] = stats
            self._pattern_mask_cache.pop(name, None)

        if not stats_map:
            raise ValueError("실행된 패턴이 없습니다.")
        result = StatsCollection(stats_map, benchmark_names=benchmark_names)
        self._last_stats_collection = result
        return result
