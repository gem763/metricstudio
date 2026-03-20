"""Bollinger pattern."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Literal

import numpy as np

from metricstudio import util as u
from metricstudio.patterns.base import BasePattern


class Bollinger(BasePattern):
    """
    볼린저밴드 squeeze/breakout 계열 패턴.
    """

    def __init__(self, *args, **kwargs):
        name, window, sigma = self._resolve_name_window_sigma_init_args(
            args,
            kwargs,
            default_window=20,
            default_sigma=2.0,
            class_name=self.__class__.__name__,
        )
        super().__init__(name=name)
        self.window = int(window)
        self.sigma = float(sigma)

    def on(
        self,
        trigger: Literal[
            "breakout_up",
            "breakout_down",
            "near_up",
            "near_down",
        ]
        | None = None,
        bandwidth_min: float = 0.0,
        bandwidth_max: float = 1.0,
        bandwidth_stay_days: int = 1,
        bandwidth_type: Literal["absolute", "percentile"] = "absolute",
        bandwidth_percentile_window: int = 240,
        breakout_cooldown_days: int = 0,
        near_tolerance: float = 0.03,
        near_stay_days: int = 1,
    ):
        """
        밴드 폭, 돌파/근접 조건을 설정한다.
        """

        trigger_text = None if trigger is None else str(trigger).lower()
        if trigger_text is not None and trigger_text not in {
            "breakout_up",
            "breakout_down",
            "near_up",
            "near_down",
        }:
            raise ValueError(
                "trigger는 다음 중 하나여야 합니다: "
                "{'breakout_up', 'breakout_down', 'near_up', 'near_down'}."
            )
        bandwidth_type_text = str(bandwidth_type or "absolute").lower()
        if bandwidth_type_text not in {"absolute", "percentile"}:
            raise ValueError("bandwidth_type은 'absolute' 또는 'percentile'이어야 합니다.")
        bandwidth_min_value = float(bandwidth_min)
        bandwidth_max_value = float(bandwidth_max)
        if bandwidth_min_value < 0.0:
            raise ValueError("bandwidth_min은 0 이상이어야 합니다.")
        if bandwidth_max_value < bandwidth_min_value:
            raise ValueError("bandwidth_max는 bandwidth_min 이상이어야 합니다.")

        self.params = SimpleNamespace(
            trigger=trigger_text,
            bandwidth_min=bandwidth_min_value,
            bandwidth_max=bandwidth_max_value,
            bandwidth_stay_days=int(max(1, bandwidth_stay_days)),
            bandwidth_type=bandwidth_type_text,
            bandwidth_percentile_window=int(max(1, bandwidth_percentile_window)),
            breakout_cooldown_days=int(max(0, breakout_cooldown_days)),
            near_tolerance=float(near_tolerance),
            near_stay_days=int(max(1, near_stay_days)),
        )
        return self

    def rank_metrics(self) -> dict[str, str]:
        return {"bandwidth": "asc"}

    def _compute_rank_metric_series(
        self,
        metric: str,
        prices: np.ndarray,
        get_stock_field,
    ) -> np.ndarray:
        if self.params is None:
            raise ValueError("Bollinger는 사용 전에 on(...)으로 설정해야 합니다.")
        if str(metric).strip().lower() != "bandwidth":
            raise KeyError(metric)

        series = np.asarray(prices, dtype=np.float64)
        out = np.full(series.shape[0], np.nan, dtype=np.float64)
        if self.window <= 0 or series.shape[0] < self.window:
            return out

        mean, std, valid_end = u.rolling_mean_std(series, self.window)
        valid = valid_end & np.isfinite(mean) & (mean > 0.0) & np.isfinite(std)
        out[valid] = (float(self.sigma) * std[valid]) / mean[valid]
        return out

    def _base_mask(self, values: np.ndarray) -> np.ndarray:
        prices = np.asarray(values, dtype=np.float64)
        n = prices.shape[0]
        mask = np.zeros(n, dtype=np.bool_)

        if self.params is None:
            raise ValueError("Bollinger는 사용 전에 on(...)으로 설정해야 합니다.")

        if self.window <= 0 or n < self.window:
            return mask

        mean, std, valid_end = u.rolling_mean_std(prices, self.window)
        if not np.any(valid_end):
            return mask

        band_width = self.sigma * std
        upper = mean + band_width
        lower = mean - band_width

        params = self.params
        trigger = params.trigger
        bandwidth_min = params.bandwidth_min
        bandwidth_max = params.bandwidth_max
        bandwidth_stay_days = params.bandwidth_stay_days
        bandwidth_type = params.bandwidth_type
        bandwidth_percentile_window = params.bandwidth_percentile_window
        breakout_cooldown_days = params.breakout_cooldown_days
        near_tolerance = params.near_tolerance
        near_stay_days = params.near_stay_days

        mode = 0 if bandwidth_type == "absolute" else 1
        band_cond = u.bandwidth_mask(
            mean,
            band_width,
            valid_end,
            bandwidth_min,
            bandwidth_max,
            mode,
            bandwidth_percentile_window,
        )
        band_cond = u.stay_mask(band_cond, bandwidth_stay_days)
        band_mask = valid_end & band_cond

        if trigger in {"breakout_up", "breakout_down"}:
            trigger_line = upper if trigger == "breakout_up" else lower
            direction = 1 if trigger == "breakout_up" else -1
            out = u.breakout_mask(
                prices,
                trigger_line,
                band_mask,
                direction,
            )
            return u.cooldown_mask(out, breakout_cooldown_days)

        if trigger in {"near_up", "near_down"}:
            trigger_line = upper if trigger == "near_up" else lower
            direction = 1 if trigger == "near_up" else -1
            out = u.near_mask(
                prices,
                trigger_line,
                band_mask,
                near_tolerance,
                direction,
            )
            return u.stay_mask(out, near_stay_days)

        if trigger is None:
            return band_mask

        raise ValueError(f"지원하지 않는 trigger 종류입니다: {trigger}")


__all__ = ["Bollinger"]
