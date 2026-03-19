"""Golden cross pattern."""

from __future__ import annotations

from types import SimpleNamespace

import numpy as np

from metricstudio import util as u
from metricstudio.patterns.base import BasePattern


class GoldenCross(BasePattern):
    """
    다중 이동평균선의 정배열 상태를 감지하는 골든크로스 패턴.
    """

    def on(
        self,
        windows: list[int] | tuple[int, ...] = (5, 10, 20),
        stay_days: int = 1,
        cooldown_days: int = 3,
    ):
        """
        사용 이동평균 창 목록과 신호 지속 규칙을 설정한다.
        """

        ws = tuple(int(w) for w in windows)
        stay_days_value = int(max(1, stay_days))
        if len(ws) < 2:
            raise ValueError("windows에는 최소 2개 이상의 값이 있어야 합니다.")
        if any(w <= 0 for w in ws):
            raise ValueError("windows의 모든 값은 1 이상이어야 합니다.")
        if any(ws[i] >= ws[i + 1] for i in range(len(ws) - 1)):
            raise ValueError("windows는 엄격한 오름차순이어야 합니다 (예: [5, 10, 20]).")

        self.params = SimpleNamespace(
            windows=ws,
            stay_days=stay_days_value,
            cooldown_days=int(max(0, cooldown_days)),
        )
        return self

    def _base_mask(self, values: np.ndarray) -> np.ndarray:
        if self.params is None:
            raise ValueError("GoldenCross는 사용 전에 on(...)으로 설정해야 합니다.")

        prices = np.asarray(values, dtype=np.float64)
        n = prices.shape[0]
        out = np.zeros(n, dtype=np.bool_)
        if n == 0:
            return out

        valid = np.ones(n, dtype=np.bool_)
        means: list[np.ndarray] = []
        for window in self.params.windows:
            mean, valid_end = u.rolling_mean(prices, window)
            means.append(mean)
            valid &= valid_end

        cond = valid.copy()
        for i in range(len(means) - 1):
            cond &= means[i] > means[i + 1]

        return u.stay_cooldown_mask(cond, self.params.stay_days, self.params.cooldown_days)


__all__ = ["GoldenCross"]
