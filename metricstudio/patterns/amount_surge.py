"""Amount surge pattern."""

from __future__ import annotations

from types import SimpleNamespace

import numpy as np

from metricstudio import util as u
from metricstudio.patterns.base import BasePattern


class AmountSurge(BasePattern):
    """
    거래대금 급증 구간을 찾는 수급 패턴.
    """

    def on(
        self,
        window: int = 20,
        threshold: float = 2.0,
        stay_days: int = 1,
        cooldown_days: int = 0,
    ):
        """
        거래대금 평균 대비 급증 배수와 신호 지속 규칙을 설정한다.
        """

        window_value = int(window)
        if window_value <= 0:
            raise ValueError("window는 1 이상이어야 합니다.")
        threshold_value = float(threshold)
        if not np.isfinite(threshold_value) or threshold_value <= 0.0:
            raise ValueError("threshold는 0보다 큰 유한한 숫자여야 합니다.")

        self.params = SimpleNamespace(
            window=window_value,
            threshold=threshold_value,
            stay_days=int(max(1, stay_days)),
            cooldown_days=int(max(0, cooldown_days)),
        )
        return self

    def _required_stock_fields(self) -> tuple[str, ...]:
        return ("amount",)

    def rank_metrics(self) -> dict[str, str]:
        return {"ratio": "desc"}

    def _compute_rank_metric_series(
        self,
        metric: str,
        prices: np.ndarray,
        get_stock_field,
    ) -> np.ndarray:
        if self.params is None:
            raise ValueError("AmountSurge는 사용 전에 on(...)으로 설정해야 합니다.")
        if str(metric).strip().lower() != "ratio":
            raise KeyError(metric)

        amount = np.asarray(get_stock_field("amount"), dtype=np.float64)
        out = np.full(amount.shape[0], np.nan, dtype=np.float64)
        if amount.shape[0] < self.params.window:
            return out

        mean_amount, valid_end = u.rolling_mean(amount, self.params.window)
        valid = (
            valid_end
            & np.isfinite(amount)
            & (amount > 0.0)
            & np.isfinite(mean_amount)
            & (mean_amount > 0.0)
        )
        out[valid] = amount[valid] / mean_amount[valid]
        return out

    def _base_mask(self, values: np.ndarray) -> np.ndarray:
        if self.params is None:
            raise ValueError("AmountSurge는 사용 전에 on(...)으로 설정해야 합니다.")

        amount = self._get_stock_values("amount")
        n = amount.shape[0]
        out = np.zeros(n, dtype=np.bool_)
        if n < self.params.window:
            return out

        mean_amount, valid_end = u.rolling_mean(amount, self.params.window)
        valid = (
            valid_end
            & np.isfinite(amount)
            & (amount > 0.0)
            & np.isfinite(mean_amount)
            & (mean_amount > 0.0)
        )
        ratio = np.zeros(n, dtype=np.float64)
        ratio[valid] = amount[valid] / mean_amount[valid]
        cond = valid & np.isfinite(ratio) & (ratio >= self.params.threshold)
        return u.stay_cooldown_mask(cond, self.params.stay_days, self.params.cooldown_days)


__all__ = ["AmountSurge"]
