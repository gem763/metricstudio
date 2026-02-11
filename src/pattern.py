"""Pattern classes for numpy price arrays."""

from __future__ import annotations
from typing import Callable, Literal

import numpy as np

from src import util as u


class Pattern:
    @staticmethod
    def _post_mask_base(prices: np.ndarray) -> np.ndarray:
        return np.ones(prices.shape[0], dtype=np.bool_)

    def __init__(
        self,
        name: str | None = None,
        default_name: str = "default",
        trim: float | None = None,
    ):
        self.name = name or default_name
        self.__name__ = self.name
        self.trim = self._normalize_trim(trim)
        self._post_mask_fn: Callable[[np.ndarray], np.ndarray] = self._post_mask_base

    @staticmethod
    def _normalize_trim(trim: float | None) -> float | None:
        if trim is None:
            return None
        value = float(trim)
        if not np.isfinite(value) or value < 0.0 or value >= 0.5:
            raise ValueError("trim must be in [0.0, 0.5).")
        return value

    def _chain_post_mask(
        self,
        step_fn: Callable[[np.ndarray], np.ndarray],
    ):
        prev_fn = self._post_mask_fn

        def _composed(prices: np.ndarray) -> np.ndarray:
            prev_mask = np.asarray(prev_fn(prices), dtype=np.bool_)
            step_mask = np.asarray(step_fn(prices), dtype=np.bool_)
            return prev_mask & step_mask

        self._post_mask_fn = _composed
        return self

    def __call__(self, values: np.ndarray) -> np.ndarray:
        prices = np.asarray(values, dtype=np.float64)
        base_mask = np.asarray(self._base_mask(prices), dtype=np.bool_)
        if base_mask.shape != prices.shape:
            raise ValueError(f"mask shape mismatch in pattern '{self.__name__}'")
        post_mask = np.asarray(self._post_mask_fn(prices), dtype=np.bool_)
        if post_mask.shape != prices.shape:
            raise ValueError(f"post mask shape mismatch in pattern '{self.__name__}'")
        return base_mask & post_mask

    def __add__(self, other: "Pattern"):
        if not isinstance(other, Pattern):
            return NotImplemented
        return CombinedPattern(self, other)

    def _base_mask(self, values: np.ndarray) -> np.ndarray:
        prices = np.asarray(values, dtype=np.float64)
        return np.isfinite(prices) & (prices > 0)


class CombinedPattern(Pattern):
    def __init__(
        self,
        left: Pattern,
        right: Pattern,
        name: str | None = None,
    ):
        self.left = left
        self.right = right
        trim = self._resolve_trim(left.trim, right.trim)
        left_name = left.name if isinstance(left.name, str) and left.name else "left_pattern"
        right_name = right.name if isinstance(right.name, str) and right.name else "right_pattern"
        resolved_name = name or f"{left_name} + {right_name}"
        super().__init__(
            name=resolved_name,
            default_name="combined_pattern",
            trim=trim,
        )

    @staticmethod
    def _resolve_trim(left_trim: float | None, right_trim: float | None) -> float | None:
        if left_trim is None:
            return right_trim
        if right_trim is None:
            return left_trim
        if float(left_trim) == float(right_trim):
            return left_trim
        raise ValueError(
            "trim 값이 서로 다른 패턴은 결합할 수 없습니다. "
            "양쪽 trim을 동일하게 맞추거나 한쪽에만 trim을 설정하세요."
        )

    def _base_mask(self, values: np.ndarray) -> np.ndarray:
        left_mask = np.asarray(self.left(values), dtype=np.bool_)
        right_mask = np.asarray(self.right(values), dtype=np.bool_)
        if left_mask.shape != right_mask.shape:
            raise ValueError("combined pattern mask shape mismatch")
        return left_mask & right_mask


class High(Pattern):
    def __init__(
        self,
        window: int,
        threshold: float = 0.9,
        stay_days: int = 1,
        cooldown_days: int = 0,
        name: str | None = None,
        trim: float | None = None,
    ):
        super().__init__(
            name=name,
            default_name="high",
            trim=trim,
        )
        self.window = int(window)
        self.threshold = float(threshold)
        self.stay_days = int(max(1, stay_days))
        self.cooldown_days = int(max(0, cooldown_days))

    def _base_mask(self, values: np.ndarray) -> np.ndarray:
        prices = np.asarray(values, dtype=np.float64)
        cond = u.high_mask(
            prices,
            self.window,
            self.threshold,
        )
        return u.cooldown_stay_mask(cond, self.stay_days, self.cooldown_days)


class MovingAverage(Pattern):
    def __init__(
        self,
        window: int = 20,
        trigger: Literal["break_up", "break_down"] = "break_up",
        cooldown_days: int = 3,
        name: str | None = None,
        trim: float | None = None,
    ):
        super().__init__(
            name=name,
            default_name="moving_average",
            trim=trim,
        )
        self.window = int(window)
        self.trigger = (trigger or "break_up").lower()
        self.cooldown_days = int(max(0, cooldown_days))

        if self.window <= 0:
            raise ValueError("window must be positive")
        if self.trigger not in {"break_up", "break_down"}:
            raise ValueError("trigger must be one of {'break_up', 'break_down'}.")

    def _base_mask(self, values: np.ndarray) -> np.ndarray:
        prices = np.asarray(values, dtype=np.float64)
        n = prices.shape[0]
        mask = np.zeros(n, dtype=np.bool_)

        if n < self.window:
            return mask

        ma, valid_end = u.rolling_mean(prices, self.window)
        if not np.any(valid_end):
            return mask

        direction = 1 if self.trigger == "break_up" else -1
        out = u.breakout_mask(
            prices,
            ma,
            valid_end,
            direction,
        )
        return u.cooldown_stay_mask(out, 1, self.cooldown_days)


class GoldenCross(Pattern):
    def __init__(
        self,
        windows: list[int] | tuple[int, ...] = (5, 10, 20),
        cooldown_days: int = 3,
        name: str | None = None,
        trim: float | None = None,
    ):
        super().__init__(
            name=name,
            default_name="golden_cross",
            trim=trim,
        )
        ws = tuple(int(w) for w in windows)
        if len(ws) < 2:
            raise ValueError("windows must contain at least two values.")
        if any(w <= 0 for w in ws):
            raise ValueError("all windows must be positive.")
        if any(ws[i] >= ws[i + 1] for i in range(len(ws) - 1)):
            raise ValueError("windows must be strictly increasing (e.g. [5, 10, 20]).")

        self.windows = ws
        self.cooldown_days = int(max(0, cooldown_days))

    def _base_mask(self, values: np.ndarray) -> np.ndarray:
        prices = np.asarray(values, dtype=np.float64)
        n = prices.shape[0]
        out = np.zeros(n, dtype=np.bool_)
        if n == 0:
            return out

        valid = np.ones(n, dtype=np.bool_)
        means: list[np.ndarray] = []
        for window in self.windows:
            mean, valid_end = u.rolling_mean(prices, window)
            means.append(mean)
            valid &= valid_end

        cond = valid.copy()
        for i in range(len(means) - 1):
            cond &= means[i] > means[i + 1]

        return u.cooldown_stay_mask(cond, 1, self.cooldown_days)


class Bollinger(Pattern):
    def __init__(
        self,
        window: int = 20,
        sigma: float = 2.0,
        trigger: Literal[
            "breakout_up",
            "breakout_down",
            "near_up",
            "near_down",
        ] = "breakout_up",
        bandwidth_limit: float = 1.0,
        bandwidth_stay_days: int = 1,
        bandwidth_type: Literal["absolute", "percentile"] = "absolute",
        bandwidth_percentile_window: int = 252,
        breakout_cooldown_days: int = 3,
        near_tolerance: float = 0.03,
        near_stay_days: int = 3,
        name: str | None = None,
        trim: float | None = None,
    ):
        super().__init__(
            name=name,
            default_name="bollinger",
            trim=trim,
        )
        self.window = int(window)
        self.sigma = float(sigma)
        self.trigger = (trigger or "breakout_up").lower()
        self.bandwidth_limit = float(bandwidth_limit)
        self.bandwidth_stay_days = int(max(1, bandwidth_stay_days))
        self.bandwidth_type = (bandwidth_type or "absolute").lower()
        self.bandwidth_percentile_window = int(max(1, bandwidth_percentile_window))
        self.breakout_cooldown_days = int(max(0, breakout_cooldown_days))
        self.near_tolerance = float(near_tolerance)
        self.near_stay_days = int(max(1, near_stay_days))

        if self.bandwidth_type not in {"absolute", "percentile"}:
            raise ValueError("bandwidth_type must be 'absolute' or 'percentile'")
        if self.trigger not in {
            "breakout_up",
            "breakout_down",
            "near_up",
            "near_down",
        }:
            raise ValueError(
                "trigger must be one of "
                "{'breakout_up', 'breakout_down', 'near_up', 'near_down'}."
            )

    def _base_mask(self, values: np.ndarray) -> np.ndarray:
        prices = np.asarray(values, dtype=np.float64)
        n = prices.shape[0]
        mask = np.zeros(n, dtype=np.bool_)

        if self.window <= 0 or n < self.window:
            return mask

        mean, std, valid_end = u.rolling_mean_std(prices, self.window)
        if not np.any(valid_end):
            return mask

        band_width = self.sigma * std
        upper = mean + band_width
        lower = mean - band_width

        mode = 0 if self.bandwidth_type == "absolute" else 1
        band_cond = u.bandwidth_mask(
            mean,
            band_width,
            valid_end,
            self.bandwidth_limit,
            mode,
            self.bandwidth_percentile_window,
        )
        band_cond = u.stay_mask(band_cond, self.bandwidth_stay_days)
        band_mask = valid_end & band_cond

        if self.trigger in {"breakout_up", "breakout_down"}:
            trigger_line = upper if self.trigger == "breakout_up" else lower
            direction = 1 if self.trigger == "breakout_up" else -1
            out = u.breakout_mask(
                prices,
                trigger_line,
                band_mask,
                direction,
            )
            return u.cooldown_mask(out, self.breakout_cooldown_days)
        
        elif self.trigger in {"near_up", "near_down"}:
            trigger_line = upper if self.trigger == "near_up" else lower
            direction = 1 if self.trigger == "near_up" else -1
            out = u.near_mask(
                prices,
                trigger_line,
                band_mask,
                self.near_tolerance,
                direction,
            )
            return u.stay_mask(out, self.near_stay_days)

        raise ValueError(f"unsupported trigger kind: {self.trigger}")


__all__ = ["Pattern", "CombinedPattern", "High", "MovingAverage", "GoldenCross", "Bollinger"]
