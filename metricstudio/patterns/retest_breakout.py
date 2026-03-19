"""Retest breakout pattern."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Literal

import numpy as np

from metricstudio import util as u
from metricstudio.patterns.base import BasePattern


class RetestBreakout(BasePattern):
    """
    돌파 후 재지지(retest) 패턴을 찾는 가격/수급 혼합 신호.
    """

    def on(
        self,
        breakout_window: int = 20,
        retest_tolerance: float = 0.03,
        max_retest_days: int = 10,
        breakout_amount_threshold: float | None = None,
        breakout_amount_window: int = 20,
        rebound_confirm: Literal["close_up"] = "close_up",
        stay_days: int = 1,
        cooldown_days: int = 0,
    ):
        """
        돌파, 재테스트, 거래대금 확인 조건을 한 번에 설정한다.
        """

        breakout_window_value = int(breakout_window)
        if breakout_window_value <= 0:
            raise ValueError("breakout_window은 1 이상이어야 합니다.")
        retest_tolerance_value = float(retest_tolerance)
        if (
            not np.isfinite(retest_tolerance_value)
            or retest_tolerance_value < 0.0
            or retest_tolerance_value >= 1.0
        ):
            raise ValueError("retest_tolerance은 0 이상 1 미만의 유한한 숫자여야 합니다.")
        max_retest_days_value = int(max_retest_days)
        if max_retest_days_value <= 0:
            raise ValueError("max_retest_days는 1 이상이어야 합니다.")
        breakout_amount_window_value = int(breakout_amount_window)
        if breakout_amount_window_value <= 0:
            raise ValueError("breakout_amount_window는 1 이상이어야 합니다.")
        breakout_amount_threshold_value = None
        if breakout_amount_threshold is not None:
            breakout_amount_threshold_value = float(breakout_amount_threshold)
            if (
                not np.isfinite(breakout_amount_threshold_value)
                or breakout_amount_threshold_value <= 0.0
            ):
                raise ValueError("breakout_amount_threshold는 0보다 큰 유한한 숫자여야 합니다.")
        rebound_confirm_text = str(rebound_confirm or "close_up").strip().lower()
        if rebound_confirm_text != "close_up":
            raise ValueError("rebound_confirm은 현재 'close_up'만 지원합니다.")

        self.params = SimpleNamespace(
            breakout_window=breakout_window_value,
            retest_tolerance=retest_tolerance_value,
            max_retest_days=max_retest_days_value,
            breakout_amount_threshold=breakout_amount_threshold_value,
            breakout_amount_window=breakout_amount_window_value,
            rebound_confirm=rebound_confirm_text,
            stay_days=int(max(1, stay_days)),
            cooldown_days=int(max(0, cooldown_days)),
        )
        return self

    def _required_stock_fields(self) -> tuple[str, ...]:
        if self.params is None:
            return ()
        if self.params.breakout_amount_threshold is None:
            return ()
        return ("amount",)

    def _base_mask(self, values: np.ndarray) -> np.ndarray:
        if self.params is None:
            raise ValueError("RetestBreakout은 사용 전에 on(...)으로 설정해야 합니다.")

        prices = np.asarray(values, dtype=np.float64)
        n = prices.shape[0]
        out = np.zeros(n, dtype=np.bool_)
        window = int(self.params.breakout_window)
        if n <= window:
            return out

        rolling_high = u.rolling_high(prices, window)
        amount_ratio = None
        amount_ratio_valid = None
        if self.params.breakout_amount_threshold is not None:
            amount = self._get_stock_values("amount")
            if amount.shape != prices.shape:
                raise ValueError("RetestBreakout amount shape이 가격 시계열과 일치하지 않습니다.")
            mean_amount, valid_end = u.rolling_mean(amount, int(self.params.breakout_amount_window))
            amount_ratio_valid = (
                valid_end
                & np.isfinite(amount)
                & (amount > 0.0)
                & np.isfinite(mean_amount)
                & (mean_amount > 0.0)
            )
            amount_ratio = np.zeros(n, dtype=np.float64)
            amount_ratio[amount_ratio_valid] = amount[amount_ratio_valid] / mean_amount[amount_ratio_valid]

        active_level = np.nan
        breakout_idx = -1
        retest_seen = False
        lower_bound = np.nan

        for i in range(1, n):
            price = prices[i]
            prev_price = prices[i - 1]
            if not (np.isfinite(price) and price > 0.0):
                continue

            prior_high = rolling_high[i - 1]
            if np.isfinite(prior_high) and prior_high > 0.0 and price > prior_high:
                if self.params.breakout_amount_threshold is not None:
                    if not bool(amount_ratio_valid[i]):
                        continue
                    if float(amount_ratio[i]) < float(self.params.breakout_amount_threshold):
                        continue
                active_level = prior_high
                breakout_idx = i
                lower_bound = active_level * (1.0 - float(self.params.retest_tolerance))
                retest_seen = False
                continue

            if not np.isfinite(active_level):
                continue

            if breakout_idx >= 0 and (i - breakout_idx) > int(self.params.max_retest_days):
                active_level = np.nan
                breakout_idx = -1
                lower_bound = np.nan
                retest_seen = False
                continue

            if price < lower_bound:
                active_level = np.nan
                breakout_idx = -1
                lower_bound = np.nan
                retest_seen = False
                continue

            if not retest_seen:
                in_retest_zone = price >= lower_bound and price <= active_level
                if in_retest_zone and np.isfinite(prev_price) and prev_price > 0.0 and price <= prev_price:
                    retest_seen = True
                continue

            if (
                np.isfinite(prev_price)
                and prev_price > 0.0
                and price > prev_price
                and price >= active_level
            ):
                out[i] = True
                active_level = np.nan
                breakout_idx = -1
                lower_bound = np.nan
                retest_seen = False

        return u.stay_cooldown_mask(out, self.params.stay_days, self.params.cooldown_days)


__all__ = ["RetestBreakout"]
