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
        self._post_mask_steps: list[tuple[str, tuple[object, ...]]] = []

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
        step_name: str | None = None,
        step_args: tuple[object, ...] = (),
    ):
        prev_fn = self._post_mask_fn

        def _composed(prices: np.ndarray) -> np.ndarray:
            prev_mask = np.asarray(prev_fn(prices), dtype=np.bool_)
            step_mask = np.asarray(step_fn(prices), dtype=np.bool_)
            return prev_mask & step_mask

        self._post_mask_fn = _composed
        if step_name is not None:
            self._post_mask_steps.append((str(step_name), tuple(step_args)))
        return self

    def high(self, window: int, threshold: float = 0.9):
        w = int(window)
        t = float(threshold)
        return self._chain_post_mask(
            lambda prices, _w=w, _t=t: u.high_mask(prices, _w, _t),
            step_name="high",
            step_args=(w, t),
        )

    def uptrend(self, window: int):
        w = int(window)
        return self._chain_post_mask(
            lambda prices, _w=w: u.uptrend_mask(prices, _w),
            step_name="uptrend",
            step_args=(w,),
        )

    @staticmethod
    def _freeze_for_key(value: object):
        if isinstance(value, (str, int, float, bool, type(None))):
            return value
        if isinstance(value, np.generic):
            return value.item()
        if isinstance(value, (list, tuple)):
            return tuple(Pattern._freeze_for_key(v) for v in value)
        if isinstance(value, dict):
            return tuple(sorted((str(k), Pattern._freeze_for_key(v)) for k, v in value.items()))
        return repr(value)

    def execution_key(self):
        attrs = []
        for key, value in vars(self).items():
            if key in {"name", "__name__", "trim", "_post_mask_fn"}:
                continue
            attrs.append((str(key), self._freeze_for_key(value)))
        attrs.sort(key=lambda item: item[0])
        return (
            self.__class__.__module__,
            self.__class__.__qualname__,
            tuple(attrs),
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
        raise NotImplementedError


class Default(Pattern):
    def __init__(self, name: str = "default", trim: float | None = None):
        super().__init__(name=name, default_name="default", trim=trim)

    def _base_mask(self, values: np.ndarray) -> np.ndarray:
        prices = np.asarray(values, dtype=np.float64)
        return np.isfinite(prices) & (prices > 0)


class Bollinger(Pattern):
    def __init__(
        self,
        window: int = 20,
        sigma: float = 2.0,
        narrow_width: float = 1.0,
        narrow_stay_days: int = 1,
        narrow_width_type: Literal["absolute", "percentile"] = "absolute",
        narrow_percentile_window: int = 252,
        trigger: Literal["breakout", "topclose"] = "breakout",
        trigger_cooldown_days: int = 3,
        trigger_topclose_tolerance: float = 0.03,
        trigger_topclose_stay_days: int = 3,
        name: str | None = None,
        trim: float | None = None,
    ):
        super().__init__(name=name, default_name="bollinger", trim=trim)
        self.window = int(window)
        self.sigma = float(sigma)
        self.narrow_width = float(narrow_width)
        self.narrow_stay_days = int(max(1, narrow_stay_days))
        self.narrow_width_type = (narrow_width_type or "absolute").lower()
        self.narrow_percentile_window = int(max(1, narrow_percentile_window))
        self.trigger = (trigger or "breakout").lower()
        self.trigger_cooldown_days = int(max(0, trigger_cooldown_days))
        self.trigger_topclose_tolerance = float(trigger_topclose_tolerance)
        self.trigger_topclose_stay_days = int(max(1, trigger_topclose_stay_days))

        if self.narrow_width_type not in {"absolute", "percentile"}:
            raise ValueError("narrow_width_type must be 'absolute' or 'percentile'")
        if self.trigger not in {"breakout", "topclose"}:
            raise ValueError("trigger must be 'breakout' or 'topclose'")

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
        mask = valid_end.copy()

        mode = 0 if self.narrow_width_type == "absolute" else 1
        mask &= u.narrow_mask(
            mean,
            band_width,
            valid_end,
            self.narrow_width,
            mode,
            self.narrow_percentile_window,
            self.narrow_stay_days,
        )

        trigger_mode = 0 if self.trigger == "breakout" else 1
        return u.trigger_mask(
            prices,
            upper,
            mask,
            trigger_mode,
            self.trigger_cooldown_days,
            self.trigger_topclose_tolerance,
            self.trigger_topclose_stay_days,
        )

__all__ = ["Default", "Bollinger"]
