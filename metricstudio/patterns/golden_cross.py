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

    def rank_metrics(self) -> dict[str, str]:
        return {"alignment_gap": "desc"}

    def _compute_rank_metric_series(
        self,
        metric: str,
        prices: np.ndarray,
        get_stock_field,
    ) -> np.ndarray:
        if self.params is None:
            raise ValueError("GoldenCross는 사용 전에 on(...)으로 설정해야 합니다.")
        if str(metric).strip().lower() != "alignment_gap":
            raise KeyError(metric)

        series = np.asarray(prices, dtype=np.float64)
        out = np.full(series.shape[0], np.nan, dtype=np.float64)
        if series.shape[0] == 0:
            return out

        valid = np.ones(series.shape[0], dtype=np.bool_)
        means: list[np.ndarray] = []
        for window in self.params.windows:
            mean, valid_end = u.rolling_mean(series, window)
            means.append(mean)
            valid &= valid_end & np.isfinite(mean) & (mean > 0.0)

        if len(means) < 2:
            return out

        spread = np.full(series.shape[0], np.nan, dtype=np.float64)
        for i in range(len(means) - 1):
            curr = means[i]
            nxt = means[i + 1]
            pair_valid = valid & np.isfinite(curr) & np.isfinite(nxt) & (nxt > 0.0)
            pair_spread = np.full(series.shape[0], np.nan, dtype=np.float64)
            pair_spread[pair_valid] = curr[pair_valid] / nxt[pair_valid] - 1.0
            if i == 0:
                spread = pair_spread
            else:
                both_valid = np.isfinite(spread) & np.isfinite(pair_spread)
                spread[both_valid] = np.minimum(spread[both_valid], pair_spread[both_valid])
                spread[~np.isfinite(spread)] = pair_spread[~np.isfinite(spread)]
        out[np.isfinite(spread)] = spread[np.isfinite(spread)]
        return out

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
