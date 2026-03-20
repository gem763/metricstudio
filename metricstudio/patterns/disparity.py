"""Disparity pattern."""

from __future__ import annotations

from types import SimpleNamespace

import numpy as np

from metricstudio import util as u
from metricstudio.patterns.base import BasePattern


class Disparity(BasePattern):
    """
    이동평균 대비 이격도(disparity) 기반 역추세 패턴.
    """

    def __init__(self, *args, **kwargs):
        name, window = self._resolve_name_window_init_args(
            args,
            kwargs,
            default_window=20,
            class_name=self.__class__.__name__,
        )
        super().__init__(name=name)
        self.window = int(window)

    def on(
        self,
        threshold: float = 0.0,
        stay_days: int = 1,
        cooldown_days: int = 0,
    ):
        """
        이격도 임계값과 신호 유지/쿨다운 규칙을 설정한다.
        """

        window_value = int(self.window)
        if window_value <= 0:
            raise ValueError("window는 1 이상이어야 합니다.")

        self.params = SimpleNamespace(
            threshold=float(threshold),
            stay_days=int(max(1, stay_days)),
            cooldown_days=int(max(0, cooldown_days)),
        )
        return self

    def rank_metrics(self) -> dict[str, str]:
        return {"disparity": "asc"}

    def _compute_rank_metric_series(
        self,
        metric: str,
        prices: np.ndarray,
        get_stock_field,
    ) -> np.ndarray:
        if self.params is None:
            raise ValueError("Disparity는 사용 전에 on(...)으로 설정해야 합니다.")
        if str(metric).strip().lower() != "disparity":
            raise KeyError(metric)

        series = np.asarray(prices, dtype=np.float64)
        out = np.full(series.shape[0], np.nan, dtype=np.float64)
        if series.shape[0] < self.window:
            return out

        ma, valid_end = u.rolling_mean(series, self.window)
        valid = valid_end & np.isfinite(series) & (series > 0.0) & np.isfinite(ma) & (ma > 0.0)
        out[valid] = series[valid] / ma[valid]
        return out

    def _base_mask(self, values: np.ndarray) -> np.ndarray:
        if self.params is None:
            raise ValueError("Disparity는 사용 전에 on(...)으로 설정해야 합니다.")

        prices = np.asarray(values, dtype=np.float64)
        n = prices.shape[0]
        out = np.zeros(n, dtype=np.bool_)
        if n < self.window:
            return out

        ma, valid_end = u.rolling_mean(prices, self.window)
        valid = valid_end & np.isfinite(ma) & (ma > 0.0)
        if not np.any(valid):
            return out

        disparity = np.zeros(n, dtype=np.float64)
        disparity[valid] = prices[valid] / ma[valid]
        cond = valid & np.isfinite(prices) & (prices > 0.0) & (disparity < self.params.threshold)
        return u.stay_cooldown_mask(cond, self.params.stay_days, self.params.cooldown_days)


__all__ = ["Disparity"]
