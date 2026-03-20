"""High breakout pattern."""

from __future__ import annotations

from types import SimpleNamespace

import numpy as np

from metricstudio import util as u
from metricstudio.patterns.base import BasePattern


class High(BasePattern):
    """
    최근 고가권 돌파/근접 구간을 감지하는 모멘텀 패턴.
    """

    def on(
        self,
        window: int,
        threshold: float = 0.9,
        stay_days: int = 1,
        cooldown_days: int = 0,
    ):
        """
        고점 기준 창 길이와 발동 임계값을 설정한다.
        """

        window_value = int(window)
        if window_value <= 0:
            raise ValueError("window는 1 이상이어야 합니다.")

        self.params = SimpleNamespace(
            window=window_value,
            threshold=float(threshold),
            stay_days=int(max(1, stay_days)),
            cooldown_days=int(max(0, cooldown_days)),
        )
        return self

    def rank_metrics(self) -> dict[str, str]:
        return {"proximity": "desc"}

    def _compute_rank_metric_series(
        self,
        metric: str,
        prices: np.ndarray,
        get_stock_field,
    ) -> np.ndarray:
        if self.params is None:
            raise ValueError("High는 사용 전에 on(...)으로 설정해야 합니다.")
        if str(metric).strip().lower() != "proximity":
            raise KeyError(metric)

        series = np.asarray(prices, dtype=np.float64)
        out = np.full(series.shape[0], np.nan, dtype=np.float64)
        high_series = u.rolling_high(series, self.params.window)
        valid = (
            np.isfinite(series)
            & (series > 0.0)
            & np.isfinite(high_series)
            & (high_series > 0.0)
        )
        out[valid] = series[valid] / high_series[valid]
        return out

    def _base_mask(self, values: np.ndarray) -> np.ndarray:
        if self.params is None:
            raise ValueError("High는 사용 전에 on(...)으로 설정해야 합니다.")

        prices = np.asarray(values, dtype=np.float64)
        cond = u.high_mask(
            prices,
            self.params.window,
            self.params.threshold,
        )
        return u.stay_cooldown_mask(cond, self.params.stay_days, self.params.cooldown_days)


__all__ = ["High"]
