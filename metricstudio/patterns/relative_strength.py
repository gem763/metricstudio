"""Relative strength pattern."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Literal

import numpy as np

from metricstudio import util as u
from metricstudio.patterns.base import BasePattern


class RelativeStrength(BasePattern):
    """
    시장 대비 상대수익률 우위/열위를 감지하는 패턴.
    """

    def on(
        self,
        window: int = 60,
        trigger: Literal["above", "below"] = "above",
        threshold: float = 0.0,
        stay_days: int = 1,
        cooldown_days: int = 0,
        market: str | None = None,
    ):
        """
        비교 시장과 상대수익률 기준 창/임계값을 설정한다.
        """

        window_value = int(window)
        if window_value <= 0:
            raise ValueError("window는 1 이상이어야 합니다.")
        trigger_text = str(trigger or "above").strip().lower()
        if trigger_text not in {"above", "below"}:
            raise ValueError("trigger는 'above' 또는 'below'여야 합니다.")
        threshold_value = float(threshold)
        if not np.isfinite(threshold_value):
            raise ValueError("threshold는 유한한 숫자여야 합니다.")
        if market is not None:
            self.market(market, field="close")
        if self.market_name is None:
            raise ValueError("RelativeStrength는 market을 지정해야 합니다.")

        self.params = SimpleNamespace(
            window=window_value,
            trigger=trigger_text,
            threshold=threshold_value,
            stay_days=int(max(1, stay_days)),
            cooldown_days=int(max(0, cooldown_days)),
        )
        return self

    def __call__(self, values: np.ndarray) -> np.ndarray:
        prices = np.asarray(values, dtype=np.float64)
        stock_keys = tuple(
            (field, self._array_cache_token(arr))
            for field, arr in sorted(self._stock_values.items())
        )
        cache_key = (
            self._array_cache_token(prices),
            self._array_cache_token(self._market_values),
            stock_keys,
            id(self._post_mask_fn),
        )
        if self._cached_mask_key != cache_key or self._cached_mask_value is None:
            base_mask = np.asarray(self._base_mask(prices), dtype=np.bool_)
            if base_mask.shape != prices.shape:
                raise ValueError(f"패턴 '{self.name}'의 mask shape이 일치하지 않습니다.")
            post_mask = np.asarray(self._post_mask_fn(prices), dtype=np.bool_)
            if post_mask.shape != prices.shape:
                raise ValueError(f"패턴 '{self.name}'의 후처리 mask shape이 일치하지 않습니다.")
            self._cached_mask_key = cache_key
            self._cached_mask_value = base_mask & post_mask
        return self._cached_mask_value

    def rank_metrics(self) -> dict[str, str]:
        if self.params is None:
            return {}
        order = "desc" if str(self.params.trigger) == "above" else "asc"
        return {"excess_return": order}

    def _compute_rank_metric_series(
        self,
        metric: str,
        prices: np.ndarray,
        get_stock_field,
    ) -> np.ndarray:
        if self.params is None:
            raise ValueError("RelativeStrength는 사용 전에 on(...)으로 설정해야 합니다.")
        if self.market_name is None or self._market_values is None:
            raise ValueError("RelativeStrength의 market 데이터가 준비되지 않았습니다.")
        if str(metric).strip().lower() != "excess_return":
            raise KeyError(metric)

        stock = np.asarray(prices, dtype=np.float64)
        market = np.asarray(self._market_values, dtype=np.float64)
        if market.shape != stock.shape:
            raise ValueError("RelativeStrength market shape이 가격 시계열과 일치하지 않습니다.")

        n = stock.shape[0]
        out = np.full(n, np.nan, dtype=np.float64)
        window = int(self.params.window)
        if n <= window:
            return out

        stock_prev = stock[:-window]
        stock_curr = stock[window:]
        market_prev = market[:-window]
        market_curr = market[window:]
        valid = (
            np.isfinite(stock_prev)
            & (stock_prev > 0.0)
            & np.isfinite(stock_curr)
            & (stock_curr > 0.0)
            & np.isfinite(market_prev)
            & (market_prev > 0.0)
            & np.isfinite(market_curr)
            & (market_curr > 0.0)
        )
        rel = np.full(n - window, np.nan, dtype=np.float64)
        rel[valid] = (stock_curr[valid] / stock_prev[valid] - 1.0) - (market_curr[valid] / market_prev[valid] - 1.0)
        out[window:] = rel
        return out

    def _base_mask(self, values: np.ndarray) -> np.ndarray:
        if self.params is None:
            raise ValueError("RelativeStrength는 사용 전에 on(...)으로 설정해야 합니다.")
        if self.market_name is None or self._market_values is None:
            raise ValueError("RelativeStrength의 market 데이터가 준비되지 않았습니다.")

        prices = np.asarray(values, dtype=np.float64)
        market = np.asarray(self._market_values, dtype=np.float64)
        if market.shape != prices.shape:
            raise ValueError("RelativeStrength market shape이 가격 시계열과 일치하지 않습니다.")

        n = prices.shape[0]
        out = np.zeros(n, dtype=np.bool_)
        window = int(self.params.window)
        if n <= window:
            return out

        stock_prev = prices[:-window]
        stock_curr = prices[window:]
        market_prev = market[:-window]
        market_curr = market[window:]
        valid = (
            np.isfinite(stock_prev)
            & (stock_prev > 0.0)
            & np.isfinite(stock_curr)
            & (stock_curr > 0.0)
            & np.isfinite(market_prev)
            & (market_prev > 0.0)
            & np.isfinite(market_curr)
            & (market_curr > 0.0)
        )
        stock_ret = np.zeros(n - window, dtype=np.float64)
        market_ret = np.zeros(n - window, dtype=np.float64)
        stock_ret[valid] = stock_curr[valid] / stock_prev[valid] - 1.0
        market_ret[valid] = market_curr[valid] / market_prev[valid] - 1.0
        rel = stock_ret - market_ret

        cond = np.zeros(n, dtype=np.bool_)
        if self.params.trigger == "above":
            cond[window:] = valid & np.isfinite(rel) & (rel >= float(self.params.threshold))
        else:
            cond[window:] = valid & np.isfinite(rel) & (rel <= float(self.params.threshold))
        return u.stay_cooldown_mask(cond, self.params.stay_days, self.params.cooldown_days)


__all__ = ["RelativeStrength"]
