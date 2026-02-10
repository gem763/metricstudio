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
        default_name: str = "pattern",
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

    def high(self, window: int, threshold: float = 0.9):
        w = int(window)
        t = float(threshold)
        return self._chain_post_mask(lambda prices, _w=w, _t=t: u.high_mask(prices, _w, _t))

    def uptrend(self, window: int):
        w = int(window)
        return self._chain_post_mask(lambda prices, _w=w: u.uptrend_mask(prices, _w))

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
        raise NotImplementedError


class Default(Pattern):
    def __init__(
        self,
        name: str = "default",
        trim: float | None = None,
    ):
        super().__init__(
            name=name,
            default_name="default",
            trim=trim,
        )

    def _base_mask(self, values: np.ndarray) -> np.ndarray:
        prices = np.asarray(values, dtype=np.float64)
        return np.isfinite(prices) & (prices > 0)


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
            "top_break",
            "bottom_break",
            "top_close",
            "bottom_close",
        ] = "top_break",
        cooldown_days: int = 3,
        proximity_tolerance: float = 0.03,
        proximity_stay_days: int = 3,
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
        self.trigger = (trigger or "top_break").lower()
        self.cooldown_days = int(max(0, cooldown_days))
        self.proximity_tolerance = float(proximity_tolerance)
        self.proximity_stay_days = int(max(1, proximity_stay_days))

        if self.bandwidth_type not in {"absolute", "percentile"}:
            raise ValueError("bandwidth_type must be 'absolute' or 'percentile'")
        if self.trigger not in {"top_break", "bottom_break", "top_close", "bottom_close"}:
            raise ValueError(
                "trigger must be one of {'top_break', 'bottom_break', 'top_close', 'bottom_close'}."
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

        if self.trigger.startswith("top_"):
            trigger_line = upper
            direction = 1
        elif self.trigger.startswith("bottom_"):
            trigger_line = lower
            direction = -1
        else:
            raise ValueError(f"unsupported trigger side: {self.trigger}")

        if self.trigger.endswith("_close"):
            return u.proximity_mask(
                prices,
                trigger_line,
                mask,
                self.proximity_tolerance,
                self.proximity_stay_days,
                direction,
            )

        if self.trigger.endswith("_break"):
            return u.breakout_mask(
                prices,
                trigger_line,
                mask,
                direction,
                self.cooldown_days,
            )

        raise ValueError(f"unsupported trigger kind: {self.trigger}")

__all__ = ["Default", "Bollinger"]
