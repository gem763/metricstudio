"""MFI pattern."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Literal

import numpy as np

from metricstudio import util as u
from metricstudio.patterns.base import BasePattern


class MFI(BasePattern):
    """
    Money Flow Index 기반 과매도/반등 패턴.
    """

    def __init__(self, *args, **kwargs):
        name, window = self._resolve_name_window_init_args(
            args,
            kwargs,
            default_window=14,
            class_name=self.__class__.__name__,
        )
        super().__init__(name=name)
        self.window = int(window)

    def on(
        self,
        trigger: Literal[
            "oversold_rebound",
            "bullish_failure_swing",
            "above",
            "below",
        ] = "oversold_rebound",
        lower: float = 20.0,
        threshold: float | None = None,
        stay_days: int = 1,
        cooldown_days: int = 0,
    ):
        """
        MFI 트리거 종류와 기준값, 신호 지속 규칙을 설정한다.
        """

        window_value = int(self.window)
        if window_value <= 0:
            raise ValueError("window는 1 이상이어야 합니다.")
        trigger_text = str(trigger or "oversold_rebound").lower()
        if trigger_text not in {"oversold_rebound", "bullish_failure_swing", "above", "below"}:
            raise ValueError(
                "trigger는 {'oversold_rebound', 'bullish_failure_swing', 'above', 'below'} 중 하나여야 합니다."
            )
        lower_value = float(lower)
        if not np.isfinite(lower_value) or lower_value <= 0.0 or lower_value >= 100.0:
            raise ValueError("lower는 0과 100 사이의 값이어야 합니다.")
        threshold_value = None if threshold is None else float(threshold)
        if trigger_text in {"above", "below"}:
            if threshold_value is None or not np.isfinite(threshold_value):
                raise ValueError("trigger가 'above' 또는 'below'일 때는 threshold를 지정해야 합니다.")
            if threshold_value <= 0.0 or threshold_value >= 100.0:
                raise ValueError("threshold는 0과 100 사이의 값이어야 합니다.")

        self.params = SimpleNamespace(
            trigger=trigger_text,
            lower=lower_value,
            threshold=threshold_value,
            stay_days=int(max(1, stay_days)),
            cooldown_days=int(max(0, cooldown_days)),
        )
        return self

    def _required_stock_fields(self) -> tuple[str, ...]:
        return ("high", "low", "volume")

    @staticmethod
    def _bullish_failure_swing_mask(
        mfi: np.ndarray,
        valid_end: np.ndarray,
        lower: float,
    ) -> np.ndarray:
        n = mfi.shape[0]
        out = np.zeros(n, dtype=np.bool_)
        oversold_seen = False
        rebound_high = np.nan
        pullback_seen = False

        for i in range(n):
            if not valid_end[i]:
                continue
            value = float(mfi[i])
            if not np.isfinite(value):
                continue

            if value <= lower:
                oversold_seen = True
                rebound_high = np.nan
                pullback_seen = False
                continue

            if not oversold_seen:
                continue

            if not np.isfinite(rebound_high):
                rebound_high = value
                continue

            if value > rebound_high:
                if pullback_seen:
                    out[i] = True
                    oversold_seen = False
                    rebound_high = np.nan
                    pullback_seen = False
                else:
                    rebound_high = value
                continue

            pullback_seen = True

        return out

    def _base_mask(self, values: np.ndarray) -> np.ndarray:
        if self.params is None:
            raise ValueError("MFI는 사용 전에 on(...)으로 설정해야 합니다.")

        close = np.asarray(values, dtype=np.float64)
        high = self._get_stock_values("high")
        low = self._get_stock_values("low")
        volume = self._get_stock_values("volume")
        if not (high.shape == low.shape == close.shape == volume.shape):
            raise ValueError("MFI 입력 시계열 shape이 일치하지 않습니다.")

        mfi, valid_end = u.money_flow_index(high, low, close, volume, self.window)
        trigger = self.params.trigger
        lower = float(self.params.lower)
        threshold = getattr(self.params, "threshold", None)

        if trigger == "above":
            cond = valid_end & np.isfinite(mfi) & (mfi > float(threshold))
            return u.stay_cooldown_mask(cond, self.params.stay_days, self.params.cooldown_days)

        if trigger == "below":
            cond = valid_end & np.isfinite(mfi) & (mfi < float(threshold))
            return u.stay_cooldown_mask(cond, self.params.stay_days, self.params.cooldown_days)

        if trigger == "oversold_rebound":
            cond = np.zeros(close.shape[0], dtype=np.bool_)
            for i in range(1, close.shape[0]):
                if not (valid_end[i] and valid_end[i - 1]):
                    continue
                prev_val = float(mfi[i - 1])
                curr_val = float(mfi[i])
                if not (np.isfinite(prev_val) and np.isfinite(curr_val)):
                    continue
                cond[i] = prev_val <= lower and curr_val > lower
            return u.stay_cooldown_mask(cond, self.params.stay_days, self.params.cooldown_days)

        cond = self._bullish_failure_swing_mask(mfi, valid_end, lower)
        return u.stay_cooldown_mask(cond, self.params.stay_days, self.params.cooldown_days)


__all__ = ["MFI"]
