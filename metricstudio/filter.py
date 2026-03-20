"""Runtime stock filters used by Backtest.analyze and screen flows."""

from __future__ import annotations

from typing import Callable
import re

import numpy as np
import pandas as pd

from metricstudio._progress import progress as _progress
from metricstudio.dataload import DataLoader
from metricstudio.univ import Univ


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
        self._data_loader: DataLoader | None = None
        self._univ: Univ | None = None
        self._marketcap_matrix: np.ndarray | None = None
        self._liquidity_matrix: np.ndarray | None = None
        self._mask_matrix: np.ndarray | None = None

    @property
    def is_active(self) -> bool:
        """
        실제 필터링 조건이 하나라도 지정됐는지 반환한다.
        """

        return self.market_cap_buckets is not None or self.liquidity_buckets is not None

    def bind(
        self,
        *,
        dates: np.ndarray,
        codes: list[str],
        prices: np.ndarray,
        data_loader: DataLoader,
        univ: Univ,
    ) -> None:
        """
        날짜/종목 축과 로더 컨텍스트를 묶어 이후 필터 계산에 사용한다.
        """

        self._dates = dates
        self._codes = codes
        self._prices = prices
        self._data_loader = data_loader
        self._univ = univ
        self._marketcap_matrix = None
        self._liquidity_matrix = None
        self._mask_matrix = None

    def _require_bound(self) -> tuple[np.ndarray, list[str], np.ndarray, DataLoader, Univ]:
        """
        Filter가 Backtest 컨텍스트에 바인딩됐는지 확인한다.
        """

        if (
            self._dates is None
            or self._codes is None
            or self._prices is None
            or self._data_loader is None
            or self._univ is None
        ):
            raise ValueError("Filter는 Backtest.analyze(..., filter=...) 이후에 사용할 수 있습니다.")
        return self._dates, self._codes, self._prices, self._data_loader, self._univ

    @staticmethod
    def _build_decile_mask_matrix(
        values: np.ndarray,
        buckets: tuple[int, ...],
        base_mask: np.ndarray | None = None,
    ) -> np.ndarray:
        """
        날짜별 단면을 10분위로 나눠 선택 bucket만 남긴 마스크를 만든다.
        """

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
        """
        실제 적용할 필터 단계를 순서대로 반환한다.
        """

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
        dates, codes, _, data_loader, univ = self._require_bound()
        if self._marketcap_matrix is None:
            mcap_df = data_loader.load_stock_field_table("marketcap", univ)
            aligned = mcap_df.reindex(
                index=pd.DatetimeIndex(dates),
                columns=pd.Index(codes, dtype="object"),
            )
            self._marketcap_matrix = aligned.to_numpy(dtype=np.float64, copy=True)
        return self._marketcap_matrix

    def _get_liquidity_matrix(self) -> np.ndarray:
        dates, codes, _, data_loader, univ = self._require_bound()
        if self._liquidity_matrix is None:
            tables = data_loader.load_stock_field_tables(["amount", "marketcap"], univ)
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


__all__ = ["Filter"]
