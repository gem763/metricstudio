"""Trending pattern."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Literal

import numpy as np

from metricstudio import util as u
from metricstudio.patterns.base import BasePattern


class Trending(BasePattern):
    """
    이동평균 기반 breakout 또는 추세 기울기를 사용하는 패턴.
    """

    def on(
        self,
        window: int = 20,
        trigger: Literal[
            "breakout_up",
            "breakout_down",
            "ma_trend_up",
            "ma_trend_down",
        ] = "breakout_up",
        stay_days: int = 1,
        cooldown_days: int = 0,
    ):
        """
        추세 판별 창과 breakout/추세 유형을 설정한다.
        """

        window_value = int(window)
        stay_days_value = int(max(1, stay_days))
        trigger_text = str(trigger or "breakout_up").lower()
        if window_value <= 0:
            raise ValueError("window는 1 이상이어야 합니다.")
        if trigger_text not in {"breakout_up", "breakout_down", "ma_trend_up", "ma_trend_down"}:
            raise ValueError(
                "trigger는 {'breakout_up', 'breakout_down', 'ma_trend_up', 'ma_trend_down'} 중 하나여야 합니다."
            )

        self.params = SimpleNamespace(
            window=window_value,
            trigger=trigger_text,
            stay_days=stay_days_value,
            cooldown_days=int(max(0, cooldown_days)),
        )
        return self

    def _base_mask(self, values: np.ndarray) -> np.ndarray:
        if self.params is None:
            raise ValueError("Trending은 사용 전에 on(...)으로 설정해야 합니다.")

        prices = np.asarray(values, dtype=np.float64)
        n = prices.shape[0]
        out = np.zeros(n, dtype=np.bool_)
        if n <= 1:
            return out

        mean, valid_end = u.rolling_mean(prices, self.params.window)
        trigger = self.params.trigger

        if trigger in {"breakout_up", "breakout_down"}:
            direction = 1 if trigger == "breakout_up" else -1
            out = u.breakout_mask(prices, mean, valid_end, direction)
            return u.stay_cooldown_mask(out, self.params.stay_days, self.params.cooldown_days)

        is_uptrend = trigger == "ma_trend_up"
        valid_pair = valid_end[1:] & valid_end[:-1]
        if is_uptrend:
            out[1:] = valid_pair & (mean[1:] > mean[:-1])
        else:
            out[1:] = valid_pair & (mean[1:] < mean[:-1])
        return u.stay_cooldown_mask(out, self.params.stay_days, self.params.cooldown_days)


__all__ = ["Trending"]
