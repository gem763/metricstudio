"""Fully numpy + numba backtesting pipeline."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Callable, Dict, List, Tuple

import numpy as np
import pandas as pd
from numba import njit

from metricstudio._progress import progress as _progress
from metricstudio.dataload import get_default_data_loader
from metricstudio.filter import Filter as _Filter
from metricstudio.plot import plot_backtest
from metricstudio import util as u
from metricstudio.patterns import AllStockPattern, AmountSurge, BasePattern, Bollinger, High, MFI
from metricstudio.regime import Regime, build_market_cap_bucket_masks, build_regime_frame, regime_mask_from_frame
from metricstudio.simulate import Simulator
from metricstudio.stats import Stats, StatsCollection
from metricstudio.univ import Univ as _Univ

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
TRADING_DAYS_PER_YEAR = 240

TRIM_MODE_REMOVE = 0
TRIM_MODE_WINSORIZE = 1
AGG_MODE_EVENT = "event"
AGG_MODE_DAY = "day"

_REGIME_FRAME_TABLE_CACHE: Dict[
    tuple[
        int,
        tuple[str, ...] | None,
        bool | None,
        tuple[str, ...],
        str,
        int,
        str,
        str,
    ],
    pd.DataFrame,
] = {}
_BASE_STATS_CACHE: Dict[tuple[object, ...], Stats] = {}


@njit(cache=True)
def _numba_quantile_linear_sorted(sorted_vals, n, q):
    """
    정렬된 배열에서 선형보간 분위수를 계산한다.
    """

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
    exit_mask,
    use_exit_mask,
    dynamic_exit_index,
    dynamic_exit_offset,
    use_dynamic_exit,
    trim_q,
    trim_mode,
    counts,
    sum_ret,
    sum_log,
    pos_counts,
    geom_invalid,
    daily_arith,
    daily_rise,
):
    """
    단일 날짜 단면 수익률에 trim/winsorize를 적용해 통계를 누적한다.
    """

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

            target_idx = fwd_idx
            if use_dynamic_exit:
                exit_idx = dynamic_exit_index[date_idx - dynamic_exit_offset, code_idx]
                if exit_idx > date_idx and exit_idx <= fwd_idx:
                    target_idx = exit_idx
            if use_exit_mask:
                for k in range(date_idx + 1, target_idx + 1):
                    if exit_mask[k, code_idx]:
                        target_idx = k
                        break

            fwd = prices[target_idx, code_idx]
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

        if has_geom_invalid:
            geom_invalid[h_idx, date_idx] = True
        else:
            sum_log[h_idx, date_idx] = kept_sum_log

        daily_arith[h_idx, date_idx] = kept_sum_ret / kept_count
        daily_rise[h_idx, date_idx] = kept_pos / kept_count


@njit(cache=True)
def _numba_accumulate_for_date(
    prices,
    mask_row,
    date_idx,
    horizon_offsets,
    exit_mask,
    use_exit_mask,
    dynamic_exit_index,
    dynamic_exit_offset,
    use_dynamic_exit,
    counts,
    sum_ret,
    sum_log,
    pos_counts,
    geom_invalid,
    daily_arith,
    daily_rise,
    write_daily,
):
    """
    단일 날짜 단면 수익률을 trim 없이 누적한다.
    """

    num_dates = prices.shape[0]
    num_codes = prices.shape[1]
    num_h = len(horizon_offsets)

    for h_idx in range(num_h):
        step = int(horizon_offsets[h_idx])
        fwd_idx = date_idx + step
        if fwd_idx >= num_dates:
            continue

        kept_count = 0
        kept_pos = 0
        kept_sum_ret = 0.0
        kept_sum_log = 0.0
        has_geom_invalid = False

        for code_idx in range(num_codes):
            if not mask_row[code_idx]:
                continue

            base = prices[date_idx, code_idx]
            if not np.isfinite(base) or base <= 0.0:
                continue

            target_idx = fwd_idx
            if use_dynamic_exit:
                exit_idx = dynamic_exit_index[date_idx - dynamic_exit_offset, code_idx]
                if exit_idx > date_idx and exit_idx <= fwd_idx:
                    target_idx = exit_idx
            if use_exit_mask:
                for k in range(date_idx + 1, target_idx + 1):
                    if exit_mask[k, code_idx]:
                        target_idx = k
                        break

            fwd = prices[target_idx, code_idx]
            if not np.isfinite(fwd) or fwd <= 0.0:
                continue

            ret = fwd / base - 1.0
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
        if has_geom_invalid:
            geom_invalid[h_idx, date_idx] = True
        else:
            sum_log[h_idx, date_idx] = kept_sum_log

        if write_daily:
            daily_arith[h_idx, date_idx] = kept_sum_ret / kept_count
            daily_rise[h_idx, date_idx] = kept_pos / kept_count


@njit(cache=True)
def _numba_accumulate_all_stock_window(
    prices,
    start_idx,
    end_idx,
    horizon_offsets,
    counts,
    sum_ret,
    sum_log,
    pos_counts,
    geom_invalid,
    daily_arith,
    daily_rise,
    write_daily,
):
    """
    전체 종목 기본 패턴(no filter/no exit)의 날짜 구간 집계를 한 번에 누적한다.
    """

    num_dates = prices.shape[0]
    num_codes = prices.shape[1]
    num_h = len(horizon_offsets)

    for date_idx in range(start_idx, end_idx):
        for h_idx in range(num_h):
            step = int(horizon_offsets[h_idx])
            fwd_idx = date_idx + step
            if fwd_idx >= num_dates:
                continue

            kept_count = 0
            kept_pos = 0
            kept_sum_ret = 0.0
            kept_sum_log = 0.0
            has_geom_invalid = False

            for code_idx in range(num_codes):
                base = prices[date_idx, code_idx]
                if not np.isfinite(base) or base <= 0.0:
                    continue

                fwd = prices[fwd_idx, code_idx]
                if not np.isfinite(fwd) or fwd <= 0.0:
                    continue

                ret = fwd / base - 1.0
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
            if has_geom_invalid:
                geom_invalid[h_idx, date_idx] = True
            else:
                sum_log[h_idx, date_idx] = kept_sum_log

            if write_daily:
                daily_arith[h_idx, date_idx] = kept_sum_ret / kept_count
                daily_rise[h_idx, date_idx] = kept_pos / kept_count


def _infer_pattern_label(pattern_fn: BasePattern, idx: int) -> str:
    """
    패턴 표시 이름을 결정한다.
    """

    name = getattr(pattern_fn, "name", None)
    if isinstance(name, str) and name:
        return name
    return f"pattern_{idx}"


def _normalize_trim_quantile(trim: float | None) -> float | None:
    """
    trim quantile 입력을 정규화하고 유효범위를 검증한다.
    """

    if trim is None:
        return None
    value = float(trim)
    if not np.isfinite(value) or value < 0.0 or value >= 0.5:
        raise ValueError("trim 값은 [0.0, 0.5) 범위여야 합니다.")
    return value


def _normalize_trim_method(method: str | None) -> str:
    """
    trim 방법 문자열을 표준값으로 정규화한다.
    """

    method_text = str(method or "remove").lower()
    if method_text not in {"remove", "winsorize"}:
        raise ValueError("trim method는 'remove' 또는 'winsorize'여야 합니다.")
    return method_text


def _trim_mode_from_method(method: str) -> int:
    """
    trim 방법 문자열을 numba용 모드 상수로 변환한다.
    """

    if method == "remove":
        return TRIM_MODE_REMOVE
    if method == "winsorize":
        return TRIM_MODE_WINSORIZE
    raise ValueError("trim method는 'remove' 또는 'winsorize'여야 합니다.")


def _infer_pattern_trim_config(pattern_fn: BasePattern) -> tuple[float | None, str]:
    """
    패턴 객체에서 trim 설정을 추출해 정규화한다.
    """

    trim_q = _normalize_trim_quantile(getattr(pattern_fn, "trim_quantile", None))
    trim_method = _normalize_trim_method(getattr(pattern_fn, "trim_method", "remove"))
    return trim_q, trim_method


def _normalize_analyze_by(mode: str | None) -> str:
    """
    집계 모드 입력을 정규화한다.
    """

    key = str(mode or AGG_MODE_DAY).strip().lower().replace("-", "_")
    if key in {"event", "events", "event_mean"}:
        return AGG_MODE_EVENT
    if key in {"day", "daily", "day_mean", "daily_mean"}:
        return AGG_MODE_DAY
    raise ValueError("by는 'event' 또는 'day'여야 합니다.")


def _callable_cache_key(fn) -> tuple[object, ...] | None:
    if fn is None:
        return None

    defaults = getattr(fn, "__defaults__", None)
    kwdefaults = getattr(fn, "__kwdefaults__", None)
    closure = getattr(fn, "__closure__", None)
    code = getattr(fn, "__code__", None)
    closure_values = None
    if closure is not None:
        closure_values = tuple(_freeze_cache_value(cell.cell_contents) for cell in closure)
    return (
        "callable",
        getattr(fn, "__module__", type(fn).__module__),
        getattr(fn, "__qualname__", type(fn).__qualname__),
        None if code is None else (code.co_filename, code.co_firstlineno, code.co_name),
        _freeze_cache_value(defaults),
        _freeze_cache_value(kwdefaults),
        closure_values,
    )


def _freeze_cache_value(value):
    if value is None or isinstance(value, (str, int, bool)):
        return value
    if isinstance(value, float):
        if np.isnan(value):
            return ("float", "nan")
        return ("float", float(value))
    if isinstance(value, np.generic):
        return _freeze_cache_value(value.item())
    if isinstance(value, SimpleNamespace):
        return tuple((key, _freeze_cache_value(val)) for key, val in sorted(vars(value).items()))
    if isinstance(value, Regime):
        return ("regime", value.cache_key())
    if isinstance(value, BasePattern):
        return _pattern_cache_signature(value)
    if isinstance(value, dict):
        return tuple((str(key), _freeze_cache_value(val)) for key, val in sorted(value.items()))
    if isinstance(value, (list, tuple)):
        return tuple(_freeze_cache_value(item) for item in value)
    if callable(value):
        return _callable_cache_key(value)
    return (type(value).__qualname__, repr(value))


def _pattern_cache_signature(pattern_fn: BasePattern) -> tuple[object, ...]:
    left = getattr(pattern_fn, "left", None)
    right = getattr(pattern_fn, "right", None)
    child = getattr(pattern_fn, "pattern", None)
    regimes = tuple(
        regime.cache_key()
        for regime in getattr(pattern_fn, "_regimes", ())
        if isinstance(regime, Regime)
    )
    return (
        type(pattern_fn).__qualname__,
        ("trim_quantile", _freeze_cache_value(getattr(pattern_fn, "trim_quantile", None))),
        ("trim_method", _freeze_cache_value(getattr(pattern_fn, "trim_method", None))),
        ("market_name", _freeze_cache_value(getattr(pattern_fn, "market_name", None))),
        ("market_field", _freeze_cache_value(getattr(pattern_fn, "market_field", None))),
        ("params", _freeze_cache_value(getattr(pattern_fn, "params", None))),
        ("post_mask", _callable_cache_key(getattr(pattern_fn, "_post_mask_fn", None))),
        ("trade_profile", _freeze_cache_value(pattern_fn._trade_profile())),
        ("nmax_profile", _freeze_cache_value(pattern_fn._resolved_nmax_profile())),
        ("regimes", regimes),
        ("left", _pattern_cache_signature(left) if isinstance(left, BasePattern) else None),
        ("right", _pattern_cache_signature(right) if isinstance(right, BasePattern) else None),
        ("pattern", _pattern_cache_signature(child) if isinstance(child, BasePattern) else None),
    )


class Backtest:
    """
    패턴 분석, 스크리닝, 시뮬레이션 실행을 담당하는 메인 엔진.
    """

    def __init__(
        self,
        start,
        end,
        benchmark: BasePattern | None = None,
        regime: Regime | None = None,
        univ: _Univ | None = None,
        by: str = AGG_MODE_DAY,
    ):
        """
        백테스트 기간과 기준 패턴(옵션)을 초기화한다.
        """

        self.start = pd.Timestamp(start)
        self.end = pd.Timestamp(end)
        self.by = _normalize_analyze_by(by)
        self.data_loader = get_default_data_loader()
        if univ is not None and not isinstance(univ, _Univ):
            raise TypeError("univ는 Univ 객체여야 합니다.")
        self.univ = univ if isinstance(univ, _Univ) else _Univ()
        table = self.data_loader.load_stock_table(self.univ)
        self.dates = table.dates
        self.prices = table.prices
        self.codes = table.codes
        self.code_names = dict(table.code_names)
        self._market_values_cache: Dict[tuple[str, str], np.ndarray] = {}
        self.horizon_offsets = np.asarray([int(days) for _, days in HORIZONS], dtype=np.int64)
        self.start_idx = int(np.searchsorted(self.dates, self.start.to_datetime64(), side="left"))
        self.end_idx = int(np.searchsorted(self.dates, self.end.to_datetime64(), side="right"))
        self.end_idx = min(self.end_idx, len(self.dates))
        if benchmark is not None and not isinstance(benchmark, BasePattern):
            raise TypeError("benchmark는 BasePattern 객체여야 합니다.")
        if regime is not None and not isinstance(regime, Regime):
            raise TypeError("regime은 Regime 객체여야 합니다.")
        self.regime = regime
        self.benchmark = self._apply_default_regime(benchmark) if benchmark is not None else None
        self._base_stats = {}
        self._analyzed_patterns: Dict[str, BasePattern] = {}
        self._analyzed_stats: Dict[str, Stats] = {}
        self._analyzed_filters: Dict[str, _Filter | None] = {}
        self._last_stats_collection: StatsCollection | None = None
        self._last_simulator: Simulator | None = None
        self._pattern_mask_cache: Dict[tuple[str, bool], np.ndarray] = {}
        self._pattern_exit_mask_cache: Dict[str, np.ndarray] = {}
        self._pattern_exit_index_cache: Dict[tuple[str, int], np.ndarray] = {}
        self._pattern_policy_id_cache: Dict[tuple[str, bool], np.ndarray] = {}
        self._pattern_trade_profile_cache: Dict[
            tuple[str, bool], Dict[int, tuple[object | None, float | None, float | None, float | None]]
        ] = {}
        self._all_opportunity_count_cache: np.ndarray | None = None
        self._stock_field_matrix_cache: Dict[str, np.ndarray] = {}
        self._pattern_nmax_node_cache: Dict[
            int,
            tuple[Bollinger | None, AmountSurge | None, High | None, MFI | None],
        ] = {}
        self._pattern_nmax_series_cache: Dict[tuple[int, str, int], np.ndarray] = {}
        self._regime_frame_cache: Dict[str, pd.DataFrame] = {}
        self._vwap_matrix: np.ndarray | None = None
        if self.benchmark is not None:
            base_name = _infer_pattern_label(self.benchmark, 0)
            base_trim_q, base_trim_method = _infer_pattern_trim_config(self.benchmark)
            base_cache_key = self._base_stats_cache_key(self.benchmark, self.by)
            if base_cache_key in _BASE_STATS_CACHE:
                self._base_stats[base_name] = _BASE_STATS_CACHE[base_cache_key]
            else:
                self._invalidate_runtime_cache(base_name)
                self._base_stats[base_name] = self._run_pattern(
                    self.benchmark,
                    trim_quantile=base_trim_q,
                    trim_method=base_trim_method,
                    progress_label=base_name,
                    aggregation_mode=self.by,
                    filter_obj=None,
                    cache_name=base_name,
                )
                _BASE_STATS_CACHE[base_cache_key] = self._base_stats[base_name]
            self._analyzed_patterns[base_name] = self.benchmark
            self._analyzed_stats[base_name] = self._base_stats[base_name]
            self._analyzed_filters[base_name] = None

    @staticmethod
    def _compute_mask(pattern_fn: BasePattern, values: np.ndarray, code: str) -> np.ndarray | None:
        """
        패턴 함수 실행 결과를 bool 마스크로 정규화한다.
        """

        mask = pattern_fn(values)
        if mask is None:
            return None
        mask_arr = np.asarray(mask, dtype=np.bool_)
        if mask_arr.shape != values.shape:
            raise ValueError(f"패턴 mask shape이 종목 코드 {code}의 가격 배열 shape과 일치하지 않습니다.")
        return mask_arr

    @staticmethod
    def _is_default_price_pattern(pattern_fn: BasePattern) -> bool:
        """
        종가가 유효한 모든 구간을 그대로 선택하는 기본 패턴인지 판별한다.
        """

        return (
            type(pattern_fn) is AllStockPattern
            and pattern_fn.market_name is None
            and pattern_fn._post_mask_fn is AllStockPattern._post_mask_base
        )

    def _base_stats_cache_key(
        self,
        pattern_fn: BasePattern,
        aggregation_mode: str,
    ) -> tuple[object, ...]:
        dates = self.dates
        return (
            self.univ.cache_key(),
            int(len(dates)),
            str(pd.Timestamp(dates[0]).date()) if len(dates) > 0 else "empty",
            str(pd.Timestamp(dates[-1]).date()) if len(dates) > 0 else "empty",
            int(self.start_idx),
            int(self.end_idx),
            str(_normalize_analyze_by(aggregation_mode)),
            _pattern_cache_signature(pattern_fn),
        )

    def _get_market_values(self, market: str, field: str) -> np.ndarray:
        """
        시장 데이터 컬럼을 날짜축에 맞춰 정렬한 뒤 캐시 반환한다.
        """

        key = (str(market).strip().lower(), str(field).strip().lower())
        if not key[0]:
            raise ValueError("market은 비어 있을 수 없습니다.")
        if not key[1]:
            raise ValueError("field는 비어 있을 수 없습니다.")

        if key not in self._market_values_cache:
            df = self.data_loader.load_market_table(key[0])
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

    def _normalized_kospi_reference_curve(self, index: pd.Index | np.ndarray) -> np.ndarray | None:
        """
        주어진 날짜 구간에 맞춰 KOSPI 종가를 시작값=1.0으로 정규화해 반환한다.
        """

        ref_index = pd.DatetimeIndex(index)
        date_index = pd.DatetimeIndex(self.dates)
        close = pd.Series(
            self._get_market_values("kospi", "close"),
            index=date_index,
            dtype="float64",
        )
        aligned = close.reindex(ref_index).to_numpy(dtype=np.float64, copy=True)
        valid = np.isfinite(aligned)
        if not valid.any():
            return None
        first_valid_idx = int(np.flatnonzero(valid)[0])
        start_value = float(aligned[first_valid_idx])
        if not np.isfinite(start_value) or start_value == 0.0:
            return None
        normalized = np.full(aligned.shape, np.nan, dtype=np.float64)
        normalized[valid] = aligned[valid] / start_value
        return normalized

    def _iter_pattern_nodes(self, pattern_fn: BasePattern):
        """
        결합 패턴 트리를 순회하며 하위 BasePattern 노드를 반환한다.
        """

        seen: set[int] = set()
        stack: list[BasePattern] = [pattern_fn]
        while stack:
            node = stack.pop()
            node_id = id(node)
            if node_id in seen:
                continue
            seen.add(node_id)
            yield node

            left = getattr(node, "left", None)
            right = getattr(node, "right", None)
            pattern = getattr(node, "pattern", None)
            if isinstance(left, BasePattern):
                stack.append(left)
            if isinstance(right, BasePattern):
                stack.append(right)
            if isinstance(pattern, BasePattern):
                stack.append(pattern)

    def _iter_attached_regimes(self, pattern_fn: BasePattern):
        """
        패턴 트리에 연결된 Regime 객체를 중복 없이 순회한다.
        """

        seen: set[int] = set()
        for node in self._iter_pattern_nodes(pattern_fn):
            for regime in getattr(node, "_regimes", ()):
                if not isinstance(regime, Regime):
                    continue
                regime_id = id(regime)
                if regime_id in seen:
                    continue
                seen.add(regime_id)
                yield regime

    def _apply_default_regime(self, pattern_fn: BasePattern) -> BasePattern:
        """
        Backtest 기본 레짐이 있고 패턴에 별도 레짐이 없으면 자동으로 감싼다.
        """

        if self.regime is None:
            return pattern_fn
        if any(True for _ in self._iter_attached_regimes(pattern_fn)):
            return pattern_fn
        return pattern_fn.when(self.regime)

    def _resolve_regime_breadth_univ(self) -> _Univ:
        """
        레짐 breadth 계산에 쓸 유니버스를 정한다.
        """
        return self.univ

    def _get_regime_frame(self, regime: Regime) -> pd.DataFrame:
        """
        Regime 계산에 필요한 일별 메트릭/라벨 테이블을 생성/캐시한다.
        """

        market_key = regime.market
        if market_key in self._regime_frame_cache:
            return self._regime_frame_cache[market_key]

        breadth_univ = self._resolve_regime_breadth_univ()
        idx = pd.DatetimeIndex(self.dates)
        global_key = (
            breadth_univ.market,
            breadth_univ.is_tradable,
            breadth_univ.dept_excludes,
            str(market_key),
            len(self.dates),
            str(idx[0].date()),
            str(idx[-1].date()),
        )
        if global_key in _REGIME_FRAME_TABLE_CACHE:
            frame = _REGIME_FRAME_TABLE_CACHE[global_key]
            self._regime_frame_cache[market_key] = frame
            return frame

        stock_tables = self.data_loader.load_stock_field_tables(["close", "amount", "marketcap"], breadth_univ)
        close_df = stock_tables["close"].reindex(index=idx)
        amount_df = stock_tables["amount"].reindex(index=idx, columns=close_df.columns)
        marketcap_df = stock_tables["marketcap"].reindex(index=idx, columns=close_df.columns)
        market_df = self.data_loader.load_market_table(market_key)
        if "close" not in market_df.columns:
            raise ValueError(f"market='{market_key}' 데이터에 'close' 컬럼이 없습니다.")
        market_close = pd.to_numeric(market_df["close"], errors="coerce").reindex(idx)

        frame = build_regime_frame(
            market_close=market_close,
            close_df=close_df,
            amount_df=amount_df,
            market_cap_df=marketcap_df,
            percentile_window=TRADING_DAYS_PER_YEAR,
        )
        _REGIME_FRAME_TABLE_CACHE[global_key] = frame
        self._regime_frame_cache[market_key] = frame
        return frame

    def _prepare_regime_sources(self, pattern_fn: BasePattern) -> None:
        """
        패턴에 연결된 Regime 객체에 날짜별 허용 마스크를 주입한다.
        """

        prepared: set[int] = set()
        for regime in self._iter_attached_regimes(pattern_fn):
            for leaf in regime._iter_leaf_regimes():
                leaf_id = id(leaf)
                if leaf_id in prepared:
                    continue
                frame = self._get_regime_frame(leaf)
                mask_values = regime_mask_from_frame(frame, leaf.kind)
                leaf._bind(self.dates, mask_values, frame)
                prepared.add(leaf_id)

    def _combined_regime_mask(self, pattern_fn: BasePattern) -> np.ndarray | None:
        """
        패턴에 연결된 모든 regime의 공통 활성 구간 마스크를 반환한다.
        """

        regimes = list(self._iter_attached_regimes(pattern_fn))
        if not regimes:
            return None

        self._prepare_regime_sources(pattern_fn)
        combined = np.ones(len(self.dates), dtype=np.bool_)
        for regime in regimes:
            combined &= np.asarray(regime.mask(len(self.dates)), dtype=np.bool_)
        return combined

    def _prepare_market_sources(self, pattern_fn: BasePattern) -> None:
        """
        market 기반 패턴 노드에 참조 시장 시계열을 주입한다.
        """

        for node in self._iter_pattern_nodes(pattern_fn):
            market_name = getattr(node, "market_name", None)
            if market_name is None:
                node._set_market_values(None)
                continue
            market_field = getattr(node, "market_field", "close")
            market_values = self._get_market_values(market_name, market_field)
            node._set_market_values(market_values)

    def _get_stock_field_matrix(self, field: str) -> np.ndarray:
        """
        종목 필드 wide 테이블을 백테스트 날짜/종목 축에 맞춰 정렬해 반환한다.
        """

        key = str(field).strip().lower()
        if not key:
            raise ValueError("field는 비어 있을 수 없습니다.")
        if key in {"size_bucket_large", "size_bucket_mid", "size_bucket_small"}:
            self._ensure_size_bucket_matrices()
        if key not in self._stock_field_matrix_cache:
            df = self.data_loader.load_stock_field_table(key, self.univ)
            aligned = df.reindex(
                index=pd.DatetimeIndex(self.dates),
                columns=pd.Index(self.codes, dtype="object"),
            )
            self._stock_field_matrix_cache[key] = aligned.to_numpy(dtype=np.float64, copy=True)
        return self._stock_field_matrix_cache[key]

    def _ensure_size_bucket_matrices(self) -> None:
        cache = self._stock_field_matrix_cache
        needed = ("size_bucket_large", "size_bucket_mid", "size_bucket_small")
        if all(key in cache for key in needed):
            return

        idx = pd.DatetimeIndex(self.dates)
        cols = pd.Index(self.codes, dtype="object")
        marketcap_df = self.data_loader.load_stock_field_table("marketcap", self.univ).reindex(index=idx, columns=cols)
        size_masks = build_market_cap_bucket_masks(marketcap_df)
        cache["size_bucket_large"] = size_masks["large"].to_numpy(dtype=np.bool_, copy=True)
        cache["size_bucket_mid"] = size_masks["mid"].to_numpy(dtype=np.bool_, copy=True)
        cache["size_bucket_small"] = size_masks["small"].to_numpy(dtype=np.bool_, copy=True)

    def _prepare_stock_sources(self, pattern_fn: BasePattern, col_idx: int) -> None:
        """
        종목별 보조 필드(high/low/volume 등)가 필요한 패턴 노드에 시계열을 주입한다.
        """

        for node in self._iter_pattern_nodes(pattern_fn):
            fields = tuple(node._required_stock_fields())
            for field in fields:
                matrix = self._get_stock_field_matrix(field)
                node._set_stock_values(field, matrix[:, col_idx])

    def _find_first_pattern_node(
        self,
        pattern_fn: BasePattern,
        pattern_type: type[BasePattern],
    ) -> BasePattern | None:
        seen: set[int] = set()

        def _walk(node: BasePattern | None) -> BasePattern | None:
            if not isinstance(node, BasePattern):
                return None
            node_id = id(node)
            if node_id in seen:
                return None
            seen.add(node_id)
            if isinstance(node, pattern_type):
                return node
            child = getattr(node, "pattern", None)
            found = _walk(child)
            if found is not None:
                return found
            left = getattr(node, "left", None)
            found = _walk(left)
            if found is not None:
                return found
            right = getattr(node, "right", None)
            return _walk(right)

        return _walk(pattern_fn)

    def _resolve_pattern_nmax_nodes(
        self,
        pattern_fn: BasePattern,
    ) -> tuple[Bollinger | None, AmountSurge | None, High | None, MFI | None]:
        if not hasattr(self, "_pattern_nmax_node_cache"):
            self._pattern_nmax_node_cache = {}
        cache_key = int(id(pattern_fn))
        if cache_key not in self._pattern_nmax_node_cache:
            self._pattern_nmax_node_cache[cache_key] = (
                self._find_first_pattern_node(pattern_fn, Bollinger),
                self._find_first_pattern_node(pattern_fn, AmountSurge),
                self._find_first_pattern_node(pattern_fn, High),
                self._find_first_pattern_node(pattern_fn, MFI),
            )
        return self._pattern_nmax_node_cache[cache_key]

    def _get_nmax_metric_series(
        self,
        node: BasePattern | None,
        metric_name: str,
        col_idx: int,
    ) -> np.ndarray | None:
        if node is None:
            return None
        if not hasattr(self, "_pattern_nmax_series_cache"):
            self._pattern_nmax_series_cache = {}

        cache_key = (int(id(node)), str(metric_name), int(col_idx))
        if cache_key in self._pattern_nmax_series_cache:
            return self._pattern_nmax_series_cache[cache_key]

        prices = np.asarray(self.prices[:, col_idx], dtype=np.float64)
        series = np.full(prices.shape[0], np.nan, dtype=np.float64)

        if isinstance(node, Bollinger) and metric_name == "bandwidth":
            mean, std, valid_end = u.rolling_mean_std(prices, int(node.window))
            valid = valid_end & np.isfinite(mean) & (mean > 0.0) & np.isfinite(std)
            series[valid] = (float(node.sigma) * std[valid]) / mean[valid]
        elif isinstance(node, AmountSurge) and metric_name == "amount_ratio":
            amount = np.asarray(self._get_stock_field_matrix("amount")[:, col_idx], dtype=np.float64)
            mean_amount, valid_end = u.rolling_mean(amount, int(node.params.window))
            valid = (
                valid_end
                & np.isfinite(amount)
                & (amount > 0.0)
                & np.isfinite(mean_amount)
                & (mean_amount > 0.0)
            )
            series[valid] = amount[valid] / mean_amount[valid]
        elif isinstance(node, High) and metric_name == "high_proximity":
            high_series = u.rolling_high(prices, int(node.params.window))
            valid = (
                np.isfinite(prices)
                & (prices > 0.0)
                & np.isfinite(high_series)
                & (high_series > 0.0)
            )
            series[valid] = prices[valid] / high_series[valid]
        elif isinstance(node, MFI) and metric_name == "mfi":
            high = np.asarray(self._get_stock_field_matrix("high")[:, col_idx], dtype=np.float64)
            low = np.asarray(self._get_stock_field_matrix("low")[:, col_idx], dtype=np.float64)
            volume = np.asarray(self._get_stock_field_matrix("volume")[:, col_idx], dtype=np.float64)
            mfi, valid_end = u.money_flow_index(high, low, prices, volume, int(node.window))
            valid = valid_end & np.isfinite(mfi)
            series[valid] = mfi[valid]

        self._pattern_nmax_series_cache[cache_key] = series
        return series

    def _get_nmax_rank_key(
        self,
        pattern_fn: BasePattern,
        date_idx: int,
        col_idx: int,
    ) -> tuple[float, float, float, float, float, int]:
        bollinger_node, amount_node, high_node, mfi_node = self._resolve_pattern_nmax_nodes(pattern_fn)
        use_market_cap = bool(pattern_fn._resolved_nmax_market_cap())

        bandwidth_series = self._get_nmax_metric_series(bollinger_node, "bandwidth", col_idx)
        amount_series = self._get_nmax_metric_series(amount_node, "amount_ratio", col_idx)
        high_series = self._get_nmax_metric_series(high_node, "high_proximity", col_idx)
        mfi_series = self._get_nmax_metric_series(mfi_node, "mfi", col_idx)

        bandwidth_value = (
            float(bandwidth_series[date_idx])
            if bandwidth_series is not None and np.isfinite(bandwidth_series[date_idx])
            else float("inf")
        )
        amount_value = (
            -float(amount_series[date_idx])
            if amount_series is not None and np.isfinite(amount_series[date_idx])
            else float("inf")
        )
        high_value = (
            -float(high_series[date_idx])
            if high_series is not None and np.isfinite(high_series[date_idx])
            else float("inf")
        )
        mfi_value = (
            -float(mfi_series[date_idx])
            if mfi_series is not None and np.isfinite(mfi_series[date_idx])
            else float("inf")
        )
        market_cap_value = float("inf")
        if use_market_cap:
            market_cap = float(self._get_stock_field_matrix("marketcap")[date_idx, col_idx])
            if np.isfinite(market_cap) and market_cap > 0.0:
                market_cap_value = -market_cap
            return (
                market_cap_value,
                bandwidth_value,
                amount_value,
                high_value,
                mfi_value,
                int(col_idx),
            )
        return bandwidth_value, amount_value, high_value, mfi_value, market_cap_value, int(col_idx)

    def _apply_pattern_nmax(
        self,
        pattern_fn: BasePattern,
        mask_matrix: np.ndarray,
        slice_start: int,
    ) -> np.ndarray:
        max_cohort_size = pattern_fn._resolved_max_cohort_size()
        if max_cohort_size is None:
            return mask_matrix

        limit = int(max_cohort_size)
        if limit <= 0 or mask_matrix.size == 0:
            return mask_matrix

        counts = np.count_nonzero(mask_matrix, axis=1)
        overflow_rows = np.flatnonzero(counts > limit)
        if overflow_rows.size == 0:
            return mask_matrix

        capped = np.asarray(mask_matrix, dtype=np.bool_).copy()
        for row_idx in overflow_rows:
            selected = np.flatnonzero(capped[row_idx])
            if selected.size <= limit:
                continue
            date_idx = int(slice_start + row_idx)
            ranked = sorted(
                selected.tolist(),
                key=lambda col_idx: self._get_nmax_rank_key(pattern_fn, date_idx, int(col_idx)),
            )
            keep = np.asarray(ranked[:limit], dtype=np.int64)
            capped[row_idx] = False
            capped[row_idx, keep] = True
        return capped

    def _run_pattern_normal(
        self,
        pattern_fn: BasePattern,
        progress_label: str,
        cache_name: str | None = None,
    ) -> Stats:
        """
        trim 없이 이벤트 기반 통계를 계산한다.
        """

        stats = Stats.create(self.dates, HORIZONS)
        stats.eval_start_idx = self.start_idx
        stats.eval_end_idx = self.end_idx
        eval_len = max(0, self.end_idx - self.start_idx)
        mask_matrix = self._build_mask_matrix(pattern_fn, eval_len)
        exit_mask_matrix = self._build_exit_mask_matrix(pattern_fn, len(self.dates))
        self._store_runtime_cache(
            cache_name,
            mask_matrix=mask_matrix,
            exit_mask_matrix=exit_mask_matrix,
        )
        self._accumulate_dates(
            mask_matrix,
            exit_mask_matrix,
            bool(pattern_fn.has_exit_rule()),
            None,
            stats,
            progress_label,
            write_daily=False,
        )
        return stats

    def _run_pattern_default_price_fast(
        self,
        pattern_fn: BasePattern,
        progress_label: str,
        aggregation_mode: str,
        cache_name: str | None = None,
    ) -> Stats:
        """
        전체 종목 기본 패턴(no trim/no filter/no exit)을 빠르게 집계한다.
        """

        agg_mode = _normalize_analyze_by(aggregation_mode)
        write_daily = agg_mode == AGG_MODE_DAY
        stats = Stats.create_daily(self.dates, HORIZONS) if write_daily else Stats.create(self.dates, HORIZONS)
        stats.eval_start_idx = self.start_idx
        stats.eval_end_idx = self.end_idx
        eval_len = max(0, self.end_idx - self.start_idx)
        if cache_name is not None:
            mask_matrix = self._build_mask_matrix(pattern_fn, eval_len)
            self._store_runtime_cache(cache_name, mask_matrix=mask_matrix)
        if eval_len <= 0:
            return stats

        if write_daily:
            daily_arith = stats.daily_arith
            daily_rise = stats.daily_rise
            if daily_arith is None or daily_rise is None:
                raise ValueError("daily 집계에는 daily 통계 버퍼가 필요합니다.")
            progress_desc = f"{progress_label} | day"
        else:
            daily_arith = np.full((1, 1), np.nan, dtype=np.float64)
            daily_rise = np.full((1, 1), np.nan, dtype=np.float64)
            progress_desc = f"{progress_label} | dates"

        chunk_size = 256
        progress_bar = _progress(total=eval_len, desc=progress_desc)
        try:
            for chunk_start in range(self.start_idx, self.end_idx, chunk_size):
                chunk_end = min(self.end_idx, chunk_start + chunk_size)
                _numba_accumulate_all_stock_window(
                    self.prices,
                    chunk_start,
                    chunk_end,
                    self.horizon_offsets,
                    stats.counts,
                    stats.sum_ret,
                    stats.sum_log,
                    stats.pos_counts,
                    stats.geom_invalid,
                    daily_arith,
                    daily_rise,
                    write_daily,
                )
                progress_bar.update(chunk_end - chunk_start)
        finally:
            progress_bar.close()
        return stats

    def _build_mask_matrix(
        self,
        pattern_fn: BasePattern,
        eval_len: int,
        allowed_cols: np.ndarray | None = None,
        prefilter_mask: np.ndarray | None = None,
    ) -> np.ndarray:
        """
        분석 구간의 [일자 x 종목] 패턴 마스크 행렬을 생성한다.
        """

        num_codes = len(self.codes)
        mask_matrix = np.zeros((eval_len, num_codes), dtype=np.bool_)
        if eval_len == 0:
            return mask_matrix

        slice_start = self.start_idx
        if eval_len == len(self.dates):
            slice_start = 0
        slice_end = min(len(self.dates), slice_start + eval_len)

        if self._is_default_price_pattern(pattern_fn):
            window = self.prices[slice_start:slice_end]
            mask_matrix = np.isfinite(window) & (window > 0.0)
            if allowed_cols is not None:
                mask_matrix[:, ~allowed_cols] = False
            if prefilter_mask is not None:
                mask_matrix &= prefilter_mask
            return self._apply_pattern_nmax(pattern_fn, mask_matrix, slice_start)

        column_indices = range(num_codes)
        if allowed_cols is not None:
            column_indices = np.flatnonzero(allowed_cols)

        for col_idx in column_indices:
            code = self.codes[col_idx]
            values = self.prices[:, col_idx]
            self._prepare_stock_sources(pattern_fn, col_idx)
            mask = self._compute_mask(pattern_fn, values, code)
            if mask is None:
                continue
            eval_mask = mask[slice_start:slice_end]
            if prefilter_mask is not None:
                eval_mask = eval_mask & prefilter_mask[:, col_idx]
            mask_matrix[:, col_idx] = eval_mask
        return self._apply_pattern_nmax(pattern_fn, mask_matrix, slice_start)

    def _build_exit_mask_matrix(
        self,
        pattern_fn: BasePattern,
        eval_len: int,
    ) -> np.ndarray:
        """
        [일자 x 종목] 청산 마스크 행렬을 생성한다.
        """

        num_codes = len(self.codes)
        exit_mask_matrix = np.zeros((eval_len, num_codes), dtype=np.bool_)
        if eval_len == 0 or not pattern_fn.has_exit_rule():
            return exit_mask_matrix

        slice_start = self.start_idx
        if eval_len == len(self.dates):
            slice_start = 0
        slice_end = min(len(self.dates), slice_start + eval_len)

        for col_idx in range(num_codes):
            values = self.prices[:, col_idx]
            self._prepare_stock_sources(pattern_fn, col_idx)
            exit_mask = pattern_fn.exit_mask(values)
            if exit_mask is None:
                continue
            exit_mask_matrix[:, col_idx] = np.asarray(
                exit_mask[slice_start:slice_end],
                dtype=np.bool_,
            )
        return exit_mask_matrix

    def _build_pattern_exit_mask_matrix(
        self,
        pattern_name: str,
        pattern_fn: BasePattern,
    ) -> np.ndarray:
        """
        전체 기간 청산 마스크를 생성/캐시한다.
        """

        if pattern_name in self._pattern_exit_mask_cache:
            return self._pattern_exit_mask_cache[pattern_name]
        exit_mask_matrix = self._build_exit_mask_matrix(pattern_fn, len(self.dates))
        self._pattern_exit_mask_cache[pattern_name] = exit_mask_matrix
        return exit_mask_matrix

    def _invalidate_runtime_cache(self, pattern_name: str) -> None:
        """
        패턴명에 연결된 run용 캐시(mask/exit/exit_index)를 비운다.
        """

        for cache_key in list(self._pattern_mask_cache):
            if cache_key[0] == pattern_name:
                self._pattern_mask_cache.pop(cache_key, None)
        for cache_key in list(self._pattern_policy_id_cache):
            if cache_key[0] == pattern_name:
                self._pattern_policy_id_cache.pop(cache_key, None)
                self._pattern_trade_profile_cache.pop(cache_key, None)
        self._pattern_exit_mask_cache.pop(pattern_name, None)
        for cache_key in list(self._pattern_exit_index_cache):
            if cache_key[0] == pattern_name:
                self._pattern_exit_index_cache.pop(cache_key, None)

    def _store_runtime_cache(
        self,
        pattern_name: str | None,
        *,
        filter_obj: _Filter | None = None,
        mask_matrix: np.ndarray | None = None,
        exit_mask_matrix: np.ndarray | None = None,
        dynamic_exit_index: np.ndarray | None = None,
    ) -> None:
        """
        analyze 중 계산한 중간 결과를 run 캐시에 저장한다.
        """

        if not pattern_name:
            return

        active_filter = bool(filter_obj is not None and filter_obj.is_active)

        if mask_matrix is not None:
            mask_arr = np.asarray(mask_matrix, dtype=np.bool_)
            if mask_arr.shape[0] == len(self.dates):
                full_mask = mask_arr
            else:
                full_mask = np.zeros((len(self.dates), mask_arr.shape[1]), dtype=np.bool_)
                full_mask[self.start_idx:self.end_idx] = mask_arr
            self._pattern_mask_cache[(pattern_name, active_filter)] = full_mask

        if exit_mask_matrix is not None:
            exit_arr = np.asarray(exit_mask_matrix, dtype=np.bool_)
            if exit_arr.shape[0] == len(self.dates):
                self._pattern_exit_mask_cache[pattern_name] = exit_arr

        if dynamic_exit_index is not None:
            exit_idx_arr = np.asarray(dynamic_exit_index, dtype=np.int32)
            if exit_idx_arr.shape[0] == len(self.dates):
                max_horizon = int(np.max(self.horizon_offsets))
                self._pattern_exit_index_cache[(pattern_name, max_horizon)] = exit_idx_arr

    def _build_dynamic_exit_index_matrix(
        self,
        pattern_fn: BasePattern,
        mask_matrix: np.ndarray,
        slice_start: int,
        max_horizon: int,
    ) -> np.ndarray:
        """
        진입일별 첫 청산일 인덱스를 [entry_date x code] 형태로 계산한다.
        """

        exit_index = np.full(mask_matrix.shape, -1, dtype=np.int32)
        if mask_matrix.size == 0 or max_horizon <= 0:
            return exit_index

        active_cols = np.flatnonzero(np.any(mask_matrix, axis=0))
        for col_idx in active_cols:
            entry_rows = np.flatnonzero(mask_matrix[:, col_idx])
            if entry_rows.size == 0:
                continue
            values = self.prices[:, col_idx]
            self._prepare_stock_sources(pattern_fn, col_idx)
            for row in entry_rows:
                entry_idx = slice_start + int(row)
                last_idx = min(len(self.dates) - 1, entry_idx + int(max_horizon))
                exit_idx = pattern_fn.first_exit_index(values, entry_idx, last_idx)
                if exit_idx > entry_idx:
                    exit_index[row, col_idx] = int(exit_idx)
        return exit_index

    def _build_pattern_dynamic_exit_index_matrix(
        self,
        pattern_name: str,
        pattern_fn: BasePattern,
        pattern_mask: np.ndarray,
        max_horizon: int,
    ) -> np.ndarray:
        """
        run()용 전체 기간 진입별 청산일 인덱스를 생성/캐시한다.
        """

        cache_key = (pattern_name, int(max_horizon))
        if cache_key in self._pattern_exit_index_cache:
            return self._pattern_exit_index_cache[cache_key]
        exit_index = self._build_dynamic_exit_index_matrix(
            pattern_fn,
            pattern_mask,
            0,
            max_horizon,
        )
        self._pattern_exit_index_cache[cache_key] = exit_index
        return exit_index

    def _accumulate_trim_dates(
        self,
        mask_matrix: np.ndarray,
        exit_mask: np.ndarray,
        use_exit_mask: bool,
        dynamic_exit_index: np.ndarray | None,
        trim_q: float,
        trim_mode: int,
        stats: Stats,
        progress_label: str,
        progress_bar=None,
    ) -> None:
        """
        날짜별 단면 데이터에 trim 집계를 누적한다.
        """

        daily_arith = stats.daily_arith
        daily_rise = stats.daily_rise
        if daily_arith is None or daily_rise is None:
            raise ValueError("trim 모드에서는 daily 통계 버퍼가 필요합니다.")

        iterator = range(mask_matrix.shape[0])
        if progress_bar is None:
            progress_mode = "trim" if trim_q > 0.0 else "day"
            iterator = _progress(iterator, desc=f"{progress_label} | {progress_mode}")

        if dynamic_exit_index is None:
            dynamic_exit_index = np.full((1, 1), -1, dtype=np.int32)
        use_dynamic_exit = dynamic_exit_index.shape == mask_matrix.shape

        for i_local in iterator:
            i = self.start_idx + i_local
            _numba_accumulate_trim_for_date(
                self.prices,
                mask_matrix[i_local],
                i,
                self.horizon_offsets,
                exit_mask,
                use_exit_mask,
                dynamic_exit_index,
                self.start_idx,
                use_dynamic_exit,
                trim_q,
                trim_mode,
                stats.counts,
                stats.sum_ret,
                stats.sum_log,
                stats.pos_counts,
                stats.geom_invalid,
                daily_arith,
                daily_rise,
            )
            if progress_bar is not None:
                progress_bar.update(1)

    def _accumulate_trim_dates_with_buffers(
        self,
        mask_matrix: np.ndarray,
        exit_mask: np.ndarray,
        use_exit_mask: bool,
        dynamic_exit_index: np.ndarray | None,
        trim_q: float,
        trim_mode: int,
        stats: Stats,
        progress_label: str,
        daily_arith: np.ndarray,
        daily_rise: np.ndarray,
        progress_bar=None,
    ) -> None:
        """
        외부 daily 버퍼를 사용해 날짜별 trim 집계를 누적한다.
        """

        iterator = range(mask_matrix.shape[0])
        if progress_bar is None:
            progress_mode = "trim" if trim_q > 0.0 else "day"
            iterator = _progress(iterator, desc=f"{progress_label} | {progress_mode}")

        if dynamic_exit_index is None:
            dynamic_exit_index = np.full((1, 1), -1, dtype=np.int32)
        use_dynamic_exit = dynamic_exit_index.shape == mask_matrix.shape

        for i_local in iterator:
            i = self.start_idx + i_local
            _numba_accumulate_trim_for_date(
                self.prices,
                mask_matrix[i_local],
                i,
                self.horizon_offsets,
                exit_mask,
                use_exit_mask,
                dynamic_exit_index,
                self.start_idx,
                use_dynamic_exit,
                trim_q,
                trim_mode,
                stats.counts,
                stats.sum_ret,
                stats.sum_log,
                stats.pos_counts,
                stats.geom_invalid,
                daily_arith,
                daily_rise,
            )
            if progress_bar is not None:
                progress_bar.update(1)

    def _accumulate_dates(
        self,
        mask_matrix: np.ndarray,
        exit_mask: np.ndarray,
        use_exit_mask: bool,
        dynamic_exit_index: np.ndarray | None,
        stats: Stats,
        progress_label: str,
        write_daily: bool,
        progress_bar=None,
    ) -> None:
        """
        날짜별 단면 수익률을 trim 없이 누적한다.
        """

        if write_daily:
            daily_arith = stats.daily_arith
            daily_rise = stats.daily_rise
            if daily_arith is None or daily_rise is None:
                raise ValueError("daily 집계에는 daily 통계 버퍼가 필요합니다.")
        else:
            daily_arith = np.full((1, 1), np.nan, dtype=np.float64)
            daily_rise = np.full((1, 1), np.nan, dtype=np.float64)

        iterator = range(mask_matrix.shape[0])
        if progress_bar is None:
            iterator = _progress(iterator, desc=f"{progress_label} | dates")

        if dynamic_exit_index is None:
            dynamic_exit_index = np.full((1, 1), -1, dtype=np.int32)
        use_dynamic_exit = dynamic_exit_index.shape == mask_matrix.shape

        for i_local in iterator:
            i = self.start_idx + i_local
            _numba_accumulate_for_date(
                self.prices,
                mask_matrix[i_local],
                i,
                self.horizon_offsets,
                exit_mask,
                use_exit_mask,
                dynamic_exit_index,
                self.start_idx,
                use_dynamic_exit,
                stats.counts,
                stats.sum_ret,
                stats.sum_log,
                stats.pos_counts,
                stats.geom_invalid,
                daily_arith,
                daily_rise,
                write_daily,
            )
            if progress_bar is not None:
                progress_bar.update(1)

    def _run_pattern_trim(
        self,
        pattern_fn: BasePattern,
        trim_q: float,
        trim_method: str,
        progress_label: str,
        cache_name: str | None = None,
    ) -> Stats:
        """
        trim/winsorize를 적용한 daily-mean 통계를 계산한다.
        """

        stats = Stats.create_daily(self.dates, HORIZONS)
        stats.eval_start_idx = self.start_idx
        stats.eval_end_idx = self.end_idx
        eval_len = max(0, self.end_idx - self.start_idx)
        mask_matrix = self._build_mask_matrix(pattern_fn, eval_len)
        exit_mask_matrix = self._build_exit_mask_matrix(pattern_fn, len(self.dates))
        self._store_runtime_cache(
            cache_name,
            mask_matrix=mask_matrix,
            exit_mask_matrix=exit_mask_matrix,
        )
        trim_mode = _trim_mode_from_method(trim_method)
        self._accumulate_trim_dates(
            mask_matrix,
            exit_mask_matrix,
            bool(pattern_fn.has_exit_rule()),
            None,
            trim_q,
            trim_mode,
            stats,
            progress_label,
        )
        return stats

    def _run_pattern_filtered(
        self,
        pattern_fn: BasePattern,
        trim_quantile: float | None,
        trim_method: str,
        progress_label: str,
        aggregation_mode: str,
        filter_obj: _Filter,
        cache_name: str | None = None,
    ) -> Stats:
        """
        analyze(filter=...)를 반영해 패턴 통계를 계산한다.
        """

        agg_mode = _normalize_analyze_by(aggregation_mode)
        trim_q = _normalize_trim_quantile(trim_quantile)
        trim_method_text = _normalize_trim_method(trim_method)

        eval_len = max(0, self.end_idx - self.start_idx)
        filter_mask = filter_obj.mask_matrix()
        eval_filter_mask = None if filter_mask is None else filter_mask[self.start_idx:self.end_idx]
        allowed_cols = None if eval_filter_mask is None else np.any(eval_filter_mask, axis=0)
        exit_mask_matrix = self._build_exit_mask_matrix(pattern_fn, len(self.dates))
        use_exit_mask = bool(pattern_fn.has_exit_rule())
        progress_bar = None
        if eval_len > 0:
            progress_bar = _progress(total=eval_len + 1, desc=f"{progress_label} | prepare")
        try:
            raw_mask_matrix = self._build_mask_matrix(
                pattern_fn,
                eval_len,
                allowed_cols=allowed_cols,
                prefilter_mask=eval_filter_mask,
            )
            if progress_bar is not None:
                progress_bar.update(1)
                progress_bar.set_description_str(progress_label)
            mask_matrix = raw_mask_matrix
            self._store_runtime_cache(
                cache_name,
                filter_obj=filter_obj,
                mask_matrix=mask_matrix,
                exit_mask_matrix=exit_mask_matrix,
            )

            if agg_mode == AGG_MODE_DAY:
                stats = Stats.create_daily(self.dates, HORIZONS)
                stats.eval_start_idx = self.start_idx
                stats.eval_end_idx = self.end_idx
                if trim_q is None or trim_q <= 0.0:
                    self._accumulate_dates(
                        mask_matrix,
                        exit_mask_matrix,
                        use_exit_mask,
                        None,
                        stats,
                        progress_label,
                        write_daily=True,
                        progress_bar=progress_bar,
                    )
                    return stats

                trim_mode = _trim_mode_from_method(trim_method_text)
                self._accumulate_trim_dates(
                    mask_matrix,
                    exit_mask_matrix,
                    use_exit_mask,
                    None,
                    trim_q,
                    trim_mode,
                    stats,
                    progress_label,
                    progress_bar=progress_bar,
                )
                return stats

            stats = Stats.create(self.dates, HORIZONS)
            stats.eval_start_idx = self.start_idx
            stats.eval_end_idx = self.end_idx
            if trim_q is None or trim_q <= 0.0:
                self._accumulate_dates(
                    mask_matrix,
                    exit_mask_matrix,
                    use_exit_mask,
                    None,
                    stats,
                    progress_label,
                    write_daily=False,
                    progress_bar=progress_bar,
                )
                return stats

            # event 모드에서도 trim 적용을 위해 외부 daily 버퍼를 임시 생성한다.
            num_h = len(HORIZONS)
            num_dates = len(self.dates)
            tmp_daily_arith = np.full((num_h, num_dates), np.nan, dtype=np.float64)
            tmp_daily_rise = np.full((num_h, num_dates), np.nan, dtype=np.float64)
            trim_mode = _trim_mode_from_method(trim_method_text)
            self._accumulate_trim_dates_with_buffers(
                mask_matrix,
                exit_mask_matrix,
                use_exit_mask,
                None,
                trim_q,
                trim_mode,
                stats,
                progress_label,
                tmp_daily_arith,
                tmp_daily_rise,
                progress_bar=progress_bar,
            )
            return stats
        finally:
            if progress_bar is not None:
                progress_bar.close()

    def _run_pattern_dynamic(
        self,
        pattern_fn: BasePattern,
        trim_quantile: float | None,
        trim_method: str,
        progress_label: str,
        aggregation_mode: str,
        filter_obj: _Filter | None,
        cache_name: str | None = None,
    ) -> Stats:
        """
        진입시점 의존형 청산 규칙(trailing stop 등)을 반영해 통계를 계산한다.
        """

        agg_mode = _normalize_analyze_by(aggregation_mode)
        trim_q = _normalize_trim_quantile(trim_quantile)
        trim_method_text = _normalize_trim_method(trim_method)

        eval_len = max(0, self.end_idx - self.start_idx)
        eval_filter_mask = None
        allowed_cols = None
        if filter_obj is not None and filter_obj.is_active:
            filter_mask = filter_obj.mask_matrix()
            eval_filter_mask = None if filter_mask is None else filter_mask[self.start_idx:self.end_idx]
            allowed_cols = None if eval_filter_mask is None else np.any(eval_filter_mask, axis=0)

        progress_bar = None
        if eval_len > 0:
            progress_bar = _progress(total=eval_len + 2, desc=f"{progress_label} | prepare")
        try:
            mask_matrix = self._build_mask_matrix(
                pattern_fn,
                eval_len,
                allowed_cols=allowed_cols,
                prefilter_mask=eval_filter_mask,
            )
            if progress_bar is not None:
                progress_bar.update(1)

            exit_mask_matrix = self._build_exit_mask_matrix(pattern_fn, len(self.dates))
            use_static_exit_mask = bool(np.any(exit_mask_matrix))
            dynamic_exit_index = self._build_dynamic_exit_index_matrix(
                pattern_fn,
                mask_matrix,
                self.start_idx,
                int(np.max(self.horizon_offsets)),
            )
            if dynamic_exit_index.shape[0] != len(self.dates):
                full_dynamic_exit_index = np.full(
                    (len(self.dates), dynamic_exit_index.shape[1]),
                    -1,
                    dtype=np.int32,
                )
                full_dynamic_exit_index[self.start_idx:self.end_idx] = dynamic_exit_index
            else:
                full_dynamic_exit_index = dynamic_exit_index
            self._store_runtime_cache(
                cache_name,
                filter_obj=filter_obj,
                mask_matrix=mask_matrix,
                exit_mask_matrix=exit_mask_matrix,
                dynamic_exit_index=full_dynamic_exit_index,
            )
            if progress_bar is not None:
                progress_bar.update(1)
                progress_bar.set_description_str(progress_label)

            if agg_mode == AGG_MODE_DAY:
                stats = Stats.create_daily(self.dates, HORIZONS)
                stats.eval_start_idx = self.start_idx
                stats.eval_end_idx = self.end_idx
                if trim_q is None or trim_q <= 0.0:
                    self._accumulate_dates(
                        mask_matrix,
                        exit_mask_matrix,
                        use_static_exit_mask,
                        dynamic_exit_index,
                        stats,
                        progress_label,
                        write_daily=True,
                        progress_bar=progress_bar,
                    )
                    return stats

                trim_mode = _trim_mode_from_method(trim_method_text)
                self._accumulate_trim_dates(
                    mask_matrix,
                    exit_mask_matrix,
                    use_static_exit_mask,
                    dynamic_exit_index,
                    trim_q,
                    trim_mode,
                    stats,
                    progress_label,
                    progress_bar=progress_bar,
                )
                return stats

            stats = Stats.create(self.dates, HORIZONS)
            stats.eval_start_idx = self.start_idx
            stats.eval_end_idx = self.end_idx
            if trim_q is None or trim_q <= 0.0:
                self._accumulate_dates(
                    mask_matrix,
                    exit_mask_matrix,
                    use_static_exit_mask,
                    dynamic_exit_index,
                    stats,
                    progress_label,
                    write_daily=False,
                    progress_bar=progress_bar,
                )
                return stats

            num_h = len(HORIZONS)
            num_dates = len(self.dates)
            tmp_daily_arith = np.full((num_h, num_dates), np.nan, dtype=np.float64)
            tmp_daily_rise = np.full((num_h, num_dates), np.nan, dtype=np.float64)
            trim_mode = _trim_mode_from_method(trim_method_text)
            self._accumulate_trim_dates_with_buffers(
                mask_matrix,
                exit_mask_matrix,
                use_static_exit_mask,
                dynamic_exit_index,
                trim_q,
                trim_mode,
                stats,
                progress_label,
                tmp_daily_arith,
                tmp_daily_rise,
                progress_bar=progress_bar,
            )
            return stats
        finally:
            if progress_bar is not None:
                progress_bar.close()

    def _run_pattern(
        self,
        pattern_fn: BasePattern,
        trim_quantile: float | None = None,
        trim_method: str = "remove",
        progress_label: str = "pattern",
        aggregation_mode: str = AGG_MODE_EVENT,
        filter_obj: _Filter | None = None,
        cache_name: str | None = None,
    ) -> Stats:
        """
        패턴 trim 설정에 따라 normal/trim 실행 경로를 선택한다.
        """

        self._prepare_regime_sources(pattern_fn)
        self._prepare_market_sources(pattern_fn)
        agg_mode = _normalize_analyze_by(aggregation_mode)
        trim_q = _normalize_trim_quantile(trim_quantile)
        trim_method_text = _normalize_trim_method(trim_method)
        if pattern_fn.has_entry_dependent_exit():
            return self._run_pattern_dynamic(
                pattern_fn,
                trim_quantile=trim_q,
                trim_method=trim_method_text,
                progress_label=progress_label,
                aggregation_mode=agg_mode,
                filter_obj=filter_obj,
                cache_name=cache_name,
            )
        if filter_obj is not None and filter_obj.is_active:
            return self._run_pattern_filtered(
                pattern_fn,
                trim_quantile=trim_q,
                trim_method=trim_method_text,
                progress_label=progress_label,
                aggregation_mode=agg_mode,
                filter_obj=filter_obj,
                cache_name=cache_name,
            )

        if (
            (trim_q is None or trim_q <= 0.0)
            and self._is_default_price_pattern(pattern_fn)
            and pattern_fn._resolved_max_cohort_size() is None
        ):
            return self._run_pattern_default_price_fast(
                pattern_fn,
                progress_label=progress_label,
                aggregation_mode=agg_mode,
                cache_name=cache_name,
            )

        if agg_mode == AGG_MODE_EVENT:
            if trim_q is None or trim_q <= 0.0:
                return self._run_pattern_normal(
                    pattern_fn,
                    progress_label,
                    cache_name=cache_name,
                )
            return self._run_pattern_trim(
                pattern_fn,
                trim_q,
                trim_method_text,
                progress_label,
                cache_name=cache_name,
            )

        # day_mean 모드: trim 미설정(None)이어도 일자균등 평균을 계산한다.
        daily_trim_q = 0.0 if trim_q is None else float(trim_q)
        return self._run_pattern_trim(
            pattern_fn,
            daily_trim_q,
            trim_method_text,
            progress_label,
            cache_name=cache_name,
        )

    @staticmethod
    def _resolve_horizon(h: str | int) -> tuple[str, int]:
        """
        horizon 입력을 (라벨, 거래일 수) 쌍으로 정규화한다.
        """

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

    def _resolve_screen_date_index(self, date) -> int:
        """
        스크리닝 대상 날짜를 내부 거래일 인덱스로 변환한다.
        """

        date_ts = pd.Timestamp(date)
        target = date_ts.to_datetime64()
        idx = int(np.searchsorted(self.dates, target, side="left"))
        if idx < len(self.dates) and self.dates[idx] == target:
            return idx

        prev_text = None
        next_text = None
        if idx > 0:
            prev_text = str(pd.Timestamp(self.dates[idx - 1]).date())
        if idx < len(self.dates):
            next_text = str(pd.Timestamp(self.dates[idx]).date())
        raise ValueError(
            f"요청한 날짜({date_ts.date()})는 거래일 데이터에 없습니다. "
            f"이전 거래일={prev_text}, 다음 거래일={next_text}"
        )

    def _resolve_screen_pattern(
        self,
        pattern: BasePattern,
    ) -> tuple[str, BasePattern, bool]:
        """
        스크리닝용 패턴 이름과 캐시 사용 가능 여부를 결정한다.
        """

        if not isinstance(pattern, BasePattern):
            raise TypeError("screen()의 pattern은 BasePattern 객체여야 합니다.")

        for name, registered in self._analyzed_patterns.items():
            if registered is pattern:
                return name, pattern, True

        inferred_name = _infer_pattern_label(pattern, len(self._analyzed_patterns) + 1)
        return inferred_name, pattern, False

    def _code_names_for(self, codes: list[str]) -> list[str]:
        """
        코드 목록을 종목명 목록으로 변환한다(없으면 코드 유지).
        """

        if not codes:
            return []
        if self.code_names:
            names = []
            for code in codes:
                code_key = str(code)
                name = str(self.code_names.get(code_key, "")).strip()
                names.append(name if name else code_key)
            return names

        code_name = self.data_loader.load_code_name_series()
        if code_name.empty:
            return list(codes)

        looked_up = code_name.reindex(pd.Index(codes, dtype="object"))
        names: list[str] = []
        values = looked_up.to_numpy(dtype=object, copy=False)
        for i, code in enumerate(codes):
            name_val = values[i]
            if pd.isna(name_val):
                names.append(code)
                continue
            text = str(name_val).strip()
            names.append(text if text else code)
        return names

    def _prepare_filter(self, filter_obj: _Filter | None, show_progress: bool) -> _Filter | None:
        """
        analyze(filter=...)로 전달된 실행 필터를 현재 Backtest 축에 바인딩하고 준비한다.
        """

        if filter_obj is None:
            return None
        if not isinstance(filter_obj, _Filter):
            raise TypeError("filter는 Filter 객체여야 합니다.")
        if not filter_obj.is_active:
            return None
        filter_obj.bind(
            dates=self.dates,
            codes=self.codes,
            prices=self.prices,
            data_loader=self.data_loader,
            univ=self.univ,
        )
        filter_obj.prepare(show_progress=show_progress)
        return filter_obj

    def _build_pattern_mask_matrix(
        self,
        pattern_name: str,
        pattern_fn: BasePattern,
        filter_obj: _Filter | None = None,
    ) -> np.ndarray:
        """
        전체 기간 패턴 마스크를 생성/캐시한다.
        """

        cache_key = (pattern_name, bool(filter_obj is not None and filter_obj.is_active))
        if cache_key in self._pattern_mask_cache:
            return self._pattern_mask_cache[cache_key]

        self._prepare_market_sources(pattern_fn)
        full_filter_mask = None
        allowed_cols = None
        if filter_obj is not None and filter_obj.is_active:
            full_filter_mask = filter_obj.mask_matrix()
            allowed_cols = None if full_filter_mask is None else np.any(full_filter_mask, axis=0)
        mask_matrix = self._build_mask_matrix(
            pattern_fn,
            eval_len=len(self.dates),
            allowed_cols=allowed_cols,
            prefilter_mask=full_filter_mask,
        )
        self._pattern_mask_cache[cache_key] = mask_matrix
        return mask_matrix

    def _build_pattern_policy_id_matrix(
        self,
        pattern_name: str,
        pattern_fn: BasePattern,
        filter_obj: _Filter | None = None,
    ) -> tuple[np.ndarray, Dict[int, tuple[object | None, float | None, float | None, float | None]]]:
        cache_key = (pattern_name, bool(filter_obj is not None and filter_obj.is_active))
        if cache_key in self._pattern_policy_id_cache:
            return (
                self._pattern_policy_id_cache[cache_key],
                dict(self._pattern_trade_profile_cache.get(cache_key, {})),
            )

        pattern_mask = self._build_pattern_mask_matrix(
            pattern_name,
            pattern_fn,
            filter_obj=filter_obj,
        )
        try:
            resolved_profile = pattern_fn._resolved_trade_profile()
        except ValueError:
            resolved_profile = None
        if resolved_profile is not None:
            policy_id_matrix = np.zeros(pattern_mask.shape, dtype=np.int16)
            policy_id_matrix[pattern_mask] = 1
            trade_profiles = {1: resolved_profile}
            self._pattern_policy_id_cache[cache_key] = policy_id_matrix
            self._pattern_trade_profile_cache[cache_key] = dict(trade_profiles)
            return policy_id_matrix, dict(trade_profiles)

        self._prepare_regime_sources(pattern_fn)
        self._prepare_market_sources(pattern_fn)

        full_filter_mask = None
        allowed_cols = None
        if filter_obj is not None and filter_obj.is_active:
            full_filter_mask = filter_obj.mask_matrix()
            allowed_cols = None if full_filter_mask is None else np.any(full_filter_mask, axis=0)

        num_codes = len(self.codes)
        policy_id_matrix = np.zeros((len(self.dates), num_codes), dtype=np.int16)
        profile_to_id: dict[tuple[object | None, float | None, float | None, float | None], int] = {}
        id_to_profile: dict[int, tuple[object | None, float | None, float | None, float | None]] = {}

        column_indices = range(num_codes)
        if allowed_cols is not None:
            column_indices = np.flatnonzero(allowed_cols)

        for col_idx in column_indices:
            values = self.prices[:, col_idx]
            self._prepare_stock_sources(pattern_fn, col_idx)
            col_policy_ids = np.asarray(
                pattern_fn._build_policy_id_mask(values, profile_to_id, id_to_profile),
                dtype=np.int16,
            )
            if col_policy_ids.shape != (len(self.dates),):
                raise ValueError("pattern policy id mask shape이 일치하지 않습니다.")
            if full_filter_mask is not None:
                col_policy_ids = np.where(full_filter_mask[:, col_idx], col_policy_ids, 0)
            policy_id_matrix[:, col_idx] = col_policy_ids

        policy_id_matrix = np.where(pattern_mask, policy_id_matrix, 0)

        self._pattern_policy_id_cache[cache_key] = policy_id_matrix
        self._pattern_trade_profile_cache[cache_key] = dict(id_to_profile)
        return policy_id_matrix, dict(id_to_profile)

    def _all_stock_opportunity_counts(self) -> np.ndarray:
        """
        horizon별 전체 종목의 유효 event 기회 수를 일자축으로 반환한다.
        """

        if self._all_opportunity_count_cache is not None:
            return self._all_opportunity_count_cache

        num_dates = self.prices.shape[0]
        num_h = len(self.horizon_offsets)
        counts = np.zeros((num_h, num_dates), dtype=np.int64)
        for h_idx, step in enumerate(self.horizon_offsets):
            step = int(step)
            if step <= 0 or step >= num_dates:
                continue
            base = self.prices[:-step]
            fwd = self.prices[step:]
            valid = np.isfinite(base) & np.isfinite(fwd) & (base > 0.0) & (fwd > 0.0)
            counts[h_idx, : num_dates - step] = np.count_nonzero(valid, axis=1)

        self._all_opportunity_count_cache = counts
        return counts

    def _get_vwap_matrix(self) -> np.ndarray:
        """
        매매 체결/평가용 VWAP(= (open+high+low+close)/4) 매트릭스를 반환한다.
        """

        if self._vwap_matrix is None:
            idx = pd.DatetimeIndex(self.dates)
            cols = pd.Index(self.codes, dtype="object")

            open_df = self.data_loader.load_stock_field_table("open", self.univ).reindex(index=idx, columns=cols)
            high_df = self.data_loader.load_stock_field_table("high", self.univ).reindex(index=idx, columns=cols)
            low_df = self.data_loader.load_stock_field_table("low", self.univ).reindex(index=idx, columns=cols)
            close_df = self.data_loader.load_stock_field_table("close", self.univ).reindex(index=idx, columns=cols)
            vwap_df = (open_df + high_df + low_df + close_df) / 4.0
            self._vwap_matrix = vwap_df.to_numpy(dtype=np.float64, copy=True)
        return self._vwap_matrix

    def _resolve_trade_price_mode(self, trade_price_mode: str) -> tuple[np.ndarray, int, str]:
        """
        매매가격 모드를 (가격매트릭스, 실행시차일수, 정규화라벨)로 변환한다.
        """

        key = str(trade_price_mode).strip().lower().replace(" ", "")
        if key in {"당일종가", "same_close", "sameclose"}:
            return self.prices, 0, "same_close"
        if key in {"익일종가", "next_close", "nextclose"}:
            return self.prices, 1, "next_close"
        if key in {"익일vwap", "next_vwap", "nextvwap", "vwap"}:
            return self._get_vwap_matrix(), 1, "next_vwap"
        raise ValueError("trade_price_mode은 '당일종가'/'익일종가'/'익일VWAP' 중 하나여야 합니다.")

    def screen(
        self,
        date,
        pattern: BasePattern,
        use_cache: bool = True,
    ) -> pd.DataFrame:
        """
        특정 거래일에 패턴을 만족하는 종목 목록을 반환한다.
        """

        date_idx = self._resolve_screen_date_index(date)
        pattern_name, pattern_fn, cache_allowed = self._resolve_screen_pattern(pattern)

        row_mask = np.zeros(len(self.codes), dtype=np.bool_)
        filter_obj = self._analyzed_filters.get(pattern_name) if cache_allowed else None
        filter_row = None if filter_obj is None else filter_obj.mask_matrix()[date_idx]
        use_cache_now = bool(use_cache and cache_allowed)
        if use_cache_now:
            mask_matrix = self._build_pattern_mask_matrix(
                pattern_name,
                pattern_fn,
                filter_obj=filter_obj,
            )
            row_mask = np.asarray(mask_matrix[date_idx], dtype=np.bool_)
        else:
            self._prepare_regime_sources(pattern_fn)
            self._prepare_market_sources(pattern_fn)
            for col_idx, code in enumerate(self.codes):
                if filter_row is not None and not filter_row[col_idx]:
                    continue
                values = self.prices[:, col_idx]
                self._prepare_stock_sources(pattern_fn, col_idx)
                mask = self._compute_mask(pattern_fn, values, code)
                if mask is None:
                    continue
                row_mask[col_idx] = bool(mask[date_idx])
            row_mask = self._apply_pattern_nmax(
                pattern_fn,
                row_mask.reshape(1, -1),
                date_idx,
            )[0]

        selected_idx = np.flatnonzero(row_mask)
        selected_codes = [self.codes[i] for i in selected_idx]
        selected_names = self._code_names_for(selected_codes)
        selected_prices = self.prices[date_idx, selected_idx]
        return pd.DataFrame(
            {
                "name": selected_names,
                "close": selected_prices.astype(np.float64, copy=False),
            },
            index=pd.Index(selected_codes, name="code"),
        )

    def _pattern_filter(self, pattern: str) -> _Filter | None:
        """
        analyze 결과 패턴명에 연결된 Filter를 반환한다.
        """

        return self._analyzed_filters.get(pattern)

    def _require_last_stats_collection(self) -> StatsCollection:
        """
        마지막 analyze() 결과를 반환한다.
        """

        if self._last_stats_collection is None:
            raise ValueError("plot() 전에 analyze()를 먼저 실행해야 합니다.")
        return self._last_stats_collection

    def _require_last_simulator(self) -> Simulator:
        """
        마지막 run() 결과를 반환한다.
        """

        if self._last_simulator is None:
            raise ValueError("plot() 전에 run()을 먼저 실행해야 합니다.")
        return self._last_simulator

    @property
    def stats(self) -> StatsCollection:
        """
        마지막 analyze() 결과를 공개 속성처럼 조회한다.
        """

        return self._require_last_stats_collection()

    @property
    def simulator(self) -> Simulator:
        """
        마지막 run() 결과를 공개 속성처럼 조회한다.
        """

        return self._require_last_simulator()

    def run(
        self,
        start=None,
        end=None,
        pattern: str = "",
        target_horizon: str | int = "1M",
        trade_price_mode: str = "익일VWAP",
        stop_loss_pct: float | None = None,
        take_profit_pct: float | None = None,
        allow_reentry: bool = True,
        min_cohort_size: int = 1,
    ) -> Simulator:
        """
        분석된 패턴 통계를 기반으로 포트폴리오 시뮬레이션을 실행하고 마지막 결과로 저장한다.
        """
        # 1) 입력 파라미터를 내부 인덱스/거래일 단위로 정규화
        if pattern not in self._analyzed_patterns or pattern not in self._analyzed_stats:
            available = sorted(self._analyzed_patterns.keys())
            raise ValueError(
                f"analyze() 결과에서 pattern '{pattern}'을 찾을 수 없습니다. "
                f"사용 가능: {available}"
            )
        horizon_label, horizon_days = self._resolve_horizon(target_horizon)

        run_start = pd.Timestamp(self.start if start is None else start)
        run_end = pd.Timestamp(self.end if end is None else end)
        if run_end < run_start:
            raise ValueError("end는 start보다 빠를 수 없습니다.")

        start_idx = int(np.searchsorted(self.dates, run_start.to_datetime64(), side="left"))
        end_idx = int(np.searchsorted(self.dates, run_end.to_datetime64(), side="right"))
        end_idx = min(end_idx, len(self.dates))
        if end_idx - start_idx < 2:
            raise ValueError("run 구간에 최소 2개 이상의 거래일이 필요합니다.")

        # 2) 패턴 실행 준비
        pattern_fn = self._analyzed_patterns[pattern]
        pattern_filter = self._pattern_filter(pattern)
        max_cohort_size = pattern_fn._resolved_max_cohort_size()
        pattern_mask = self._build_pattern_mask_matrix(
            pattern,
            pattern_fn,
            filter_obj=pattern_filter,
        )
        (
            pattern_policy_id_matrix,
            pattern_trade_profiles,
        ) = self._build_pattern_policy_id_matrix(
            pattern,
            pattern_fn,
            filter_obj=pattern_filter,
        )
        pattern_exit_mask = self._build_pattern_exit_mask_matrix(pattern, pattern_fn)
        pattern_dynamic_exit_index = None
        if pattern_fn.has_entry_dependent_exit():
            pattern_dynamic_exit_index = self._build_pattern_dynamic_exit_index_matrix(
                pattern,
                pattern_fn,
                pattern_mask,
                horizon_days,
            )

        if self.code_names:
            code_names = dict(self.code_names)
        else:
            code_name_series = self.data_loader.load_code_name_series()
            code_names = {
                str(code): str(name).strip()
                for code, name in code_name_series.items()
                if pd.notna(name) and str(name).strip()
            }

        # 3) Simulator로 주문/보유/청산 루프 실행
        trade_prices, execution_lag_days, execution_price_mode = self._resolve_trade_price_mode(
            trade_price_mode
        )
        default_stop_loss_pct = Simulator._normalize_stop_loss_pct(stop_loss_pct)
        default_take_profit_pct = Simulator._normalize_take_profit_pct(take_profit_pct)
        max_policy_id = max(pattern_trade_profiles.keys(), default=0)
        policy_horizon_days = np.full(max_policy_id + 1, int(horizon_days), dtype=np.int32)
        policy_stop_loss_pct = np.full(max_policy_id + 1, np.nan, dtype=np.float64)
        policy_take_profit_pct = np.full(max_policy_id + 1, np.nan, dtype=np.float64)
        policy_cohort_scale = np.ones(max_policy_id + 1, dtype=np.float64)
        if default_stop_loss_pct is not None:
            policy_stop_loss_pct[:] = float(default_stop_loss_pct)
        if default_take_profit_pct is not None:
            policy_take_profit_pct[:] = float(default_take_profit_pct)
        for policy_id, profile in pattern_trade_profiles.items():
            horizon_override, stop_override, take_override, cohort_scale_override = profile
            resolved_horizon_days = int(horizon_days)
            if horizon_override is not None:
                _, resolved_horizon_days = self._resolve_horizon(horizon_override)
            policy_horizon_days[int(policy_id)] = int(resolved_horizon_days)
            if stop_override is not None:
                policy_stop_loss_pct[int(policy_id)] = float(stop_override)
            if take_override is not None:
                policy_take_profit_pct[int(policy_id)] = float(take_override)
            if cohort_scale_override is not None:
                policy_cohort_scale[int(policy_id)] = float(cohort_scale_override)
        simulator = Simulator(
            dates=self.dates,
            prices=trade_prices,
            codes=self.codes,
            code_names=code_names,
        )
        result = simulator.run(
            start_idx=start_idx,
            end_idx=end_idx,
            pattern=pattern,
            target_horizon=horizon_label,
            target_horizon_days=horizon_days,
            pattern_mask=pattern_mask,
            pattern_policy_id_matrix=pattern_policy_id_matrix,
            policy_horizon_days=policy_horizon_days,
            policy_stop_loss_pct=policy_stop_loss_pct,
            policy_take_profit_pct=policy_take_profit_pct,
            policy_cohort_scale=policy_cohort_scale,
            pattern_exit_mask=pattern_exit_mask,
            pattern_dynamic_exit_index=pattern_dynamic_exit_index,
            stop_loss_pct=stop_loss_pct,
            take_profit_pct=take_profit_pct,
            execution_lag_days=execution_lag_days,
            execution_price_mode=execution_price_mode,
            allow_reentry=allow_reentry,
            min_cohort_size=min_cohort_size,
            max_cohort_size=max_cohort_size,
        )
        regime_mask = self._combined_regime_mask(pattern_fn)
        if regime_mask is not None and result.data is not None:
            result.data.attrs["regime_active_mask"] = np.asarray(
                regime_mask[start_idx:end_idx],
                dtype=np.bool_,
            ).copy()
        if result.data is not None:
            kospi_curve = self._normalized_kospi_reference_curve(result.data.index)
            if kospi_curve is not None:
                result.data.attrs["kospi_reference_curve"] = kospi_curve
        self._last_simulator = result
        return result

    def analyze(
        self,
        *patterns: BasePattern,
        include_base: bool = True,
        filter: _Filter | None = None,
    ) -> StatsCollection:
        """
        패턴들을 평가해 StatsCollection 결과를 생성하고 마지막 결과로 저장한다.
        """

        aggregation_mode = self.by
        analyze_filter = self._prepare_filter(filter, show_progress=True)

        stats_map: Dict[str, Stats] = {}
        benchmark_names: set[str] = set()
        if include_base and self.benchmark is not None:
            stats_map.update(self._base_stats)
            benchmark_names.update(self._base_stats.keys())

        for idx, pattern_fn in enumerate(patterns, start=len(stats_map) + 1):
            if not isinstance(pattern_fn, BasePattern):
                raise TypeError("analyze()에 전달한 모든 패턴은 BasePattern 객체여야 합니다.")
            pattern_fn = self._apply_default_regime(pattern_fn)
            base_name = _infer_pattern_label(pattern_fn, idx)
            trim_q, trim_method = _infer_pattern_trim_config(pattern_fn)
            name = base_name
            suffix = 2
            while name in stats_map:
                name = f"{base_name}_{suffix}"
                suffix += 1
            self._invalidate_runtime_cache(name)
            stats = self._run_pattern(
                pattern_fn,
                trim_quantile=trim_q,
                trim_method=trim_method,
                progress_label=name,
                aggregation_mode=aggregation_mode,
                filter_obj=analyze_filter,
                cache_name=name,
            )
            stats_map[name] = stats
            self._analyzed_patterns[name] = pattern_fn
            self._analyzed_stats[name] = stats
            self._analyzed_filters[name] = analyze_filter

        if not stats_map:
            raise ValueError("실행된 패턴이 없습니다.")
        result = StatsCollection(
            stats_map,
            benchmark_names=benchmark_names,
        )
        result.exposure_opportunity_counts = self._all_stock_opportunity_counts()
        self._last_stats_collection = result
        self._last_simulator = None
        return result

    def plot(
        self,
        patterns: list[str] | tuple[str, ...] | None = None,
        start=None,
        end=None,
        figsize=(14.5, 8.0),
        annualized: bool = False,
        cost: bool | None = None,
        rise_ylim=None,
        return_ylim=None,
        show_kospi: bool = False,
        hspace: float = 0.2,
        wspace: float = 0.7,
        return_handles: bool = False,
    ):
        """
        마지막 analyze()/run() 결과를 결합해 통합 figure를 그린다.
        """

        return plot_backtest(
            self,
            patterns=patterns,
            start=start,
            end=end,
            figsize=figsize,
            annualized=annualized,
            cost=cost,
            rise_ylim=rise_ylim,
            return_ylim=return_ylim,
            show_kospi=show_kospi,
            hspace=hspace,
            wspace=wspace,
            return_handles=return_handles,
        )

__all__ = ["Backtest"]
