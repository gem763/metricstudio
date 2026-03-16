"""Fully numpy + numba backtesting pipeline."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Dict, List, Tuple, Optional
import math
import re

import numpy as np
import pandas as pd
from numba import njit
from tqdm import tqdm

from src.db_manager import (
    DB as DuckDBManager,
    DEFAULT_DEPT_EXCLUDES,
    DEFAULT_MARKETS,
)
from src.db_manager_archive import DB as ArchiveDBManager
from src.pattern import Pattern
from src.regime import Regime, build_market_cap_bucket_masks, build_regime_frame, regime_mask_from_frame
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
TRADING_DAYS_PER_YEAR = 240

TRIM_MODE_REMOVE = 0
TRIM_MODE_WINSORIZE = 1
AGG_MODE_EVENT = "event"
AGG_MODE_DAY = "day"


def _progress(*args, **kwargs):
    """
    노트북 저장 시 widget 출력이 남지 않도록 일반 tqdm를 사용한다.
    """

    kwargs.setdefault("leave", True)
    kwargs.setdefault("dynamic_ncols", True)
    return tqdm(*args, **kwargs)


@dataclass
class StockTable:
    """
    백테스트에 쓰는 정렬된 가격 테이블(날짜 x 종목) 컨테이너.
    """

    dates: np.ndarray  # shape (T,)
    prices: np.ndarray  # shape (T, N)
    codes: List[str]
    code_names: Dict[str, str]


def _normalize_univ_markets(values) -> tuple[str, ...] | None:
    if values is None:
        return None
    if isinstance(values, str):
        items = [values]
    else:
        items = list(values)
    out: list[str] = []
    for item in items:
        text = str(item).strip().upper()
        if text and text not in out:
            out.append(text)
    if not out:
        raise ValueError("market은 비어 있을 수 없습니다.")
    return tuple(out)


def _normalize_univ_depts(values) -> tuple[str, ...]:
    if values is None:
        return ()
    if isinstance(values, str):
        items = [values]
    else:
        items = list(values)
    out: list[str] = []
    for item in items:
        text = str(item).strip()
        if text and text not in out:
            out.append(text)
    return tuple(out)


@dataclass(frozen=True)
class Univ:
    market: tuple[str, ...] | None = DEFAULT_MARKETS
    is_tradable: bool | None = True
    dept_excludes: tuple[str, ...] = DEFAULT_DEPT_EXCLUDES

    def __init__(
        self,
        market=DEFAULT_MARKETS,
        is_tradable: bool | None = True,
        dept_excludes=DEFAULT_DEPT_EXCLUDES,
    ):
        object.__setattr__(self, "market", _normalize_univ_markets(market))
        object.__setattr__(self, "is_tradable", None if is_tradable is None else bool(is_tradable))
        object.__setattr__(self, "dept_excludes", _normalize_univ_depts(dept_excludes))

    def cache_key(self) -> tuple[tuple[str, ...] | None, bool | None, tuple[str, ...]]:
        return self.market, self.is_tradable, self.dept_excludes


def _normalize_bucket_list(values, name: str) -> tuple[int, ...] | None:
    """
    10분위 구간 입력을 1~10 정수 튜플로 정규화한다.
    """

    if values is None:
        return None
    if isinstance(values, (str, int, np.integer)):
        items = [values]
    else:
        items = list(values)
    if not items:
        return None

    out: list[int] = []
    for item in items:
        if isinstance(item, (int, np.integer)):
            q = int(item)
        else:
            match = re.fullmatch(r"(10|[1-9])\s*[QqDd]?", str(item).strip())
            if match is None:
                raise ValueError(f"{name}는 1~10 또는 '1Q'~'10Q' 형식만 지원합니다.")
            q = int(match.group(1))
        if q < 1 or q > 10:
            raise ValueError(f"{name}는 1~10 또는 '1Q'~'10Q' 형식만 지원합니다.")
        if q not in out:
            out.append(q)
    return tuple(sorted(out))


def _normalize_filter_order(values) -> tuple[str, ...] | None:
    """
    순차 적용할 필터 순서를 정규화한다.
    """

    if values is None:
        return None
    if isinstance(values, str):
        items = [values]
    else:
        items = list(values)
    if not items:
        return None

    alias_map = {
        "market_cap": "market_cap",
        "marketcap": "market_cap",
        "liquidity": "liquidity",
    }
    out: list[str] = []
    for item in items:
        key = str(item).strip().lower()
        if key not in alias_map:
            raise ValueError("order는 'market_cap'과 'liquidity'만 지원합니다.")
        normalized = alias_map[key]
        if normalized not in out:
            out.append(normalized)
    return tuple(out)


class Filter:
    """
    날짜별 종목 필터 마스크를 계산하고 조회한다.
    """

    def __init__(self, market_cap=None, liquidity=None, order=None):
        self.market_cap_buckets = _normalize_bucket_list(market_cap, "market_cap")
        self.liquidity_buckets = _normalize_bucket_list(liquidity, "liquidity")
        self.order = _normalize_filter_order(order)
        self._dates: np.ndarray | None = None
        self._codes: list[str] | None = None
        self._prices: np.ndarray | None = None
        self._db_mode: int | None = None
        self._univ: Univ | None = None
        self._marketcap_matrix: np.ndarray | None = None
        self._liquidity_matrix: np.ndarray | None = None
        self._mask_matrix: np.ndarray | None = None

    @property
    def is_active(self) -> bool:
        return self.market_cap_buckets is not None or self.liquidity_buckets is not None

    def bind(
        self,
        *,
        dates: np.ndarray,
        codes: list[str],
        prices: np.ndarray,
        db_mode: int,
        univ: Univ,
    ) -> None:
        self._dates = dates
        self._codes = codes
        self._prices = prices
        self._db_mode = db_mode
        self._univ = univ
        self._marketcap_matrix = None
        self._liquidity_matrix = None
        self._mask_matrix = None

    def _require_bound(self) -> tuple[np.ndarray, list[str], np.ndarray, int, Univ]:
        if (
            self._dates is None
            or self._codes is None
            or self._prices is None
            or self._db_mode is None
            or self._univ is None
        ):
            raise ValueError("Filter는 Backtest.analyze(..., filter=...) 이후에 사용할 수 있습니다.")
        return self._dates, self._codes, self._prices, self._db_mode, self._univ

    @staticmethod
    def _build_decile_mask_matrix(
        values: np.ndarray,
        buckets: tuple[int, ...],
        base_mask: np.ndarray | None = None,
    ) -> np.ndarray:
        mask = np.zeros(values.shape, dtype=np.bool_)
        selected_buckets = set(int(q) for q in buckets)
        for row_idx in range(values.shape[0]):
            row = values[row_idx]
            valid_mask = np.isfinite(row)
            if base_mask is not None:
                valid_mask &= base_mask[row_idx]
            valid_idx = np.flatnonzero(valid_mask)
            if valid_idx.size == 0:
                continue
            order = valid_idx[np.argsort(row[valid_idx], kind="mergesort")]
            deciles = ((np.arange(order.size) * 10) // order.size) + 1
            keep = np.fromiter((int(q) in selected_buckets for q in deciles), dtype=np.bool_)
            mask[row_idx, order[keep]] = True
        return mask

    def _ordered_steps(self) -> list[tuple[str, tuple[int, ...]]]:
        steps: list[tuple[str, tuple[int, ...]]] = []
        if self.market_cap_buckets is not None:
            steps.append(("market_cap", self.market_cap_buckets))
        if self.liquidity_buckets is not None:
            steps.append(("liquidity", self.liquidity_buckets))
        if not steps:
            return steps
        if self.order is None:
            return steps

        step_map = {name: buckets for name, buckets in steps}
        ordered: list[tuple[str, tuple[int, ...]]] = []
        for name in self.order:
            buckets = step_map.pop(name, None)
            if buckets is not None:
                ordered.append((name, buckets))
        for name, buckets in steps:
            if name in step_map:
                ordered.append((name, buckets))
                step_map.pop(name, None)
        return ordered

    def _get_marketcap_matrix(self) -> np.ndarray:
        dates, codes, _, db_mode, univ = self._require_bound()
        if self._marketcap_matrix is None:
            mcap_df = _load_stock_field_table("marketcap", db_mode, univ)
            aligned = mcap_df.reindex(
                index=pd.DatetimeIndex(dates),
                columns=pd.Index(codes, dtype="object"),
            )
            self._marketcap_matrix = aligned.to_numpy(dtype=np.float64, copy=True)
        return self._marketcap_matrix

    def _get_liquidity_matrix(self) -> np.ndarray:
        dates, codes, _, db_mode, univ = self._require_bound()
        if self._liquidity_matrix is None:
            tables = _load_stock_field_tables(["amount", "marketcap"], db_mode, univ)
            amount_df = tables["amount"].reindex(index=pd.DatetimeIndex(dates), columns=pd.Index(codes, dtype="object"))
            marketcap_df = tables["marketcap"].reindex(index=pd.DatetimeIndex(dates), columns=pd.Index(codes, dtype="object"))
            amount = amount_df.to_numpy(dtype=np.float64, copy=True)
            marketcap = marketcap_df.to_numpy(dtype=np.float64, copy=True)
            ratio = np.full(amount.shape, np.nan, dtype=np.float64)
            valid = np.isfinite(amount) & np.isfinite(marketcap) & (marketcap > 0.0)
            ratio[valid] = amount[valid] / marketcap[valid]
            self._liquidity_matrix = ratio
        return self._liquidity_matrix

    def _build_mask_matrix(self) -> np.ndarray:
        _, _, prices, _, _ = self._require_bound()
        mask = np.isfinite(prices) & (prices > 0.0)
        if self.order is None:
            if self.market_cap_buckets is not None:
                marketcap = self._get_marketcap_matrix()
                marketcap_mask = self._build_decile_mask_matrix(
                    np.where(marketcap > 0.0, marketcap, np.nan),
                    self.market_cap_buckets,
                )
                mask &= marketcap_mask
            if self.liquidity_buckets is not None:
                liquidity = self._get_liquidity_matrix()
                liquidity_mask = self._build_decile_mask_matrix(liquidity, self.liquidity_buckets)
                mask &= liquidity_mask
            return mask

        for step_name, buckets in self._ordered_steps():
            if step_name == "market_cap":
                marketcap = self._get_marketcap_matrix()
                step_mask = self._build_decile_mask_matrix(
                    np.where(marketcap > 0.0, marketcap, np.nan),
                    buckets,
                    base_mask=mask,
                )
            elif step_name == "liquidity":
                liquidity = self._get_liquidity_matrix()
                step_mask = self._build_decile_mask_matrix(
                    liquidity,
                    buckets,
                    base_mask=mask,
                )
            else:
                raise ValueError(f"지원하지 않는 필터 단계입니다: {step_name}")
            mask &= step_mask
        return mask

    def prepare(self, show_progress: bool = True) -> None:
        if not self.is_active:
            return
        self._require_bound()

        tasks: list[tuple[str, Callable[[], np.ndarray]]] = []
        if self.market_cap_buckets is not None and self._marketcap_matrix is None:
            tasks.append(("시가총액 로드", self._get_marketcap_matrix))
        if self.liquidity_buckets is not None and self._liquidity_matrix is None:
            tasks.append(("유동성 로드", self._get_liquidity_matrix))
        if self._mask_matrix is None:
            tasks.append(("필터 마스크", self._build_mask_matrix))
        if not tasks:
            return

        progress_bar = _progress(total=len(tasks), desc="실행필터 준비") if show_progress else None
        try:
            for label, task in tasks:
                if progress_bar is not None:
                    progress_bar.set_description_str(f"실행필터 준비 | {label}")
                result = task()
                if label == "필터 마스크":
                    self._mask_matrix = result
                if progress_bar is not None:
                    progress_bar.update(1)
        finally:
            if progress_bar is not None:
                progress_bar.close()

    def mask_matrix(self) -> np.ndarray | None:
        if not self.is_active:
            return None
        self.prepare(show_progress=False)
        return self._mask_matrix

    def get(self, at) -> list[str]:
        dates, codes, _, _, _ = self._require_bound()
        mask_matrix = self.mask_matrix()
        if mask_matrix is None:
            return list(codes)

        target = pd.Timestamp(at).to_datetime64()
        idx = int(np.searchsorted(dates, target, side="left"))
        if idx >= len(dates) or dates[idx] != target:
            raise ValueError(f"날짜 {pd.Timestamp(at).date()} 가 Backtest 거래일 범위에 없습니다.")
        selected = np.flatnonzero(mask_matrix[idx])
        return [codes[i] for i in selected]


@dataclass
class GateDiagnostics:
    """
    게이트 분류력 진단 결과 컨테이너.
    """

    summary: pd.Series
    samples: pd.DataFrame

    @staticmethod
    def _as_float(value) -> float:
        """
        숫자형으로 변환 가능한 값을 float로 정규화한다.
        """

        try:
            out = float(value)
        except (TypeError, ValueError):
            return float("nan")
        return out if np.isfinite(out) else float("nan")

    def core_summary(self) -> pd.Series:
        """
        분류력 검증에 필요한 핵심 지표만 추린 요약을 반환한다.
        """

        keys = [
            "samples",
            "pass_rate",
            "uplift_mean_ret",
            "uplift_win_rate",
            "precision",
            "recall",
            "f1",
            "ic_gate_vs_ret",
            "t_stat_pass_minus_fail",
            "p_norm_pass_minus_fail",
            "gate_geom_min",
            "gate_arith_min",
            "gate_rise_min",
            "gate_use_geom",
            "gate_use_arith",
            "gate_use_rise",
            "dqs_return_score",
            "dqs_win_score",
            "dqs_classification_score",
            "dqs_quality_score",
            "dqs_score",
        ]
        out = {key: self.summary.get(key, np.nan) for key in keys}
        return pd.Series(out, name=self.summary.name, dtype="object")

    def plot(self, figsize=(16.8, 4.4), return_handles: bool = False):
        """
        핵심 분류력 지표를 4개 패널로 시각화한다.
        """

        import matplotlib.pyplot as plt

        core = self.core_summary()
        samples = int(round(self._as_float(core.get("samples", np.nan))))
        pass_rate = self._as_float(core.get("pass_rate", np.nan))
        ic = self._as_float(core.get("ic_gate_vs_ret", np.nan))
        p_norm = self._as_float(core.get("p_norm_pass_minus_fail", np.nan))
        gate_geom_min = self._as_float(core.get("gate_geom_min", np.nan))
        gate_arith_min = self._as_float(core.get("gate_arith_min", np.nan))
        gate_rise_min = self._as_float(core.get("gate_rise_min", np.nan))
        gate_use_geom = bool(self.summary.get("gate_use_geom", False))
        gate_use_arith = bool(self.summary.get("gate_use_arith", False))
        gate_use_rise = bool(self.summary.get("gate_use_rise", False))
        dqs_return = self._as_float(core.get("dqs_return_score", np.nan))
        dqs_win = self._as_float(core.get("dqs_win_score", np.nan))
        dqs_cls = self._as_float(core.get("dqs_classification_score", np.nan))
        dqs_quality = self._as_float(core.get("dqs_quality_score", np.nan))
        dqs_total = self._as_float(core.get("dqs_score", np.nan))

        pass_mean = self._as_float(self.summary.get("pass_mean_ret", np.nan))
        fail_mean = self._as_float(self.summary.get("fail_mean_ret", np.nan))
        uplift_mean = self._as_float(core.get("uplift_mean_ret", np.nan))
        pass_win = self._as_float(self.summary.get("pass_win_rate", np.nan))
        fail_win = self._as_float(self.summary.get("fail_win_rate", np.nan))
        overall_win = self._as_float(self.summary.get("overall_win_rate", np.nan))
        precision = self._as_float(core.get("precision", np.nan))
        recall = self._as_float(core.get("recall", np.nan))
        f1 = self._as_float(core.get("f1", np.nan))

        fig, axes = plt.subplots(1, 4, figsize=figsize, constrained_layout=False)

        # 1) 수익률 분리력
        ret_vals = np.array([pass_mean, fail_mean, uplift_mean], dtype=np.float64) * 100.0
        ret_labels = ["pass", "fail", "uplift"]
        ret_colors = ["#067BC2", "#F37748", "#00A878" if uplift_mean >= 0 else "#D7263D"]
        axes[0].bar(ret_labels, ret_vals, color=ret_colors, alpha=0.9)
        axes[0].axhline(0.0, color="gray", linestyle="--", linewidth=0.9)
        axes[0].set_title("Return Split")
        axes[0].set_ylabel("%")
        axes[0].grid(alpha=0.25, linestyle="--")

        # 2) 승률 분리력
        win_vals = np.array([pass_win, fail_win, overall_win], dtype=np.float64) * 100.0
        win_labels = ["pass", "fail", "overall"]
        axes[1].bar(win_labels, win_vals, color=["#067BC2", "#F37748", "#6C757D"], alpha=0.9)
        axes[1].axhline(50.0, color="gray", linestyle="--", linewidth=0.9)
        axes[1].set_title("Win Rate Split")
        axes[1].set_ylabel("%")
        axes[1].grid(alpha=0.25, linestyle="--")

        # 3) 분류 품질
        cls_vals = np.array([precision, recall, f1], dtype=np.float64) * 100.0
        cls_labels = ["precision", "recall", "f1"]
        axes[2].bar(cls_labels, cls_vals, color=["#7B6CF6", "#26A69A", "#FFB703"], alpha=0.9)
        axes[2].set_title("Classification")
        axes[2].set_ylabel("%")
        axes[2].set_ylim(bottom=0.0)
        axes[2].grid(alpha=0.25, linestyle="--")

        # 4) 분류력 통합 점수(DQS)
        dqs_vals = np.array(
            [dqs_return, dqs_win, dqs_cls, dqs_quality, dqs_total],
            dtype=np.float64,
        ) * 100.0
        dqs_labels = ["return", "win", "class", "quality", "dqs"]
        dqs_colors = ["#067BC2", "#00A878", "#7B6CF6", "#495057", "#F77F00"]
        axes[3].bar(dqs_labels, dqs_vals, color=dqs_colors, alpha=0.9)
        axes[3].axhline(70.0, color="gray", linestyle="--", linewidth=0.9)
        axes[3].set_ylim(0.0, 100.0)
        axes[3].set_title("DQS (0-100)")
        axes[3].set_ylabel("score")
        axes[3].grid(alpha=0.25, linestyle="--")

        def _fmt_pct(value: float) -> str:
            return "nan" if not np.isfinite(value) else f"{value * 100.0:.2f}%"

        def _fmt_num(value: float) -> str:
            return "nan" if not np.isfinite(value) else f"{value:.3f}"

        title = (
            f"Gate Diagnostics | samples={samples} | pass_rate={_fmt_pct(pass_rate)} | "
            f"IC={_fmt_num(ic)} | p≈{_fmt_num(p_norm)} | "
            f"min=({_fmt_num(gate_geom_min)}, {_fmt_num(gate_arith_min)}, {_fmt_pct(gate_rise_min)}) | "
            f"use=(geom={gate_use_geom},arith={gate_use_arith},rise={gate_use_rise}) | "
            f"DQS={_fmt_pct(dqs_total)}"
        )
        fig.suptitle(title, fontsize=11)
        fig.subplots_adjust(top=0.78, wspace=0.35)

        if return_handles:
            return fig, axes
        plt.show()
        return None


DB_MODE_DUCKDB = 0
DB_MODE_LEGACY = 1

_INDEX_NAME_MAP: Dict[str, str] = {
    "kospi": "코스피",
    "kosdaq": "코스닥",
    "kospi200": "코스피200",
    "ks11": "코스피",
    "kq11": "코스닥",
    "ks200": "코스피200",
}

_STOCK_TABLE_CACHE: Dict[tuple[int, tuple[tuple[str, ...] | None, bool | None, tuple[str, ...]]], StockTable] = {}
_MARKET_TABLE_CACHE: Dict[int, Dict[str, pd.DataFrame]] = {}
_CODE_NAME_SERIES_CACHE: Dict[int, pd.Series] = {}
_STOCK_FIELD_TABLE_CACHE: Dict[
    tuple[int, tuple[tuple[str, ...] | None, bool | None, tuple[str, ...]]],
    Dict[str, pd.DataFrame],
] = {}
_DB_MANAGER_CACHE: Dict[int, object] = {}


def _normalize_db_mode(db: int) -> int:
    mode = int(db)
    if mode not in {DB_MODE_DUCKDB, DB_MODE_LEGACY}:
        raise ValueError("db는 0(duckdb adjusted/index) 또는 1(legacy db/stock, db/market)만 지원합니다.")
    return mode


def _get_db_manager(db_mode: int):
    mode = _normalize_db_mode(db_mode)
    if mode in _DB_MANAGER_CACHE:
        return _DB_MANAGER_CACHE[mode]
    manager = DuckDBManager() if mode == DB_MODE_DUCKDB else ArchiveDBManager()
    _DB_MANAGER_CACHE[mode] = manager
    return manager


def _resolve_univ(univ: Univ | None) -> Univ:
    return univ if isinstance(univ, Univ) else Univ()


def _load_stock_field_table(field: str, db_mode: int, univ: Univ | None = None) -> pd.DataFrame:
    return _load_stock_field_tables([field], db_mode, univ)[str(field).strip().lower()]


def _load_stock_field_tables(fields: list[str], db_mode: int, univ: Univ | None = None) -> Dict[str, pd.DataFrame]:
    mode = _normalize_db_mode(db_mode)
    resolved_univ = _resolve_univ(univ)
    cache_key = (mode, resolved_univ.cache_key())
    cache = _STOCK_FIELD_TABLE_CACHE.setdefault(cache_key, {})
    keys: list[str] = []
    for field in fields:
        key = str(field).strip().lower()
        if key and key not in keys:
            keys.append(key)

    if not keys:
        return {}

    if mode == DB_MODE_LEGACY:
        if resolved_univ.cache_key() != Univ().cache_key():
            raise ValueError("db=1(legacy)에서는 Univ 사용자 지정이 아직 지원되지 않습니다.")
        for key in keys:
            if key in cache:
                continue
            cache[key] = _get_db_manager(mode).load_stock(field=key)
        return {key: cache[key] for key in keys}

    # duckdb adjusted-stock 모드
    field_map = {"marketcap": "market_cap"}
    missing_keys = [key for key in keys if key not in cache]
    if missing_keys:
        source_fields = [field_map.get(key, key) for key in missing_keys]
        long_df = _get_db_manager(mode).load_adjusted_stock_duckdb(
            columns=source_fields,
            market=resolved_univ.market,
            is_tradable=resolved_univ.is_tradable,
            dept_excludes=resolved_univ.dept_excludes,
        )
        for key in missing_keys:
            source_field = field_map.get(key, key)
            if source_field not in long_df.columns:
                raise ValueError(f"adjusted-stock 데이터에 '{source_field}' 컬럼이 없습니다.")
            wide = pd.to_numeric(long_df[source_field], errors="coerce").unstack("ticker")
            wide.index = pd.to_datetime(wide.index, errors="coerce")
            wide = wide[wide.index.notna()]
            wide.columns = pd.Index([str(c) for c in wide.columns], dtype="object")
            cache[key] = wide.sort_index().sort_index(axis=1)

    return {key: cache[key] for key in keys}


def _load_stock_table(db_mode: int, univ: Univ | None = None) -> StockTable:
    """
    종가 테이블을 로드해 전역 캐시에 보관한다.
    """

    mode = _normalize_db_mode(db_mode)
    resolved_univ = _resolve_univ(univ)
    cache_key = (mode, resolved_univ.cache_key())
    if cache_key in _STOCK_TABLE_CACHE:
        return _STOCK_TABLE_CACHE[cache_key]

    if mode == DB_MODE_LEGACY:
        if resolved_univ.cache_key() != Univ().cache_key():
            raise ValueError("db=1(legacy)에서는 Univ 사용자 지정이 아직 지원되지 않습니다.")
        # DB 기본 경로: db/stock/close.parquet 또는 db/stock/data/*.parquet
        df = _load_stock_field_table("close", mode, resolved_univ)
        dates = df.index.to_numpy(dtype="datetime64[ns]")
        prices = df.to_numpy(dtype=np.float64, copy=True)
        codes = [str(c) for c in df.columns]
        table = StockTable(dates=dates, prices=prices, codes=codes, code_names={})
        _STOCK_TABLE_CACHE[cache_key] = table
        return table

    # duckdb adjusted-stock 모드
    close_wide = _get_db_manager(mode).load_stock(
        field="close",
        market=resolved_univ.market,
        is_tradable=resolved_univ.is_tradable,
        dept_excludes=resolved_univ.dept_excludes,
    )
    close_wide.index = pd.to_datetime(close_wide.index, errors="coerce")
    close_wide = close_wide[close_wide.index.notna()]
    close_wide.columns = pd.Index([str(c) for c in close_wide.columns], dtype="object")
    close_wide = close_wide.sort_index().sort_index(axis=1)

    table = StockTable(
        dates=close_wide.index.to_numpy(dtype="datetime64[ns]"),
        prices=close_wide.to_numpy(dtype=np.float64, copy=True),
        codes=[str(c) for c in close_wide.columns],
        code_names={},
    )
    _STOCK_TABLE_CACHE[cache_key] = table
    return table


def _load_market_table(market: str, db_mode: int) -> pd.DataFrame:
    """
    시장 보조지표 테이블을 로드해 전역 캐시에 보관한다.
    """

    key = str(market).strip().lower()
    if not key:
        raise ValueError("market은 비어 있을 수 없습니다.")

    mode = _normalize_db_mode(db_mode)
    market_cache = _MARKET_TABLE_CACHE.setdefault(mode, {})
    if key in market_cache:
        return market_cache[key]

    if mode == DB_MODE_LEGACY:
        market_cache[key] = _get_db_manager(mode).load_market(market=key)
        return market_cache[key]

    index_name = _INDEX_NAME_MAP.get(key, str(market).strip())
    df = _get_db_manager(mode).load_index_duckdb(names=[index_name])
    if isinstance(df.index, pd.MultiIndex) and "name" in df.index.names:
        df = df.droplevel("name")
    df.index = pd.to_datetime(df.index, errors="coerce")
    df = df[df.index.notna()].sort_index()
    df.columns = [str(c).strip().lower() for c in df.columns]
    if "market_cap" in df.columns and "marketcap" not in df.columns:
        df = df.rename(columns={"market_cap": "marketcap"})
    market_cache[key] = df
    return market_cache[key]


def _load_code_name_series(db_mode: int) -> pd.Series:
    """
    종목코드-종목명 매핑 시리즈를 로드한다.
    """

    mode = _normalize_db_mode(db_mode)
    if mode in _CODE_NAME_SERIES_CACHE:
        return _CODE_NAME_SERIES_CACHE[mode]

    if mode == DB_MODE_LEGACY:
        out = _get_db_manager(mode).load_code_name()
        _CODE_NAME_SERIES_CACHE[mode] = out
        return out

    table = _load_stock_table(mode)
    if table.code_names:
        out = pd.Series(table.code_names, dtype="object")
    else:
        out = _get_db_manager(mode).load_code_name()
    _CODE_NAME_SERIES_CACHE[mode] = out
    return out


@njit(cache=True)
def _numba_accumulate_returns(
    values,
    mask,
    start_idx,
    end_idx,
    horizon_offsets,
    exit_mask,
    use_exit_mask,
    counts,
    sum_ret,
    sum_log,
    pos_counts,
    geom_invalid,
):
    """
    패턴 발생일별 horizon 수익률 통계를 누적한다.
    """

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
            target_idx = j
            if use_exit_mask:
                for k in range(i + 1, j + 1):
                    if exit_mask[k]:
                        target_idx = k
                        break
            fwd = values[target_idx]
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
    """
    구간 내 패턴 발생 횟수를 일자별로 누적한다.
    """

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
    daily_geom,
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
        daily_arith[h_idx, date_idx] = kept_sum_ret / kept_count
        daily_rise[h_idx, date_idx] = kept_pos / kept_count

        if has_geom_invalid:
            geom_invalid[h_idx, date_idx] = True
            continue

        sum_log[h_idx, date_idx] = kept_sum_log
        daily_geom[h_idx, date_idx] = np.exp(kept_sum_log / kept_count) - 1.0


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
    daily_geom,
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
            if not has_geom_invalid:
                daily_geom[h_idx, date_idx] = np.exp(kept_sum_log / kept_count) - 1.0


def _infer_pattern_label(pattern_fn: Pattern, idx: int) -> str:
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


def _infer_pattern_trim_config(pattern_fn: Pattern) -> tuple[float | None, str]:
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


def _parse_lookback_window(lookback: int | str) -> int:
    """
    lookback 입력(정수/문자열)을 거래일 수로 변환한다.
    """

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
        return value * TRADING_DAYS_PER_YEAR
    raise ValueError("지원하지 않는 lookback 단위입니다. D/W/M/Y만 사용 가능합니다.")


class Backtest:
    """
    패턴 분석, 스크리닝, 시뮬레이션 실행을 담당하는 메인 엔진.
    """

    def __init__(
        self,
        start,
        end,
        benchmark: Pattern | None = None,
        univ: Univ | None = None,
        by: str = AGG_MODE_DAY,
        db: int = 0,
    ):
        """
        백테스트 기간과 기준 패턴(옵션)을 초기화한다.
        """

        self.start = pd.Timestamp(start)
        self.end = pd.Timestamp(end)
        self.by = _normalize_analyze_by(by)
        self.db_mode = _normalize_db_mode(db)
        if univ is not None and not isinstance(univ, Univ):
            raise TypeError("univ는 Univ 객체여야 합니다.")
        self.univ = univ if isinstance(univ, Univ) else Univ()
        table = _load_stock_table(self.db_mode, self.univ)
        self.dates = table.dates
        self.prices = table.prices
        self.codes = table.codes
        self.code_names = dict(table.code_names)
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
        self._analyzed_filters: Dict[str, Filter | None] = {}
        self._last_stats_collection: StatsCollection | None = None
        self._pattern_mask_cache: Dict[tuple[str, bool], np.ndarray] = {}
        self._pattern_exit_mask_cache: Dict[str, np.ndarray] = {}
        self._pattern_exit_index_cache: Dict[tuple[str, int], np.ndarray] = {}
        self._all_stock_geom_cache: Dict[tuple[int, int], np.ndarray] = {}
        self._all_stock_metric_cache: Dict[tuple[int, int], tuple[np.ndarray, np.ndarray, np.ndarray]] = {}
        self._stock_field_matrix_cache: Dict[str, np.ndarray] = {}
        self._regime_frame_cache: Dict[str, pd.DataFrame] = {}
        self._vwap_matrix: np.ndarray | None = None
        if benchmark is not None:
            base_name = _infer_pattern_label(benchmark, 0)
            base_trim_q, base_trim_method = _infer_pattern_trim_config(benchmark)
            self._base_stats[base_name] = self._run_pattern(
                benchmark,
                trim_quantile=base_trim_q,
                trim_method=base_trim_method,
                progress_label=base_name,
                aggregation_mode=self.by,
                filter_obj=None,
            )
            self._analyzed_patterns[base_name] = benchmark
            self._analyzed_stats[base_name] = self._base_stats[base_name]
            self._analyzed_filters[base_name] = None

    @staticmethod
    def _compute_mask(pattern_fn: Pattern, values: np.ndarray, code: str) -> np.ndarray | None:
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
    def _is_default_price_pattern(pattern_fn: Pattern) -> bool:
        """
        종가가 유효한 모든 구간을 그대로 선택하는 기본 패턴인지 판별한다.
        """

        return (
            type(pattern_fn) is Pattern
            and pattern_fn.market_name is None
            and pattern_fn._post_mask_fn is Pattern._post_mask_base
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
            df = _load_market_table(key[0], self.db_mode)
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
        """
        결합 패턴 트리를 순회하며 하위 Pattern 노드를 반환한다.
        """

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
            pattern = getattr(node, "pattern", None)
            if isinstance(left, Pattern):
                stack.append(left)
            if isinstance(right, Pattern):
                stack.append(right)
            if isinstance(pattern, Pattern):
                stack.append(pattern)

    def _iter_attached_regimes(self, pattern_fn: Pattern):
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

    def _resolve_regime_breadth_univ(self) -> Univ:
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

        idx = pd.DatetimeIndex(self.dates)
        breadth_univ = self._resolve_regime_breadth_univ()
        stock_tables = _load_stock_field_tables(["close", "amount", "marketcap"], self.db_mode, breadth_univ)
        close_df = stock_tables["close"].reindex(index=idx)
        amount_df = stock_tables["amount"].reindex(index=idx, columns=close_df.columns)
        marketcap_df = stock_tables["marketcap"].reindex(index=idx, columns=close_df.columns)
        market_df = _load_market_table(market_key, self.db_mode)
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
        self._regime_frame_cache[market_key] = frame
        return frame

    def _prepare_regime_sources(self, pattern_fn: Pattern) -> None:
        """
        패턴에 연결된 Regime 객체에 날짜별 허용 마스크를 주입한다.
        """

        for regime in self._iter_attached_regimes(pattern_fn):
            frame = self._get_regime_frame(regime)
            mask_values = regime_mask_from_frame(frame, regime.kind)
            regime._bind(self.dates, mask_values, frame)

    def _prepare_market_sources(self, pattern_fn: Pattern) -> None:
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
            df = _load_stock_field_table(key, self.db_mode, self.univ)
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
        marketcap_df = _load_stock_field_table("marketcap", self.db_mode, self.univ).reindex(index=idx, columns=cols)
        size_masks = build_market_cap_bucket_masks(marketcap_df)
        cache["size_bucket_large"] = size_masks["large"].to_numpy(dtype=np.bool_, copy=True)
        cache["size_bucket_mid"] = size_masks["mid"].to_numpy(dtype=np.bool_, copy=True)
        cache["size_bucket_small"] = size_masks["small"].to_numpy(dtype=np.bool_, copy=True)

    def _prepare_stock_sources(self, pattern_fn: Pattern, col_idx: int) -> None:
        """
        종목별 보조 필드(high/low/volume 등)가 필요한 패턴 노드에 시계열을 주입한다.
        """

        for node in self._iter_pattern_nodes(pattern_fn):
            fields = tuple(node._required_stock_fields())
            for field in fields:
                matrix = self._get_stock_field_matrix(field)
                node._set_stock_values(field, matrix[:, col_idx])

    def _run_pattern_normal(self, pattern_fn: Pattern, progress_label: str) -> Stats:
        """
        trim 없이 이벤트 기반 통계를 계산한다.
        """

        stats = Stats.create(self.dates, HORIZONS)
        stats.eval_start_idx = self.start_idx
        stats.eval_end_idx = self.end_idx
        exit_mask_matrix = self._build_exit_mask_matrix(pattern_fn, len(self.dates))
        use_exit_mask = bool(pattern_fn.has_exit_rule())
        for col_idx, code in enumerate(_progress(self.codes, desc=progress_label)):
            values = self.prices[:, col_idx]
            self._prepare_stock_sources(pattern_fn, col_idx)
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
                exit_mask_matrix[:, col_idx],
                use_exit_mask,
                stats.counts,
                stats.sum_ret,
                stats.sum_log,
                stats.pos_counts,
                stats.geom_invalid,
            )
        return stats

    def _build_mask_matrix(
        self,
        pattern_fn: Pattern,
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
            return mask_matrix

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
        return mask_matrix

    def _build_exit_mask_matrix(
        self,
        pattern_fn: Pattern,
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
        pattern_fn: Pattern,
    ) -> np.ndarray:
        """
        전체 기간 청산 마스크를 생성/캐시한다.
        """

        if pattern_name in self._pattern_exit_mask_cache:
            return self._pattern_exit_mask_cache[pattern_name]
        exit_mask_matrix = self._build_exit_mask_matrix(pattern_fn, len(self.dates))
        self._pattern_exit_mask_cache[pattern_name] = exit_mask_matrix
        return exit_mask_matrix

    def _build_dynamic_exit_index_matrix(
        self,
        pattern_fn: Pattern,
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
        pattern_fn: Pattern,
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
        daily_geom = stats.daily_geom
        daily_rise = stats.daily_rise
        if daily_arith is None or daily_geom is None or daily_rise is None:
            raise ValueError("trim 모드에서는 daily 통계 버퍼가 필요합니다.")

        iterator = range(mask_matrix.shape[0])
        if progress_bar is None:
            iterator = _progress(iterator, desc=f"{progress_label} | trim")

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
                daily_geom,
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
        daily_geom: np.ndarray,
        daily_rise: np.ndarray,
        progress_bar=None,
    ) -> None:
        """
        외부 daily 버퍼를 사용해 날짜별 trim 집계를 누적한다.
        """

        iterator = range(mask_matrix.shape[0])
        if progress_bar is None:
            iterator = _progress(iterator, desc=f"{progress_label} | trim")

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
                daily_geom,
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
            daily_geom = stats.daily_geom
            daily_rise = stats.daily_rise
            if daily_arith is None or daily_geom is None or daily_rise is None:
                raise ValueError("daily 집계에는 daily 통계 버퍼가 필요합니다.")
        else:
            daily_arith = np.full((1, 1), np.nan, dtype=np.float64)
            daily_geom = np.full((1, 1), np.nan, dtype=np.float64)
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
                daily_geom,
                daily_rise,
                write_daily,
            )
            if progress_bar is not None:
                progress_bar.update(1)

    def _run_pattern_trim(
        self,
        pattern_fn: Pattern,
        trim_q: float,
        trim_method: str,
        progress_label: str,
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
        if eval_len > 0:
            stats.occurrence_counts[self.start_idx:self.end_idx] = np.sum(
                mask_matrix,
                axis=1,
                dtype=np.int64,
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
        pattern_fn: Pattern,
        trim_quantile: float | None,
        trim_method: str,
        progress_label: str,
        aggregation_mode: str,
        filter_obj: Filter,
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

            if agg_mode == AGG_MODE_DAY:
                stats = Stats.create_daily(self.dates, HORIZONS)
                stats.eval_start_idx = self.start_idx
                stats.eval_end_idx = self.end_idx
                if eval_len > 0:
                    stats.occurrence_counts[self.start_idx:self.end_idx] = np.sum(
                        mask_matrix,
                        axis=1,
                        dtype=np.int64,
                    )
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
            if eval_len > 0:
                stats.occurrence_counts[self.start_idx:self.end_idx] = np.sum(
                    mask_matrix,
                    axis=1,
                    dtype=np.int64,
                )

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
            tmp_daily_geom = np.full((num_h, num_dates), np.nan, dtype=np.float64)
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
                tmp_daily_geom,
                tmp_daily_rise,
                progress_bar=progress_bar,
            )
            return stats
        finally:
            if progress_bar is not None:
                progress_bar.close()

    def _run_pattern_dynamic(
        self,
        pattern_fn: Pattern,
        trim_quantile: float | None,
        trim_method: str,
        progress_label: str,
        aggregation_mode: str,
        filter_obj: Filter | None,
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
            if progress_bar is not None:
                progress_bar.update(1)
                progress_bar.set_description_str(progress_label)

            if agg_mode == AGG_MODE_DAY:
                stats = Stats.create_daily(self.dates, HORIZONS)
                stats.eval_start_idx = self.start_idx
                stats.eval_end_idx = self.end_idx
                if eval_len > 0:
                    stats.occurrence_counts[self.start_idx:self.end_idx] = np.sum(
                        mask_matrix,
                        axis=1,
                        dtype=np.int64,
                    )
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
            if eval_len > 0:
                stats.occurrence_counts[self.start_idx:self.end_idx] = np.sum(
                    mask_matrix,
                    axis=1,
                    dtype=np.int64,
                )

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
            tmp_daily_geom = np.full((num_h, num_dates), np.nan, dtype=np.float64)
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
                tmp_daily_geom,
                tmp_daily_rise,
                progress_bar=progress_bar,
            )
            return stats
        finally:
            if progress_bar is not None:
                progress_bar.close()

    def _run_pattern(
        self,
        pattern_fn: Pattern,
        trim_quantile: float | None = None,
        trim_method: str = "remove",
        progress_label: str = "pattern",
        aggregation_mode: str = AGG_MODE_EVENT,
        filter_obj: Filter | None = None,
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
            )
        if filter_obj is not None and filter_obj.is_active:
            return self._run_pattern_filtered(
                pattern_fn,
                trim_quantile=trim_q,
                trim_method=trim_method_text,
                progress_label=progress_label,
                aggregation_mode=agg_mode,
                filter_obj=filter_obj,
            )

        if agg_mode == AGG_MODE_EVENT:
            if trim_q is None or trim_q <= 0.0:
                return self._run_pattern_normal(pattern_fn, progress_label)
            return self._run_pattern_trim(pattern_fn, trim_q, trim_method_text, progress_label)

        # day_mean 모드: trim 미설정(None)이어도 일자균등 평균을 계산한다.
        daily_trim_q = 0.0 if trim_q is None else float(trim_q)
        return self._run_pattern_trim(pattern_fn, daily_trim_q, trim_method_text, progress_label)

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
        pattern: Pattern,
    ) -> tuple[str, Pattern, bool]:
        """
        스크리닝용 패턴 이름과 캐시 사용 가능 여부를 결정한다.
        """

        if not isinstance(pattern, Pattern):
            raise TypeError("screen()의 pattern은 Pattern 객체여야 합니다.")

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

        code_name = _load_code_name_series(self.db_mode)
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

    def _prepare_filter(self, filter_obj: Filter | None, show_progress: bool) -> Filter | None:
        """
        analyze(filter=...)로 전달된 Filter를 현재 Backtest 축에 바인딩하고 준비한다.
        """

        if filter_obj is None:
            return None
        if not isinstance(filter_obj, Filter):
            raise TypeError("filter는 Filter 객체여야 합니다.")
        if not filter_obj.is_active:
            return None
        filter_obj.bind(
            dates=self.dates,
            codes=self.codes,
            prices=self.prices,
            db_mode=self.db_mode,
            univ=self.univ,
        )
        filter_obj.prepare(show_progress=show_progress)
        return filter_obj

    def _build_pattern_mask_matrix(
        self,
        pattern_name: str,
        pattern_fn: Pattern,
        filter_obj: Filter | None = None,
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

    def _all_stock_history_metrics(
        self,
        horizon_days: int,
        lookback_window: int,
    ) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        """
        전체 종목 기준 horizon 산술/기하/상승확률 히스토리를 계산한다.
        """

        cache_key = (int(horizon_days), int(lookback_window))
        if cache_key in self._all_stock_metric_cache:
            return self._all_stock_metric_cache[cache_key]

        prices = self.prices
        num_dates, _ = prices.shape
        counts = np.zeros(num_dates, dtype=np.float64)
        sum_ret = np.zeros(num_dates, dtype=np.float64)
        sum_log = np.zeros(num_dates, dtype=np.float64)
        pos_counts = np.zeros(num_dates, dtype=np.float64)
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
            sum_ret[i] = float(ret.sum())
            pos_counts[i] = float(np.sum(ret > 0.0))
            if np.any(ret <= -1.0):
                invalid[i] = True
            else:
                sum_log[i] = float(np.log1p(ret).sum())

        window = int(max(1, lookback_window))
        roll_counts = (
            pd.Series(counts).rolling(window=window, min_periods=1).sum().to_numpy(dtype=np.float64)
        )
        roll_sum_ret = (
            pd.Series(sum_ret).rolling(window=window, min_periods=1).sum().to_numpy(dtype=np.float64)
        )
        roll_sum_log = (
            pd.Series(sum_log).rolling(window=window, min_periods=1).sum().to_numpy(dtype=np.float64)
        )
        roll_pos = (
            pd.Series(pos_counts).rolling(window=window, min_periods=1).sum().to_numpy(dtype=np.float64)
        )
        roll_invalid = (
            pd.Series(invalid.astype(np.float64))
            .rolling(window=window, min_periods=1)
            .sum()
            .to_numpy(dtype=np.float64)
            > 0.0
        )

        arith_base = np.full(num_dates, np.nan, dtype=np.float64)
        geom_base = np.full(num_dates, np.nan, dtype=np.float64)
        rise_base = np.full(num_dates, np.nan, dtype=np.float64)

        valid = roll_counts > 0.0
        arith_base[valid] = roll_sum_ret[valid] / roll_counts[valid]
        rise_base[valid] = roll_pos[valid] / roll_counts[valid]
        valid_geom = valid & (~roll_invalid)
        geom_base[valid_geom] = np.exp(roll_sum_log[valid_geom] / roll_counts[valid_geom]) - 1.0

        support = np.arange(num_dates) >= (window - 1)
        arith_base[~support] = np.nan
        geom_base[~support] = np.nan
        rise_base[~support] = np.nan

        def _asof_shift(series: np.ndarray) -> np.ndarray:
            shifted = np.full(num_dates, np.nan, dtype=np.float64)
            if horizon_days > 0:
                if horizon_days < num_dates:
                    shifted[horizon_days:] = series[:-horizon_days]
            else:
                shifted[:] = series
            return shifted

        arith_asof = _asof_shift(arith_base)
        geom_asof = _asof_shift(geom_base)
        rise_asof = _asof_shift(rise_base)
        self._all_stock_metric_cache[cache_key] = (arith_asof, geom_asof, rise_asof)
        return arith_asof, geom_asof, rise_asof

    def _all_stock_geom_history(self, horizon_days: int, lookback_window: int) -> np.ndarray:
        """
        전체 종목 기준 horizon 기하평균 수익률 히스토리를 계산한다.
        """
        cache_key = (int(horizon_days), int(lookback_window))
        if cache_key in self._all_stock_geom_cache:
            return self._all_stock_geom_cache[cache_key]
        _, geom_asof, _ = self._all_stock_history_metrics(horizon_days, lookback_window)
        self._all_stock_geom_cache[cache_key] = geom_asof
        return geom_asof

    def _get_vwap_matrix(self) -> np.ndarray:
        """
        매매 체결/평가용 VWAP(= (open+high+low+close)/4) 매트릭스를 반환한다.
        """

        if self._vwap_matrix is None:
            idx = pd.DatetimeIndex(self.dates)
            cols = pd.Index(self.codes, dtype="object")

            open_df = _load_stock_field_table("open", self.db_mode, self.univ).reindex(index=idx, columns=cols)
            high_df = _load_stock_field_table("high", self.db_mode, self.univ).reindex(index=idx, columns=cols)
            low_df = _load_stock_field_table("low", self.db_mode, self.univ).reindex(index=idx, columns=cols)
            close_df = _load_stock_field_table("close", self.db_mode, self.univ).reindex(index=idx, columns=cols)
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
        pattern: Pattern,
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

    def _pattern_filter(self, pattern: str) -> Filter | None:
        """
        analyze 결과 패턴명에 연결된 Filter를 반환한다.
        """

        return self._analyzed_filters.get(pattern)

    @staticmethod
    def _gate_full_cohort(
        pattern_arith: float,
        market_arith: float,
        pattern_geom: float,
        market_geom: float,
        pattern_rise: float,
        market_rise: float,
        *,
        gate_geom_min: float,
        gate_arith_min: float,
        gate_rise_min: float,
        gate_use_geom: bool,
        gate_use_arith: bool,
        gate_use_rise: bool,
    ) -> bool:
        """
        run과 동일한 게이트 판정식을 반환한다.
        """

        has_metrics = (
            np.isfinite(pattern_arith)
            and np.isfinite(market_arith)
            and np.isfinite(pattern_geom)
            and np.isfinite(market_geom)
            and np.isfinite(pattern_rise)
            and np.isfinite(market_rise)
        )
        geom_pass = pattern_geom > max(gate_geom_min, market_geom)
        arith_pass = pattern_arith > max(gate_arith_min, market_arith)
        rise_pass = pattern_rise > max(gate_rise_min, market_rise)
        return bool(
            has_metrics
            and ((not gate_use_geom) or geom_pass)
            and ((not gate_use_arith) or arith_pass)
            and ((not gate_use_rise) or rise_pass)
        )

    @staticmethod
    def _welch_like_t_and_p(x: np.ndarray, y: np.ndarray) -> tuple[float, float]:
        """
        두 집단 평균 차이의 t-유사 통계량과 정규근사 p값을 반환한다.
        """

        if x.size < 2 or y.size < 2:
            return float("nan"), float("nan")
        vx = float(np.var(x, ddof=1))
        vy = float(np.var(y, ddof=1))
        se = math.sqrt(vx / float(x.size) + vy / float(y.size))
        if not np.isfinite(se) or se <= 0.0:
            return float("nan"), float("nan")
        t_stat = (float(np.mean(x)) - float(np.mean(y))) / se
        p_two = math.erfc(abs(t_stat) / math.sqrt(2.0))
        return float(t_stat), float(p_two)

    @staticmethod
    def _clip01(value: float) -> float:
        """
        값을 [0, 1] 구간으로 클리핑한다. 비유한 값은 0으로 처리한다.
        """

        if not np.isfinite(value):
            return 0.0
        return float(min(1.0, max(0.0, value)))

    @staticmethod
    def _sigmoid_score(value: float, scale: float) -> float:
        """
        입력값을 시그모이드로 0~1 점수로 변환한다.
        """

        if not np.isfinite(value):
            return 0.0
        if not np.isfinite(scale) or scale <= 0.0:
            raise ValueError("scale은 양수 유한값이어야 합니다.")
        z = float(value) / float(scale)
        if z >= 60.0:
            return 1.0
        if z <= -60.0:
            return 0.0
        return 1.0 / (1.0 + math.exp(-z))

    @classmethod
    def _compute_dqs_components(
        cls,
        *,
        uplift_ret: float,
        uplift_win: float,
        precision: float,
        recall: float,
        samples: int,
        pass_rate: float,
        p_norm: float,
    ) -> dict[str, float]:
        """
        분류력 통합점수(DQS)와 구성요소 점수를 계산한다.
        """

        ret_score = cls._sigmoid_score(uplift_ret, 0.01)
        win_score = cls._sigmoid_score(uplift_win, 0.05)

        if np.isfinite(precision) and np.isfinite(recall):
            cls_score = math.sqrt(max(0.0, precision) * max(0.0, recall))
        else:
            cls_score = 0.0
        cls_score = cls._clip01(cls_score)

        sample_factor = 0.0
        if np.isfinite(samples) and samples > 0:
            sample_factor = min(1.0, math.log1p(float(samples)) / math.log1p(1500.0))

        if np.isfinite(p_norm):
            z = (float(p_norm) - 0.05) / 0.02
            if z >= 60.0:
                pvalue_factor = 0.0
            elif z <= -60.0:
                pvalue_factor = 1.0
            else:
                pvalue_factor = 1.0 / (1.0 + math.exp(z))
        else:
            pvalue_factor = 0.0

        if np.isfinite(pass_rate):
            passrate_balance = 1.0 - abs(float(pass_rate) - 0.5) / 0.5
        else:
            passrate_balance = 0.0
        passrate_balance = cls._clip01(passrate_balance)

        quality_score = cls._clip01(sample_factor * pvalue_factor * passrate_balance)
        dqs_score = (
            (ret_score ** 0.35)
            * (win_score ** 0.25)
            * (cls_score ** 0.30)
            * (quality_score ** 0.10)
        )
        dqs_score = cls._clip01(dqs_score)

        return {
            "dqs_return_score": float(ret_score),
            "dqs_win_score": float(win_score),
            "dqs_classification_score": float(cls_score),
            "dqs_quality_score": float(quality_score),
            "dqs_score": float(dqs_score),
        }

    def run(
        self,
        start=None,
        end=None,
        pattern: str = "",
        target_horizon: str | int = "1M",
        aggregate_lookback: int | str = TRADING_DAYS_PER_YEAR,
        trade_price_mode: str = "익일VWAP",
        fallback_exposure: float = 0.5,
        gate_geom_min: float = 0.0,
        gate_arith_min: float = 0.0,
        gate_rise_min: float = 0.5,
        gate_use_geom: bool = False,
        gate_use_arith: bool = False,
        gate_use_rise: bool = False,
        stop_loss_pct: float | None = None,
        take_profit_pct: float | None = None,
        allow_reentry: bool = True,
        min_cohort_size: int = 1,
    ) -> Simulator:
        """
        분석된 패턴 통계를 기반으로 포트폴리오 시뮬레이션을 실행한다.
        """
        # 1) 입력 파라미터를 내부 인덱스/거래일 단위로 정규화
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

        # 2) 패턴/시장 통계 시계열 준비
        pattern_fn = self._analyzed_patterns[pattern]
        pattern_stats = self._analyzed_stats[pattern]
        pattern_filter = self._pattern_filter(pattern)
        pattern_mask = self._build_pattern_mask_matrix(
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

        pattern_hist = pattern_stats.to_frame_history(
            horizon=horizon_label,
            start=None,
            end=None,
            history_window=lookback_window,
            min_count=1,
            require_full_window=True,
        )
        pattern_arith_series = (
            pattern_hist["arith_mean"].reindex(pd.DatetimeIndex(self.dates)).to_numpy(dtype=np.float64)
        )
        pattern_geom_series = (
            pattern_hist["geom_mean"].reindex(pd.DatetimeIndex(self.dates)).to_numpy(dtype=np.float64)
        )
        pattern_rise_series = (
            pattern_hist["rise_prob"].reindex(pd.DatetimeIndex(self.dates)).to_numpy(dtype=np.float64)
        )
        (
            all_stock_arith_series,
            all_stock_geom_series,
            all_stock_rise_series,
        ) = self._all_stock_history_metrics(horizon_days, lookback_window)
        if self.code_names:
            code_names = dict(self.code_names)
        else:
            code_name_series = _load_code_name_series(self.db_mode)
            code_names = {
                str(code): str(name).strip()
                for code, name in code_name_series.items()
                if pd.notna(name) and str(name).strip()
            }

        # 3) Simulator로 주문/보유/청산 루프 실행
        trade_prices, execution_lag_days, execution_price_mode = self._resolve_trade_price_mode(
            trade_price_mode
        )
        simulator = Simulator(
            dates=self.dates,
            prices=trade_prices,
            codes=self.codes,
            code_names=code_names,
        )
        return simulator.run(
            start_idx=start_idx,
            end_idx=end_idx,
            pattern=pattern,
            target_horizon=horizon_label,
            target_horizon_days=horizon_days,
            aggregate_lookback=aggregate_lookback,
            pattern_mask=pattern_mask,
            pattern_exit_mask=pattern_exit_mask,
            pattern_dynamic_exit_index=pattern_dynamic_exit_index,
            pattern_arith_series=pattern_arith_series,
            pattern_geom_series=pattern_geom_series,
            pattern_rise_series=pattern_rise_series,
            all_stock_arith_series=all_stock_arith_series,
            all_stock_geom_series=all_stock_geom_series,
            all_stock_rise_series=all_stock_rise_series,
            fallback_exposure=fallback_exposure,
            gate_geom_min=gate_geom_min,
            gate_arith_min=gate_arith_min,
            gate_rise_min=gate_rise_min,
            gate_use_geom=gate_use_geom,
            gate_use_arith=gate_use_arith,
            gate_use_rise=gate_use_rise,
            stop_loss_pct=stop_loss_pct,
            take_profit_pct=take_profit_pct,
            execution_lag_days=execution_lag_days,
            execution_price_mode=execution_price_mode,
            allow_reentry=allow_reentry,
            min_cohort_size=min_cohort_size,
        )

    def diagnose_gate(
        self,
        *,
        pattern: str,
        start=None,
        end=None,
        target_horizon: str | int = "1M",
        aggregate_lookback: int | str = TRADING_DAYS_PER_YEAR,
        trade_price_mode: str = "익일VWAP",
        gate_geom_min: float = 0.0,
        gate_arith_min: float = 0.0,
        gate_rise_min: float = 0.5,
        gate_use_geom: bool = False,
        gate_use_arith: bool = False,
        gate_use_rise: bool = False,
        min_cohort_size: int = 1,
    ) -> GateDiagnostics:
        """
        게이트가 cohort 수익률 양/음을 얼마나 구분하는지(분류력) 진단한다.
        """

        if pattern not in self._analyzed_patterns or pattern not in self._analyzed_stats:
            available = sorted(self._analyzed_patterns.keys())
            raise ValueError(
                f"analyze() 결과에서 pattern '{pattern}'을 찾을 수 없습니다. "
                f"사용 가능: {available}"
            )
        min_cohort_size_value = int(min_cohort_size)
        if min_cohort_size_value <= 0:
            raise ValueError("min_cohort_size는 1 이상의 정수여야 합니다.")

        gate_geom_min_value = Simulator._normalize_return_gate_min(
            gate_geom_min,
            "gate_geom_min",
        )
        gate_arith_min_value = Simulator._normalize_return_gate_min(
            gate_arith_min,
            "gate_arith_min",
        )
        gate_rise_min_value = Simulator._normalize_rise_gate_min(gate_rise_min)
        gate_use_geom_value = bool(gate_use_geom)
        gate_use_arith_value = bool(gate_use_arith)
        gate_use_rise_value = bool(gate_use_rise)
        if not (gate_use_geom_value or gate_use_arith_value or gate_use_rise_value):
            raise ValueError(
                "게이트 지표를 모두 비활성화할 수 없습니다. "
                "gate_use_geom/gate_use_arith/gate_use_rise 중 최소 1개는 True여야 합니다."
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
        pattern_filter = self._pattern_filter(pattern)
        pattern_mask = self._build_pattern_mask_matrix(
            pattern,
            pattern_fn,
            filter_obj=pattern_filter,
        )
        trade_prices, lag_days, _ = self._resolve_trade_price_mode(trade_price_mode)

        pattern_hist = pattern_stats.to_frame_history(
            horizon=horizon_label,
            start=None,
            end=None,
            history_window=lookback_window,
            min_count=1,
            require_full_window=True,
        )
        pattern_arith_series = (
            pattern_hist["arith_mean"].reindex(pd.DatetimeIndex(self.dates)).to_numpy(dtype=np.float64)
        )
        pattern_geom_series = (
            pattern_hist["geom_mean"].reindex(pd.DatetimeIndex(self.dates)).to_numpy(dtype=np.float64)
        )
        pattern_rise_series = (
            pattern_hist["rise_prob"].reindex(pd.DatetimeIndex(self.dates)).to_numpy(dtype=np.float64)
        )
        (
            all_stock_arith_series,
            all_stock_geom_series,
            all_stock_rise_series,
        ) = self._all_stock_history_metrics(horizon_days, lookback_window)

        rows: list[dict[str, object]] = []
        for t in range(start_idx, end_idx - 1):
            signal_idx = t if lag_days == 1 else (t + 1)
            entry_idx = t + 1
            exit_idx = entry_idx + horizon_days
            if signal_idx >= len(self.dates) or entry_idx >= len(self.dates):
                continue
            # 수익 실현이 확인되는 cohort만 샘플로 사용한다(검열 제거).
            if exit_idx >= len(self.dates) or exit_idx >= end_idx:
                continue

            selected = np.where(pattern_mask[signal_idx])[0]
            if selected.size < min_cohort_size_value:
                continue

            entry_px = trade_prices[entry_idx, selected]
            exit_px = trade_prices[exit_idx, selected]
            valid_px = (
                np.isfinite(entry_px)
                & np.isfinite(exit_px)
                & (entry_px > 0.0)
                & (exit_px > 0.0)
            )
            if int(np.sum(valid_px)) < min_cohort_size_value:
                continue
            cohort_ret = float(np.mean(exit_px[valid_px] / entry_px[valid_px] - 1.0))

            pattern_arith = float(pattern_arith_series[signal_idx])
            pattern_geom = float(pattern_geom_series[signal_idx])
            pattern_rise = float(pattern_rise_series[signal_idx])
            market_arith = float(all_stock_arith_series[signal_idx])
            market_geom = float(all_stock_geom_series[signal_idx])
            market_rise = float(all_stock_rise_series[signal_idx])
            gate_pass = self._gate_full_cohort(
                pattern_arith=pattern_arith,
                market_arith=market_arith,
                pattern_geom=pattern_geom,
                market_geom=market_geom,
                pattern_rise=pattern_rise,
                market_rise=market_rise,
                gate_geom_min=gate_geom_min_value,
                gate_arith_min=gate_arith_min_value,
                gate_rise_min=gate_rise_min_value,
                gate_use_geom=gate_use_geom_value,
                gate_use_arith=gate_use_arith_value,
                gate_use_rise=gate_use_rise_value,
            )

            rows.append(
                {
                    "signal_date": pd.Timestamp(self.dates[signal_idx]),
                    "entry_date": pd.Timestamp(self.dates[entry_idx]),
                    "exit_date": pd.Timestamp(self.dates[exit_idx]),
                    "selected_count": int(selected.size),
                    "valid_count": int(np.sum(valid_px)),
                    "cohort_ret": cohort_ret,
                    "ret_positive": bool(cohort_ret > 0.0),
                    "gate_pass": bool(gate_pass),
                    "pattern_geom": pattern_geom,
                    "pattern_arith": pattern_arith,
                    "pattern_rise": pattern_rise,
                    "market_geom": market_geom,
                    "market_arith": market_arith,
                    "market_rise": market_rise,
                }
            )

        samples = pd.DataFrame(rows)
        if samples.empty:
            raise ValueError("gate 진단에 사용할 샘플이 없습니다. 기간/필터/패턴을 확인하세요.")
        samples = samples.sort_values("signal_date", kind="stable").set_index("signal_date")

        ret = samples["cohort_ret"].to_numpy(dtype=np.float64)
        gate = samples["gate_pass"].to_numpy(dtype=np.bool_)
        pos = ret > 0.0
        pass_ret = ret[gate]
        fail_ret = ret[~gate]

        n_total = int(ret.size)
        n_pass = int(np.sum(gate))
        n_fail = int(np.sum(~gate))
        n_pos = int(np.sum(pos))
        n_neg = int(np.sum(~pos))
        tp = int(np.sum(gate & pos))
        fp = int(np.sum(gate & (~pos)))
        fn = int(np.sum((~gate) & pos))
        tn = int(np.sum((~gate) & (~pos)))

        pass_mean = float(np.mean(pass_ret)) if pass_ret.size > 0 else float("nan")
        fail_mean = float(np.mean(fail_ret)) if fail_ret.size > 0 else float("nan")
        pass_win = float(np.mean(pass_ret > 0.0)) if pass_ret.size > 0 else float("nan")
        fail_win = float(np.mean(fail_ret > 0.0)) if fail_ret.size > 0 else float("nan")
        overall_win = float(np.mean(pos)) if ret.size > 0 else float("nan")
        uplift_ret = pass_mean - fail_mean if np.isfinite(pass_mean) and np.isfinite(fail_mean) else float("nan")
        uplift_win = pass_win - fail_win if np.isfinite(pass_win) and np.isfinite(fail_win) else float("nan")

        precision = float(tp / (tp + fp)) if (tp + fp) > 0 else float("nan")
        recall = float(tp / (tp + fn)) if (tp + fn) > 0 else float("nan")
        specificity = float(tn / (tn + fp)) if (tn + fp) > 0 else float("nan")
        if np.isfinite(precision) and np.isfinite(recall) and (precision + recall) > 0.0:
            f1 = 2.0 * precision * recall / (precision + recall)
        else:
            f1 = float("nan")

        ic = float("nan")
        gate_float = gate.astype(np.float64)
        if gate_float.size >= 2 and np.nanstd(gate_float) > 0.0 and np.nanstd(ret) > 0.0:
            ic = float(np.corrcoef(gate_float, ret)[0, 1])

        t_stat, p_norm = self._welch_like_t_and_p(pass_ret, fail_ret)
        dqs = self._compute_dqs_components(
            uplift_ret=uplift_ret,
            uplift_win=uplift_win,
            precision=precision,
            recall=recall,
            samples=n_total,
            pass_rate=float(n_pass / n_total) if n_total > 0 else float("nan"),
            p_norm=p_norm,
        )
        summary = pd.Series(
            {
                "samples": float(n_total),
                "pass_count": float(n_pass),
                "fail_count": float(n_fail),
                "pass_rate": float(n_pass / n_total) if n_total > 0 else float("nan"),
                "positive_count": float(n_pos),
                "negative_count": float(n_neg),
                "pass_mean_ret": pass_mean,
                "fail_mean_ret": fail_mean,
                "uplift_mean_ret": uplift_ret,
                "pass_win_rate": pass_win,
                "fail_win_rate": fail_win,
                "overall_win_rate": overall_win,
                "uplift_win_rate": uplift_win,
                "precision": precision,
                "recall": recall,
                "specificity": specificity,
                "f1": f1,
                "ic_gate_vs_ret": ic,
                "t_stat_pass_minus_fail": t_stat,
                "p_norm_pass_minus_fail": p_norm,
                "target_horizon_days": float(horizon_days),
                "aggregate_lookback": float(lookback_window),
                "gate_geom_min": float(gate_geom_min_value),
                "gate_arith_min": float(gate_arith_min_value),
                "gate_rise_min": float(gate_rise_min_value),
                "gate_use_geom": bool(gate_use_geom_value),
                "gate_use_arith": bool(gate_use_arith_value),
                "gate_use_rise": bool(gate_use_rise_value),
                "dqs_return_score": dqs["dqs_return_score"],
                "dqs_win_score": dqs["dqs_win_score"],
                "dqs_classification_score": dqs["dqs_classification_score"],
                "dqs_quality_score": dqs["dqs_quality_score"],
                "dqs_score": dqs["dqs_score"],
            },
            name=f"gate_diagnostics:{pattern}",
            dtype="object",
        )
        return GateDiagnostics(summary=summary, samples=samples)

    def analyze(
        self,
        *patterns: Pattern,
        include_base: bool = True,
        filter: Filter | None = None,
    ) -> StatsCollection:
        """
        패턴들을 평가해 StatsCollection 결과를 생성한다.
        """

        aggregation_mode = self.by
        analyze_filter = self._prepare_filter(filter, show_progress=True)

        stats_map: Dict[str, Stats] = {}
        benchmark_names: set[str] = set()
        if include_base and self.benchmark is not None:
            stats_map.update(self._base_stats)
            benchmark_names.update(self._base_stats.keys())

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
                aggregation_mode=aggregation_mode,
                filter_obj=analyze_filter,
            )
            name = base_name
            suffix = 2
            while name in stats_map:
                name = f"{base_name}_{suffix}"
                suffix += 1
            stats_map[name] = stats
            self._analyzed_patterns[name] = pattern_fn
            self._analyzed_stats[name] = stats
            self._analyzed_filters[name] = analyze_filter
            self._pattern_mask_cache.pop((name, False), None)
            self._pattern_mask_cache.pop((name, True), None)
            self._pattern_exit_mask_cache.pop(name, None)
            for cache_key in list(self._pattern_exit_index_cache):
                if cache_key[0] == name:
                    self._pattern_exit_index_cache.pop(cache_key, None)

        if not stats_map:
            raise ValueError("실행된 패턴이 없습니다.")
        result = StatsCollection(stats_map, benchmark_names=benchmark_names)
        self._last_stats_collection = result
        return result
