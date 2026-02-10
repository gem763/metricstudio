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

    def high(
        self,
        window: int,
        threshold: float = 0.9,
        stay_days: int = 1,
        cooldown_days: int = 0,
    ):
        return self._chain_post_mask(
            High(
                window=window,
                threshold=threshold,
                stay_days=stay_days,
                cooldown_days=cooldown_days,
            )
        )

    def uptrend(self, window: int, stay_days: int = 1, cooldown_days: int = 0):
        w = int(window)
        s = int(max(1, stay_days))
        c = int(max(0, cooldown_days))
        return self._chain_post_mask(
            lambda prices, _w=w, _s=s, _c=c: u.uptrend_mask(prices, _w, _s, _c)
        )

    def moving_average(
        self,
        window: int = 20,
        trigger: Literal["break_up", "break_down"] = "break_up",
        cooldown_days: int = 3,
    ):
        return self._chain_post_mask(
            MovingAverage(
                window=window,
                trigger=trigger,
                cooldown_days=cooldown_days,
            )
        )

    def golden_cross(
        self,
        windows: list[int] | tuple[int, ...] = (5, 10, 20),
        cooldown_days: int = 3,
    ):
        return self._chain_post_mask(
            GoldenCross(
                windows=windows,
                cooldown_days=cooldown_days,
            )
        )

    def bollinger(
        self,
        window: int = 20,
        sigma: float = 2.0,
        bandwidth: float = 1.0,
        bandwidth_stay_days: int = 1,
        bandwidth_type: Literal["absolute", "percentile"] = "absolute",
        bandwidth_percentile_window: int = 252,
        trigger: Literal[
            "break_up",
            "break_down",
            "approach_up",
            "approach_down",
        ] = "break_up",
        cooldown_days: int = 3,
        approach_tolerance: float = 0.03,
        approach_stay_days: int = 3,
    ):
        return self._chain_post_mask(
            Bollinger(
                window=window,
                sigma=sigma,
                bandwidth=bandwidth,
                bandwidth_stay_days=bandwidth_stay_days,
                bandwidth_type=bandwidth_type,
                bandwidth_percentile_window=bandwidth_percentile_window,
                trigger=trigger,
                cooldown_days=cooldown_days,
                approach_tolerance=approach_tolerance,
                approach_stay_days=approach_stay_days,
            )
        )

    def __call__(self, values: np.ndarray) -> np.ndarray:
        prices = np.asarray(values, dtype=np.float64)
        base_mask = np.asarray(self._base_mask(prices), dtype=np.bool_)
        if base_mask.shape != prices.shape:
            raise ValueError(f"mask shape mismatch in pattern '{self.__name__}'")
        post_mask = np.asarray(self._post_mask_fn(prices), dtype=np.bool_)
        if post_mask.shape != prices.shape:
            raise ValueError(f"post mask shape mismatch in pattern '{self.__name__}'")
        return base_mask & post_mask

    def _base_mask(self, values: np.ndarray) -> np.ndarray:
        prices = np.asarray(values, dtype=np.float64)
        return np.isfinite(prices) & (prices > 0)


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
        return u.high_mask(
            prices,
            self.window,
            self.threshold,
            self.stay_days,
            self.cooldown_days,
        )


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
        return u.break_mask(
            prices,
            ma,
            valid_end,
            direction,
            self.cooldown_days,
        )


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
        bandwidth: float = 1.0,
        bandwidth_stay_days: int = 1,
        bandwidth_type: Literal["absolute", "percentile"] = "absolute",
        bandwidth_percentile_window: int = 252,
        trigger: Literal[
            "break_up",
            "break_down",
            "approach_up",
            "approach_down",
        ] = "break_up",
        cooldown_days: int = 3,
        approach_tolerance: float = 0.03,
        approach_stay_days: int = 3,
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
        self.bandwidth = float(bandwidth)
        self.bandwidth_stay_days = int(max(1, bandwidth_stay_days))
        self.bandwidth_type = (bandwidth_type or "absolute").lower()
        self.bandwidth_percentile_window = int(max(1, bandwidth_percentile_window))
        self.trigger = (trigger or "break_up").lower()
        self.cooldown_days = int(max(0, cooldown_days))
        self.approach_tolerance = float(approach_tolerance)
        self.approach_stay_days = int(max(1, approach_stay_days))

        if self.bandwidth_type not in {"absolute", "percentile"}:
            raise ValueError("bandwidth_type must be 'absolute' or 'percentile'")
        if self.trigger not in {"break_up", "break_down", "approach_up", "approach_down"}:
            raise ValueError(
                "trigger must be one of {'break_up', 'break_down', 'approach_up', 'approach_down'}."
            )

    def _base_mask(self, values: np.ndarray) -> np.ndarray:
        prices = np.asarray(values, dtype=np.float64)
        n = prices.shape[0]
        mask = np.zeros(n, dtype=bool)

        if self.window <= 0 or n < self.window:
            return mask

        mean, std, valid_end = u.rolling_mean_std(prices, self.window)
        if not np.any(valid_end):
            return mask

        band_width = self.sigma * std
        upper = mean + band_width
        lower = mean - band_width
        mask = valid_end.copy()

        mode = 0 if self.bandwidth_type == "absolute" else 1
        mask &= u.bandwidth_mask(
            mean,
            band_width,
            valid_end,
            self.bandwidth,
            mode,
            self.bandwidth_percentile_window,
            self.bandwidth_stay_days,
        )

        if self.trigger.endswith("_up"):
            trigger_line = upper
            direction = 1
        elif self.trigger.endswith("_down"):
            trigger_line = lower
            direction = -1
        else:
            raise ValueError(f"unsupported trigger side: {self.trigger}")

        if self.trigger.startswith("approach_"):
            return u.approach_mask(
                prices,
                trigger_line,
                mask,
                self.approach_tolerance,
                self.approach_stay_days,
                direction,
            )

        if self.trigger.startswith("break_"):
            return u.break_mask(
                prices,
                trigger_line,
                mask,
                direction,
                self.cooldown_days,
            )

        raise ValueError(f"unsupported trigger kind: {self.trigger}")

__all__ = ["Pattern", "High", "MovingAverage", "GoldenCross", "Bollinger"]
