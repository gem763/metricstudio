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
        self._stock_values: dict[str, np.ndarray | None] = {}
        self._cached_exit_mask_key: tuple[object, ...] | None = None
        self._cached_exit_mask_value: np.ndarray | None = None
        self.params: SimpleNamespace | None = None
        self._post_mask_fn: Callable[[np.ndarray], np.ndarray] = self._post_mask_base

    @staticmethod
    def _normalize_loss_cut(loss_cut: str | None) -> str | None:
        if loss_cut is None:
            return None
        loss_cut_text = str(loss_cut).strip().lower().replace("-", "_").replace(" ", "_")
        if loss_cut_text not in {"mid_stop", "trailing_stop"}:
            raise ValueError("loss_cut은 현재 'mid_stop' 또는 'trailing_stop'만 지원합니다.")
        return loss_cut_text

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

    def _set_stock_values(self, field: str, values: np.ndarray | None) -> None:
        self._stock_values[str(field).strip().lower()] = values

    def _get_stock_values(self, field: str) -> np.ndarray:
        key = str(field).strip().lower()
        values = self._stock_values.get(key)
        if values is None:
            raise ValueError(
                f"패턴 '{self.name}'의 stock field='{key}' 데이터가 준비되지 않았습니다."
            )
        return np.asarray(values, dtype=np.float64)

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

    def exit_mask(self, values: np.ndarray) -> np.ndarray:
        return self._get_cached_exit_mask(values)

    def __add__(self, other: "Pattern"):
        if not isinstance(other, Pattern):
            return NotImplemented
        return CombinedPattern(self, other)

    def _base_mask(self, values: np.ndarray) -> np.ndarray:
        prices = np.asarray(values, dtype=np.float64)
        return np.isfinite(prices) & (prices > 0)

    def _required_stock_fields(self) -> tuple[str, ...]:
        return ()

    def _exit_mask(self, values: np.ndarray) -> np.ndarray:
        prices = np.asarray(values, dtype=np.float64)
        return np.zeros(prices.shape[0], dtype=np.bool_)

    def _get_cached_exit_mask(self, values: np.ndarray) -> np.ndarray:
        prices = np.asarray(values, dtype=np.float64)
        stock_keys = tuple(
            (field, id(arr) if arr is not None else None)
            for field, arr in sorted(self._stock_values.items())
        )
        cache_key = (
            id(prices),
            id(self._market_values) if self._market_values is not None else None,
            stock_keys,
        )
        if self._cached_exit_mask_key != cache_key or self._cached_exit_mask_value is None:
            exit_mask = np.asarray(self._exit_mask(prices), dtype=np.bool_)
            if exit_mask.shape != prices.shape:
                raise ValueError(f"패턴 '{self.name}'의 exit mask shape이 일치하지 않습니다.")
            self._cached_exit_mask_key = cache_key
            self._cached_exit_mask_value = exit_mask
        return self._cached_exit_mask_value

    def has_exit_rule(self) -> bool:
        return False

    def has_entry_dependent_exit(self) -> bool:
        return False

    def first_exit_index(
        self,
        values: np.ndarray,
        entry_idx: int,
        last_idx: int,
    ) -> int:
        if not self.has_exit_rule():
            return -1
        exit_mask = self._get_cached_exit_mask(values)
        lo = max(0, int(entry_idx) + 1)
        hi = min(len(exit_mask), int(last_idx) + 1)
        if hi <= lo:
            return -1
        hits = np.flatnonzero(exit_mask[lo:hi])
        if hits.size == 0:
            return -1
        return int(lo + hits[0])


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

    def _exit_mask(self, values: np.ndarray) -> np.ndarray:
        left_exit = np.asarray(self.left.exit_mask(values), dtype=np.bool_)
        right_exit = np.asarray(self.right.exit_mask(values), dtype=np.bool_)
        if left_exit.shape != right_exit.shape:
            raise ValueError("결합 패턴의 exit mask shape이 일치하지 않습니다.")
        return left_exit | right_exit

    def has_exit_rule(self) -> bool:
        return self.left.has_exit_rule() or self.right.has_exit_rule()

    def has_entry_dependent_exit(self) -> bool:
        return self.left.has_entry_dependent_exit() or self.right.has_entry_dependent_exit()

    def first_exit_index(
        self,
        values: np.ndarray,
        entry_idx: int,
        last_idx: int,
    ) -> int:
        left_idx = self.left.first_exit_index(values, entry_idx, last_idx)
        right_idx = self.right.first_exit_index(values, entry_idx, last_idx)
        if left_idx < 0:
            return right_idx
        if right_idx < 0:
            return left_idx
        return min(left_idx, right_idx)


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


class MFI(Pattern):
    def __init__(
        self,
        window: int = 14,
        name: str | None = None,
    ):
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
        bandwidth_percentile_window: int = 240,
        breakout_cooldown_days: int = 0,
        near_tolerance: float = 0.03,
        near_stay_days: int = 1,
        loss_cut: Literal["mid_stop", "trailing_stop"] | None = None,
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
            loss_cut=self._normalize_loss_cut(loss_cut),
        )
        return self

    def _required_stock_fields(self) -> tuple[str, ...]:
        if self.params is None:
            return ()
        loss_cut = getattr(self.params, "loss_cut", None)
        if loss_cut == "trailing_stop":
            return ("high", "low")
        return ()

    def _get_trailing_atr(
        self,
        prices: np.ndarray,
    ) -> tuple[np.ndarray, np.ndarray]:
        high = self._get_stock_values("high")
        low = self._get_stock_values("low")
        if high.shape != prices.shape or low.shape != prices.shape:
            raise ValueError("Bollinger trailing_stop 입력 시계열 shape이 일치하지 않습니다.")

        cache_key = (id(prices), id(high), id(low))
        cached_key = getattr(self, "_cached_trailing_atr_key", None)
        cached_value = getattr(self, "_cached_trailing_atr_value", None)
        if cached_key != cache_key or cached_value is None:
            cached_value = u.average_true_range(high, low, prices, self.window)
            self._cached_trailing_atr_key = cache_key
            self._cached_trailing_atr_value = cached_value
        return cached_value

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

    def _exit_mask(self, values: np.ndarray) -> np.ndarray:
        prices = np.asarray(values, dtype=np.float64)
        out = np.zeros(prices.shape[0], dtype=np.bool_)
        if self.params is None:
            raise ValueError("Bollinger는 사용 전에 on(...)으로 설정해야 합니다.")

        loss_cut = getattr(self.params, "loss_cut", None)
        if loss_cut is None:
            return out
        if loss_cut == "mid_stop":
            if self.window <= 0 or prices.shape[0] < self.window:
                return out

            mean, valid_end = u.rolling_mean(prices, self.window)
            valid = valid_end & np.isfinite(prices) & (prices > 0.0) & np.isfinite(mean)
            out[valid] = prices[valid] < mean[valid]
            return out
        if loss_cut == "trailing_stop":
            return out
        else:
            raise ValueError(f"지원하지 않는 Bollinger loss_cut 입니다: {loss_cut}")

    def has_exit_rule(self) -> bool:
        return self.params is not None and getattr(self.params, "loss_cut", None) is not None

    def has_entry_dependent_exit(self) -> bool:
        return self.params is not None and getattr(self.params, "loss_cut", None) == "trailing_stop"

    def first_exit_index(
        self,
        values: np.ndarray,
        entry_idx: int,
        last_idx: int,
    ) -> int:
        prices = np.asarray(values, dtype=np.float64)
        if self.params is None:
            raise ValueError("Bollinger는 사용 전에 on(...)으로 설정해야 합니다.")

        loss_cut = getattr(self.params, "loss_cut", None)
        if loss_cut != "trailing_stop":
            return super().first_exit_index(prices, entry_idx, last_idx)
        if self.window <= 0 or prices.shape[0] <= self.window:
            return -1

        atr, atr_valid_end = self._get_trailing_atr(prices)
        return int(
            u.trailing_stop_first_exit_index(
                prices,
                atr,
                atr_valid_end,
                int(entry_idx),
                int(last_idx),
                3.0,
            )
        )


__all__ = [
    "Pattern",
    "High",
    "Disparity",
    "MFI",
    "Trending",
    "GoldenCross",
    "Bollinger",
]
