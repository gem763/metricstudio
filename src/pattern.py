"""Pattern classes for numpy price arrays."""

from __future__ import annotations
from types import SimpleNamespace
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
    ):
        self.name = name or self.__class__.__name__.lower()
        self.trim_quantile: float | None = None
        self.trim_method: str = "remove"
        self.market_name: str | None = None
        self.market_field: str = "close"
        self._market_values: np.ndarray | None = None
        self.params: SimpleNamespace | None = None
        self._post_mask_fn: Callable[[np.ndarray], np.ndarray] = self._post_mask_base

    @staticmethod
    def _normalize_trim_quantile(quantile: float) -> float:
        value = float(quantile)
        if not np.isfinite(value) or value < 0.0 or value >= 0.5:
            raise ValueError("trim 값은 [0.0, 0.5) 범위여야 합니다.")
        return value

    @staticmethod
    def _normalize_trim_method(method: str) -> str:
        method_text = str(method or "remove").lower()
        if method_text not in {"remove", "winsorize"}:
            raise ValueError("trim method는 'remove' 또는 'winsorize'여야 합니다.")
        return method_text

    def trim(
        self,
        quantile: float | None,
        method: Literal["remove", "winsorize"] = "remove",
    ):
        if quantile is None:
            self.trim_quantile = None
            self.trim_method = "remove"
            return self

        self.trim_quantile = self._normalize_trim_quantile(quantile)
        self.trim_method = self._normalize_trim_method(method)
        return self

    @staticmethod
    def _normalize_market_field(field: str) -> str:
        field_text = str(field).strip().lower()
        valid_fields = {"open", "high", "low", "close", "volume", "amount", "marketcap"}
        if field_text not in valid_fields:
            raise ValueError(
                "market field는 {'open', 'high', 'low', 'close', 'volume', 'amount', 'marketcap'} 중 하나여야 합니다."
            )
        return field_text

    def market(self, market: str, field: str = "close"):
        market_name = str(market).strip().lower()
        if not market_name:
            raise ValueError("market 이름은 비어 있을 수 없습니다.")

        self.market_name = market_name
        self.market_field = self._normalize_market_field(field)
        self._market_values = None
        return self

    def _set_market_values(self, values: np.ndarray | None) -> None:
        self._market_values = values

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
        source_values = values
        if self.market_name is not None:
            if self._market_values is None:
                raise ValueError(
                    f"패턴 '{self.name}'의 market 데이터가 준비되지 않았습니다."
                )
            source_values = self._market_values

        prices = np.asarray(source_values, dtype=np.float64)
        base_mask = np.asarray(self._base_mask(prices), dtype=np.bool_)
        if base_mask.shape != prices.shape:
            raise ValueError(f"패턴 '{self.name}'의 mask shape이 일치하지 않습니다.")
        post_mask = np.asarray(self._post_mask_fn(prices), dtype=np.bool_)
        if post_mask.shape != prices.shape:
            raise ValueError(f"패턴 '{self.name}'의 후처리 mask shape이 일치하지 않습니다.")
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
        trim_quantile, trim_method = self._resolve_trim(
            left.trim_quantile,
            left.trim_method,
            right.trim_quantile,
            right.trim_method,
        )
        left_name = left.name if isinstance(left.name, str) and left.name else "left_pattern"
        right_name = right.name if isinstance(right.name, str) and right.name else "right_pattern"
        resolved_name = name or f"{left_name} + {right_name}"
        super().__init__(
            name=resolved_name,
        )
        if trim_quantile is not None:
            self.trim(trim_quantile, method=trim_method)

    @staticmethod
    def _resolve_trim(
        left_quantile: float | None,
        left_method: str,
        right_quantile: float | None,
        right_method: str,
    ) -> tuple[float | None, str]:
        if left_quantile is None and right_quantile is None:
            return None, "remove"
        if left_quantile is None:
            return right_quantile, right_method
        if right_quantile is None:
            return left_quantile, left_method
        if float(left_quantile) == float(right_quantile) and left_method == right_method:
            return left_quantile, left_method
        raise ValueError(
            "trim 설정이 서로 다른 패턴은 결합할 수 없습니다. "
            "양쪽 trim quantile/method를 동일하게 맞추거나 한쪽만 trim을 설정하세요."
        )

    def _base_mask(self, values: np.ndarray) -> np.ndarray:
        left_mask = np.asarray(self.left(values), dtype=np.bool_)
        right_mask = np.asarray(self.right(values), dtype=np.bool_)
        if left_mask.shape != right_mask.shape:
            raise ValueError("결합 패턴의 mask shape이 일치하지 않습니다.")
        return left_mask & right_mask


class High(Pattern):
    def on(
        self,
        window: int,
        threshold: float = 0.9,
        stay_days: int = 1,
        cooldown_days: int = 0,
    ):
        window_value = int(window)
        if window_value <= 0:
            raise ValueError("window는 1 이상이어야 합니다.")

        self.params = SimpleNamespace(
            window=window_value,
            threshold=float(threshold),
            stay_days=int(max(1, stay_days)),
            cooldown_days=int(max(0, cooldown_days)),
        )
        return self

    def _base_mask(self, values: np.ndarray) -> np.ndarray:
        if self.params is None:
            raise ValueError("High는 사용 전에 on(...)으로 설정해야 합니다.")

        prices = np.asarray(values, dtype=np.float64)
        cond = u.high_mask(
            prices,
            self.params.window,
            self.params.threshold,
        )
        return u.stay_cooldown_mask(cond, self.params.stay_days, self.params.cooldown_days)


class MovingAverage(Pattern):
    def on(
        self,
        window: int = 20,
        trigger: Literal["break_up", "break_down"] = "break_up",
        cooldown_days: int = 3,
    ):
        window_value = int(window)
        trigger_text = str(trigger or "break_up").lower()
        if window_value <= 0:
            raise ValueError("window는 1 이상이어야 합니다.")
        if trigger_text not in {"break_up", "break_down"}:
            raise ValueError("trigger는 {'break_up', 'break_down'} 중 하나여야 합니다.")

        self.params = SimpleNamespace(
            window=window_value,
            trigger=trigger_text,
            cooldown_days=int(max(0, cooldown_days)),
        )
        return self

    def _base_mask(self, values: np.ndarray) -> np.ndarray:
        if self.params is None:
            raise ValueError("MovingAverage는 사용 전에 on(...)으로 설정해야 합니다.")

        prices = np.asarray(values, dtype=np.float64)
        n = prices.shape[0]
        mask = np.zeros(n, dtype=np.bool_)

        if n < self.params.window:
            return mask

        ma, valid_end = u.rolling_mean(prices, self.params.window)
        if not np.any(valid_end):
            return mask

        direction = 1 if self.params.trigger == "break_up" else -1
        out = u.breakout_mask(
            prices,
            ma,
            valid_end,
            direction,
        )
        return u.stay_cooldown_mask(out, 1, self.params.cooldown_days)


class Disparity(Pattern):
    def __init__(
        self,
        window: int = 20,
        name: str | None = None,
    ):
        super().__init__(name=name)
        self.window = int(window)

    def on(
        self,
        threshold: float = 0.0,
        stay_days: int = 1,
        cooldown_days: int = 0,
    ):
        window_value = int(self.window)
        if window_value <= 0:
            raise ValueError("window는 1 이상이어야 합니다.")

        self.params = SimpleNamespace(
            threshold=float(threshold),
            stay_days=int(max(1, stay_days)),
            cooldown_days=int(max(0, cooldown_days)),
        )
        return self

    def _base_mask(self, values: np.ndarray) -> np.ndarray:
        if self.params is None:
            raise ValueError("Disparity는 사용 전에 on(...)으로 설정해야 합니다.")

        prices = np.asarray(values, dtype=np.float64)
        n = prices.shape[0]
        out = np.zeros(n, dtype=np.bool_)
        if n < self.window:
            return out

        ma, valid_end = u.rolling_mean(prices, self.window)
        valid = valid_end & np.isfinite(ma) & (ma > 0.0)
        if not np.any(valid):
            return out

        disparity = np.zeros(n, dtype=np.float64)
        disparity[valid] = prices[valid] / ma[valid]
        cond = valid & np.isfinite(prices) & (prices > 0.0) & (disparity < self.params.threshold)
        return u.stay_cooldown_mask(cond, self.params.stay_days, self.params.cooldown_days)


class Trending(Pattern):
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
        for i in range(1, n):
            if not (valid_end[i] and valid_end[i - 1]):
                continue
            if is_uptrend:
                out[i] = mean[i] > mean[i - 1]
            else:
                out[i] = mean[i] < mean[i - 1]
        return u.stay_cooldown_mask(out, self.params.stay_days, self.params.cooldown_days)


class GoldenCross(Pattern):
    def on(
        self,
        windows: list[int] | tuple[int, ...] = (5, 10, 20),
        stay_days: int = 1,
        cooldown_days: int = 3,
    ):
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


class Bollinger(Pattern):
    def __init__(
        self,
        window: int = 20,
        sigma: float = 2.0,
        name: str | None = None,
    ):
        super().__init__(
            name=name,
        )
        self.window = int(window)
        self.sigma = float(sigma)

    def on(
        self,
        trigger: Literal[
            "breakout_up",
            "breakout_down",
            "near_up",
            "near_down",
        ]
        | None = None,
        bandwidth_min: float = 0.0,
        bandwidth_max: float = 1.0,
        bandwidth_stay_days: int = 1,
        bandwidth_type: Literal["absolute", "percentile"] = "absolute",
        bandwidth_percentile_window: int = 252,
        breakout_cooldown_days: int = 0,
        near_tolerance: float = 0.03,
        near_stay_days: int = 1,
    ):
        trigger_text = None if trigger is None else str(trigger).lower()
        if trigger_text is not None and trigger_text not in {
            "breakout_up",
            "breakout_down",
            "near_up",
            "near_down",
        }:
            raise ValueError(
                "trigger는 다음 중 하나여야 합니다: "
                "{'breakout_up', 'breakout_down', 'near_up', 'near_down'}."
            )
        bandwidth_type_text = str(bandwidth_type or "absolute").lower()
        if bandwidth_type_text not in {"absolute", "percentile"}:
            raise ValueError("bandwidth_type은 'absolute' 또는 'percentile'이어야 합니다.")
        bandwidth_min_value = float(bandwidth_min)
        bandwidth_max_value = float(bandwidth_max)
        if bandwidth_min_value < 0.0:
            raise ValueError("bandwidth_min은 0 이상이어야 합니다.")
        if bandwidth_max_value < bandwidth_min_value:
            raise ValueError("bandwidth_max는 bandwidth_min 이상이어야 합니다.")

        self.params = SimpleNamespace(
            trigger=trigger_text,
            bandwidth_min=bandwidth_min_value,
            bandwidth_max=bandwidth_max_value,
            bandwidth_stay_days=int(max(1, bandwidth_stay_days)),
            bandwidth_type=bandwidth_type_text,
            bandwidth_percentile_window=int(max(1, bandwidth_percentile_window)),
            breakout_cooldown_days=int(max(0, breakout_cooldown_days)),
            near_tolerance=float(near_tolerance),
            near_stay_days=int(max(1, near_stay_days)),
        )
        return self

    def _base_mask(self, values: np.ndarray) -> np.ndarray:
        prices = np.asarray(values, dtype=np.float64)
        n = prices.shape[0]
        mask = np.zeros(n, dtype=np.bool_)

        if self.params is None:
            raise ValueError("Bollinger는 사용 전에 on(...)으로 설정해야 합니다.")

        if self.window <= 0 or n < self.window:
            return mask

        mean, std, valid_end = u.rolling_mean_std(prices, self.window)
        if not np.any(valid_end):
            return mask

        band_width = self.sigma * std
        upper = mean + band_width
        lower = mean - band_width

        params = self.params
        trigger = params.trigger
        bandwidth_min = params.bandwidth_min
        bandwidth_max = params.bandwidth_max
        bandwidth_stay_days = params.bandwidth_stay_days
        bandwidth_type = params.bandwidth_type
        bandwidth_percentile_window = params.bandwidth_percentile_window
        breakout_cooldown_days = params.breakout_cooldown_days
        near_tolerance = params.near_tolerance
        near_stay_days = params.near_stay_days

        mode = 0 if bandwidth_type == "absolute" else 1
        band_cond = u.bandwidth_mask(
            mean,
            band_width,
            valid_end,
            bandwidth_min,
            bandwidth_max,
            mode,
            bandwidth_percentile_window,
        )
        band_cond = u.stay_mask(band_cond, bandwidth_stay_days)
        band_mask = valid_end & band_cond

        if trigger in {"breakout_up", "breakout_down"}:
            trigger_line = upper if trigger == "breakout_up" else lower
            direction = 1 if trigger == "breakout_up" else -1
            out = u.breakout_mask(
                prices,
                trigger_line,
                band_mask,
                direction,
            )
            return u.cooldown_mask(out, breakout_cooldown_days)

        if trigger in {"near_up", "near_down"}:
            trigger_line = upper if trigger == "near_up" else lower
            direction = 1 if trigger == "near_up" else -1
            out = u.near_mask(
                prices,
                trigger_line,
                band_mask,
                near_tolerance,
                direction,
            )
            return u.stay_mask(out, near_stay_days)

        if trigger is None:
            return band_mask

        raise ValueError(f"지원하지 않는 trigger 종류입니다: {trigger}")


__all__ = [
    "Pattern",
    "CombinedPattern",
    "High",
    "MovingAverage",
    "Disparity",
    "Trending",
    "GoldenCross",
    "Bollinger",
]
