"""Panic rebound pattern."""

from __future__ import annotations

from types import SimpleNamespace

import numpy as np

from metricstudio import util as u
from metricstudio.patterns.base import BasePattern


class PanicRebound(BasePattern):
    """
    급락 이후 반등 초입을 노리는 panic rebound 패턴.
    """

    def on(
        self,
        drawdown_window: int = 20,
        drawdown_min: float = -0.18,
        rebound_days: int = 3,
        volume_spike: bool = True,
        volume_window: int = 20,
        volume_threshold: float = 1.5,
        stay_days: int = 1,
        cooldown_days: int = 0,
    ):
        """
        급락 강도, 반등 확인, 거래량 스파이크 조건을 설정한다.
        """

        drawdown_window_value = int(drawdown_window)
        if drawdown_window_value <= 0:
            raise ValueError("drawdown_window은 1 이상이어야 합니다.")
        drawdown_min_value = float(drawdown_min)
        if not np.isfinite(drawdown_min_value) or drawdown_min_value >= 0.0:
            raise ValueError("drawdown_min은 0보다 작은 유한한 숫자여야 합니다.")
        rebound_days_value = int(rebound_days)
        if rebound_days_value <= 0:
            raise ValueError("rebound_days는 1 이상이어야 합니다.")
        volume_window_value = int(volume_window)
        if volume_window_value <= 0:
            raise ValueError("volume_window는 1 이상이어야 합니다.")
        volume_threshold_value = float(volume_threshold)
        if not np.isfinite(volume_threshold_value) or volume_threshold_value <= 0.0:
            raise ValueError("volume_threshold는 0보다 큰 유한한 숫자여야 합니다.")

        self.params = SimpleNamespace(
            drawdown_window=drawdown_window_value,
            drawdown_min=drawdown_min_value,
            rebound_days=rebound_days_value,
            volume_spike=bool(volume_spike),
            volume_window=volume_window_value,
            volume_threshold=volume_threshold_value,
            stay_days=int(max(1, stay_days)),
            cooldown_days=int(max(0, cooldown_days)),
        )
        return self

    def _required_stock_fields(self) -> tuple[str, ...]:
        if self.params is None:
            return ()
        if not bool(self.params.volume_spike):
            return ()
        return ("volume",)

    def rank_metrics(self) -> dict[str, str]:
        return {"panic_depth": "asc"}

    def _compute_rank_metric_series(
        self,
        metric: str,
        prices: np.ndarray,
        get_stock_field,
    ) -> np.ndarray:
        if self.params is None:
            raise ValueError("PanicRebound는 사용 전에 on(...)으로 설정해야 합니다.")
        if str(metric).strip().lower() != "panic_depth":
            raise KeyError(metric)

        series = np.asarray(prices, dtype=np.float64)
        out = np.full(series.shape[0], np.nan, dtype=np.float64)
        if series.shape[0] == 0:
            return out

        rolling_high = u.rolling_high(series, int(self.params.drawdown_window))
        valid = (
            np.isfinite(series)
            & (series > 0.0)
            & np.isfinite(rolling_high)
            & (rolling_high > 0.0)
        )
        out[valid] = series[valid] / rolling_high[valid] - 1.0
        return out

    def _base_mask(self, values: np.ndarray) -> np.ndarray:
        if self.params is None:
            raise ValueError("PanicRebound는 사용 전에 on(...)으로 설정해야 합니다.")

        prices = np.asarray(values, dtype=np.float64)
        n = prices.shape[0]
        out = np.zeros(n, dtype=np.bool_)
        if n == 0:
            return out

        rolling_high = u.rolling_high(prices, int(self.params.drawdown_window))
        panic_mask = np.zeros(n, dtype=np.bool_)
        valid_price = np.isfinite(prices) & (prices > 0.0)
        valid_high = np.isfinite(rolling_high) & (rolling_high > 0.0)
        valid_drawdown = valid_price & valid_high
        drawdown = np.zeros(n, dtype=np.float64)
        drawdown[valid_drawdown] = prices[valid_drawdown] / rolling_high[valid_drawdown] - 1.0
        panic_mask[valid_drawdown] = drawdown[valid_drawdown] <= float(self.params.drawdown_min)

        volume_ratio = None
        volume_ratio_valid = None
        if bool(self.params.volume_spike):
            volume = self._get_stock_values("volume")
            if volume.shape != prices.shape:
                raise ValueError("PanicRebound volume shape이 가격 시계열과 일치하지 않습니다.")
            mean_volume, valid_end = u.rolling_mean(volume, int(self.params.volume_window))
            volume_ratio_valid = (
                valid_end
                & np.isfinite(volume)
                & (volume > 0.0)
                & np.isfinite(mean_volume)
                & (mean_volume > 0.0)
            )
            volume_ratio = np.zeros(n, dtype=np.float64)
            volume_ratio[volume_ratio_valid] = volume[volume_ratio_valid] / mean_volume[volume_ratio_valid]

        rebound_days = int(self.params.rebound_days)
        for i in range(rebound_days, n):
            if bool(self.params.volume_spike):
                if not bool(volume_ratio_valid[i]):
                    continue
                if float(volume_ratio[i]) < float(self.params.volume_threshold):
                    continue

            rebound_ok = True
            for j in range(i - rebound_days + 1, i + 1):
                if j <= 0:
                    rebound_ok = False
                    break
                prev_price = prices[j - 1]
                curr_price = prices[j]
                if not (
                    np.isfinite(prev_price)
                    and prev_price > 0.0
                    and np.isfinite(curr_price)
                    and curr_price > prev_price
                ):
                    rebound_ok = False
                    break
            if not rebound_ok:
                continue

            lo = max(0, i - rebound_days)
            if not bool(np.any(panic_mask[lo : i + 1])):
                continue
            out[i] = True

        return u.stay_cooldown_mask(out, self.params.stay_days, self.params.cooldown_days)


__all__ = ["PanicRebound"]
