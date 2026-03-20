"""Base and composite pattern types."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Callable, Literal

import numpy as np

from metricstudio.regime import Regime

_UNSET = object()


class BasePattern:
    """
    단일 종목 시계열을 bool 진입 신호로 변환하는 기본 패턴 인터페이스.
    """

    @staticmethod
    def _post_mask_base(prices: np.ndarray) -> np.ndarray:
        return np.ones(prices.shape[0], dtype=np.bool_)

    def __init__(
        self,
        name: str | None = None,
    ):
        self.name = self._resolve_name(name)
        self.trim_quantile: float | None = None
        self.trim_method: str = "remove"
        self.market_name: str | None = None
        self.market_field: str = "close"
        self._market_values: np.ndarray | None = None
        self._stock_values: dict[str, np.ndarray | None] = {}
        self._cached_mask_key: tuple[object, ...] | None = None
        self._cached_mask_value: np.ndarray | None = None
        self._cached_exit_mask_key: tuple[object, ...] | None = None
        self._cached_exit_mask_value: np.ndarray | None = None
        self.params: SimpleNamespace | None = None
        self._post_mask_fn: Callable[[np.ndarray], np.ndarray] = self._post_mask_base
        self._regimes: list[Regime] = []
        self._trade_target_horizon: str | int | None = None
        self._trade_stop_loss_pct: float | None = None
        self._trade_take_profit_pct: float | None = None
        self._trade_cohort_scale: float | None = None
        self._max_cohort_size: int | None = None
        self._max_cohort_use_market_cap: bool = False

    def _resolve_name(self, value: str | None) -> str:
        if value is None:
            return self.__class__.__name__.lower()
        text = str(value).strip()
        if not text:
            raise ValueError("pattern name은 비어 있을 수 없습니다.")
        return text

    @staticmethod
    def _resolve_name_window_init_args(
        args: tuple[object, ...],
        kwargs: dict[str, object],
        *,
        default_window: int,
        class_name: str,
    ) -> tuple[object | None, object]:
        params = dict(kwargs)
        has_name = "name" in params
        has_window = "window" in params
        name = params.pop("name", None)
        window = params.pop("window", default_window)
        if params:
            unexpected = next(iter(params))
            raise TypeError(f"{class_name}() got an unexpected keyword argument '{unexpected}'")
        if len(args) > 2:
            raise TypeError(f"{class_name}() takes at most 2 positional arguments but {len(args)} were given")
        if not args:
            return name, window

        first = args[0]
        if isinstance(first, str) or first is None:
            if has_name:
                raise TypeError(f"{class_name}() got multiple values for argument 'name'")
            name = first
            if len(args) == 2:
                if has_window:
                    raise TypeError(f"{class_name}() got multiple values for argument 'window'")
                window = args[1]
            return name, window

        if has_window:
            raise TypeError(f"{class_name}() got multiple values for argument 'window'")
        window = first
        if len(args) == 2:
            if has_name:
                raise TypeError(f"{class_name}() got multiple values for argument 'name'")
            name = args[1]
        return name, window

    @staticmethod
    def _resolve_name_window_sigma_init_args(
        args: tuple[object, ...],
        kwargs: dict[str, object],
        *,
        default_window: int,
        default_sigma: float,
        class_name: str,
    ) -> tuple[object | None, object, object]:
        params = dict(kwargs)
        has_name = "name" in params
        has_window = "window" in params
        has_sigma = "sigma" in params
        name = params.pop("name", None)
        window = params.pop("window", default_window)
        sigma = params.pop("sigma", default_sigma)
        if params:
            unexpected = next(iter(params))
            raise TypeError(f"{class_name}() got an unexpected keyword argument '{unexpected}'")
        if len(args) > 3:
            raise TypeError(f"{class_name}() takes at most 3 positional arguments but {len(args)} were given")
        if not args:
            return name, window, sigma

        first = args[0]
        if isinstance(first, str) or first is None:
            if has_name:
                raise TypeError(f"{class_name}() got multiple values for argument 'name'")
            name = first
            if len(args) >= 2:
                if has_window:
                    raise TypeError(f"{class_name}() got multiple values for argument 'window'")
                window = args[1]
            if len(args) == 3:
                if has_sigma:
                    raise TypeError(f"{class_name}() got multiple values for argument 'sigma'")
                sigma = args[2]
            return name, window, sigma

        if has_window:
            raise TypeError(f"{class_name}() got multiple values for argument 'window'")
        window = first
        if len(args) >= 2:
            second = args[1]
            if len(args) == 2 and (isinstance(second, str) or second is None):
                if has_name:
                    raise TypeError(f"{class_name}() got multiple values for argument 'name'")
                name = second
                return name, window, sigma
            if has_sigma:
                raise TypeError(f"{class_name}() got multiple values for argument 'sigma'")
            sigma = second
        if len(args) == 3:
            if has_name:
                raise TypeError(f"{class_name}() got multiple values for argument 'name'")
            name = args[2]
        return name, window, sigma

    def named(self, value: str | None):
        """
        표시/집계에 사용할 패턴 이름을 지정한다.
        """

        self.name = self._resolve_name(value)
        return self

    @staticmethod
    def _normalize_max_cohort_size(value: int | None) -> int | None:
        if value is None:
            return None
        out = int(value)
        if out <= 0:
            raise ValueError("nmax 값은 1 이상의 정수 또는 None이어야 합니다.")
        return out

    def nmax(self, value: int | None, market_cap: bool = False):
        """
        코호트당 최대 편입 종목 수 제한을 설정한다.
        """

        if value is None:
            self._max_cohort_size = None
            self._max_cohort_use_market_cap = False
            return self
        self._max_cohort_size = self._normalize_max_cohort_size(value)
        self._max_cohort_use_market_cap = bool(market_cap)
        return self

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

    @staticmethod
    def _normalize_trade_horizon(value: str | int) -> str | int:
        if isinstance(value, str):
            text = str(value).strip()
            if not text:
                raise ValueError("target_horizon은 비어 있을 수 없습니다.")
            return text
        out = int(value)
        if out <= 0:
            raise ValueError("target_horizon은 양의 정수 또는 horizon 라벨이어야 합니다.")
        return out

    @staticmethod
    def _normalize_trade_pct(value: float | None, name: str) -> float | None:
        if value is None:
            return None
        out = float(value)
        if not np.isfinite(out) or out <= 0.0:
            raise ValueError(f"{name}는 양수여야 합니다.")
        if out >= 1.0:
            out = out / 100.0
        if out <= 0.0 or out >= 1.0:
            raise ValueError(f"{name}는 0~1(소수) 또는 1~100(%) 범위여야 합니다.")
        return out

    def trim(
        self,
        quantile: float | None,
        method: Literal["remove", "winsorize"] = "remove",
    ):
        """
        분석 집계 시 극단값 trim 규칙을 설정한다.
        """

        if quantile is None:
            self.trim_quantile = None
            self.trim_method = "remove"
            return self

        self.trim_quantile = self._normalize_trim_quantile(quantile)
        self.trim_method = self._normalize_trim_method(method)
        return self

    def trade(
        self,
        *,
        target_horizon: str | int | object = _UNSET,
        stop_loss_pct: float | None | object = _UNSET,
        take_profit_pct: float | None | object = _UNSET,
        cohort_scale: float | object = _UNSET,
    ):
        """
        패턴 branch에 귀속될 거래 정책을 설정한다.
        """

        if target_horizon is not _UNSET:
            self._trade_target_horizon = (
                None
                if target_horizon is None
                else self._normalize_trade_horizon(target_horizon)
            )
        if stop_loss_pct is not _UNSET:
            self._trade_stop_loss_pct = self._normalize_trade_pct(
                None if stop_loss_pct is None else float(stop_loss_pct),
                "stop_loss_pct",
            )
        if take_profit_pct is not _UNSET:
            self._trade_take_profit_pct = self._normalize_trade_pct(
                None if take_profit_pct is None else float(take_profit_pct),
                "take_profit_pct",
            )
        if cohort_scale is not _UNSET:
            self._trade_cohort_scale = self._normalize_trade_scale(
                None if cohort_scale is None else float(cohort_scale),
                "cohort_scale",
            )
        return self

    @staticmethod
    def _normalize_trade_scale(value: float | None, name: str) -> float | None:
        if value is None:
            return None
        out = float(value)
        if not np.isfinite(out) or out <= 0.0 or out > 1.0:
            raise ValueError(f"{name}는 0보다 크고 1 이하여야 합니다.")
        return out

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
        """
        종목 대신 시장 시계열을 기준으로 계산하도록 설정한다.
        """

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

    @staticmethod
    def _array_cache_token(values: np.ndarray | None) -> tuple[object, ...] | None:
        if values is None:
            return None
        arr = np.asarray(values)
        data_ptr = int(arr.__array_interface__["data"][0]) if arr.size else 0
        return (data_ptr, arr.shape, arr.strides, arr.dtype.str)

    def _get_stock_values(self, field: str) -> np.ndarray:
        key = str(field).strip().lower()
        values = self._stock_values.get(key)
        if values is None:
            raise ValueError(
                f"패턴 '{self.name}'의 stock field='{key}' 데이터가 준비되지 않았습니다."
            )
        return np.asarray(values, dtype=np.float64)

    def when(self, regime: Regime):
        """
        현재 패턴을 특정 regime 구간에서만 활성화한다.
        """

        if not isinstance(regime, Regime):
            raise TypeError("when(...)은 Regime 객체만 지원합니다.")
        return _RegimePattern(self, regime)

    def __call__(self, values: np.ndarray) -> np.ndarray:
        """
        진입 신호 mask를 계산하고 동일 입력에 대해서는 캐시를 재사용한다.
        """

        source_values = values
        if self.market_name is not None:
            if self._market_values is None:
                raise ValueError(
                    f"패턴 '{self.name}'의 market 데이터가 준비되지 않았습니다."
                )
            source_values = self._market_values

        prices = np.asarray(source_values, dtype=np.float64)
        stock_keys = tuple(
            (field, self._array_cache_token(arr))
            for field, arr in sorted(self._stock_values.items())
        )
        cache_key = (
            self._array_cache_token(np.asarray(values, dtype=np.float64)),
            self._array_cache_token(prices),
            self._array_cache_token(self._market_values),
            stock_keys,
            id(self._post_mask_fn),
        )
        if self._cached_mask_key != cache_key or self._cached_mask_value is None:
            base_mask = np.asarray(self._base_mask(prices), dtype=np.bool_)
            if base_mask.shape != prices.shape:
                raise ValueError(f"패턴 '{self.name}'의 mask shape이 일치하지 않습니다.")
            post_mask = np.asarray(self._post_mask_fn(prices), dtype=np.bool_)
            if post_mask.shape != prices.shape:
                raise ValueError(f"패턴 '{self.name}'의 후처리 mask shape이 일치하지 않습니다.")
            self._cached_mask_key = cache_key
            self._cached_mask_value = base_mask & post_mask
        return self._cached_mask_value

    def exit_mask(self, values: np.ndarray) -> np.ndarray:
        """
        진입 후 청산 규칙이 있는 패턴의 청산 mask를 반환한다.
        """

        return self._get_cached_exit_mask(values)

    def __add__(self, other: "BasePattern"):
        if not isinstance(other, BasePattern):
            return NotImplemented
        return _CombinedPattern(self, other)

    def __or__(self, other: "BasePattern"):
        if not isinstance(other, BasePattern):
            return NotImplemented
        return _UnionPattern(self, other)

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
            (field, self._array_cache_token(arr))
            for field, arr in sorted(self._stock_values.items())
        )
        cache_key = (
            self._array_cache_token(prices),
            self._array_cache_token(self._market_values),
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
        """
        별도 청산 규칙을 제공하는 패턴인지 반환한다.
        """

        return False

    def has_entry_dependent_exit(self) -> bool:
        """
        청산 시점이 진입 시점에 따라 달라지는지 반환한다.
        """

        return False

    def first_exit_index(
        self,
        values: np.ndarray,
        entry_idx: int,
        last_idx: int,
    ) -> int:
        """
        지정 진입 이후 첫 청산 시점 인덱스를 반환한다.
        """

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

    @staticmethod
    def _is_empty_trade_profile(
        profile: tuple[object | None, float | None, float | None, float | None]
    ) -> bool:
        return profile == (None, None, None, None)

    @staticmethod
    def _merge_trade_profiles(
        left: tuple[object | None, float | None, float | None, float | None],
        right: tuple[object | None, float | None, float | None, float | None],
    ) -> tuple[object | None, float | None, float | None, float | None]:
        if BasePattern._is_empty_trade_profile(left):
            return right
        if BasePattern._is_empty_trade_profile(right):
            return left
        if left == right:
            return left
        raise ValueError(
            "trade 설정이 서로 다른 패턴은 단일 branch로 결합할 수 없습니다. "
            "최종 branch에 trade(...)를 주거나 양쪽 설정을 동일하게 맞추세요."
        )

    def _trade_profile(self) -> tuple[object | None, float | None, float | None, float | None]:
        return (
            self._trade_target_horizon,
            self._trade_stop_loss_pct,
            self._trade_take_profit_pct,
            self._trade_cohort_scale,
        )

    @staticmethod
    def _merge_nmax_profile(
        left: tuple[int | None, bool],
        right: tuple[int | None, bool],
    ) -> tuple[int | None, bool]:
        if left[0] is None:
            return right
        if right[0] is None:
            return left
        if left == right:
            return left
        raise ValueError(
            "nmax 설정이 서로 다른 패턴은 단일 branch로 결합할 수 없습니다. "
            "최종 패턴에 nmax(...)를 주거나 양쪽 설정을 동일하게 맞추세요."
        )

    def _nmax_profile(self) -> tuple[int | None, bool]:
        return (
            self._max_cohort_size,
            bool(self._max_cohort_use_market_cap) if self._max_cohort_size is not None else False,
        )

    def _resolved_trade_profile(
        self,
    ) -> tuple[object | None, float | None, float | None, float | None]:
        return self._trade_profile()

    def _resolved_nmax_profile(self) -> tuple[int | None, bool]:
        return self._nmax_profile()

    def _resolved_max_cohort_size(self) -> int | None:
        return self._resolved_nmax_profile()[0]

    def _resolved_nmax_market_cap(self) -> bool:
        return bool(self._resolved_nmax_profile()[1])

    @staticmethod
    def _register_trade_profile(
        profile: tuple[object | None, float | None, float | None, float | None],
        profile_to_id: dict[tuple[object | None, float | None, float | None, float | None], int],
        id_to_profile: dict[int, tuple[object | None, float | None, float | None, float | None]],
    ) -> int:
        if profile not in profile_to_id:
            next_id = len(profile_to_id) + 1
            profile_to_id[profile] = next_id
            id_to_profile[next_id] = profile
        return int(profile_to_id[profile])

    def _build_policy_id_mask(
        self,
        values: np.ndarray,
        profile_to_id: dict[tuple[object | None, float | None, float | None, float | None], int],
        id_to_profile: dict[int, tuple[object | None, float | None, float | None, float | None]],
    ) -> np.ndarray:
        mask = np.asarray(self(values), dtype=np.bool_)
        return self._build_policy_id_mask_from_mask(
            mask,
            profile_to_id,
            id_to_profile,
        )

    def _build_policy_id_mask_from_mask(
        self,
        mask: np.ndarray,
        profile_to_id: dict[tuple[object | None, float | None, float | None, float | None], int],
        id_to_profile: dict[int, tuple[object | None, float | None, float | None, float | None]],
    ) -> np.ndarray:
        mask = np.asarray(mask, dtype=np.bool_)
        out = np.zeros(mask.shape, dtype=np.int16)
        if not np.any(mask):
            return out
        profile_id = self._register_trade_profile(
            self._resolved_trade_profile(),
            profile_to_id,
            id_to_profile,
        )
        out[mask] = int(profile_id)
        return out


class _CombinedPattern(BasePattern):
    """
    두 패턴을 교집합(and)으로 결합한 패턴.
    """

    def __init__(
        self,
        left: BasePattern,
        right: BasePattern,
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
        super().__init__(name=resolved_name)
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

    def _resolved_trade_profile(
        self,
    ) -> tuple[object | None, float | None, float | None, float | None]:
        direct = self._trade_profile()
        if not self._is_empty_trade_profile(direct):
            return direct
        return self._merge_trade_profiles(
            self.left._resolved_trade_profile(),
            self.right._resolved_trade_profile(),
        )

    def _resolved_nmax_profile(self) -> tuple[int | None, bool]:
        if self._max_cohort_size is not None:
            return self._nmax_profile()
        return self._merge_nmax_profile(
            self.left._resolved_nmax_profile(),
            self.right._resolved_nmax_profile(),
        )


class _RegimePattern(BasePattern):
    """
    기존 패턴을 regime mask로 한 번 더 감싼 패턴.
    """

    def __init__(
        self,
        pattern: BasePattern,
        regime: Regime,
        name: str | None = None,
    ):
        self.pattern = pattern
        self.regime = regime
        resolved_name = name or pattern.name
        super().__init__(name=resolved_name)
        self._regimes.append(regime)
        if pattern.trim_quantile is not None:
            self.trim(pattern.trim_quantile, method=pattern.trim_method)

    def _base_mask(self, values: np.ndarray) -> np.ndarray:
        base_mask = np.asarray(self.pattern(values), dtype=np.bool_)
        regime_mask = np.asarray(self.regime.mask(base_mask.shape[0]), dtype=np.bool_)
        if base_mask.shape != regime_mask.shape:
            raise ValueError("Regime mask shape이 패턴 시계열과 일치하지 않습니다.")
        return base_mask & regime_mask

    def _exit_mask(self, values: np.ndarray) -> np.ndarray:
        return np.asarray(self.pattern.exit_mask(values), dtype=np.bool_)

    def has_exit_rule(self) -> bool:
        return self.pattern.has_exit_rule()

    def has_entry_dependent_exit(self) -> bool:
        return self.pattern.has_entry_dependent_exit()

    def first_exit_index(
        self,
        values: np.ndarray,
        entry_idx: int,
        last_idx: int,
    ) -> int:
        return self.pattern.first_exit_index(values, entry_idx, last_idx)

    def _resolved_trade_profile(
        self,
    ) -> tuple[object | None, float | None, float | None, float | None]:
        direct = self._trade_profile()
        if not self._is_empty_trade_profile(direct):
            return direct
        return self.pattern._resolved_trade_profile()

    def _resolved_nmax_profile(self) -> tuple[int | None, bool]:
        if self._max_cohort_size is not None:
            return self._nmax_profile()
        return self.pattern._resolved_nmax_profile()


class _UnionPattern(BasePattern):
    """
    두 패턴을 합집합(or)으로 결합한 분기 패턴.
    """

    def __init__(
        self,
        left: BasePattern,
        right: BasePattern,
        name: str | None = None,
    ):
        self.left = left
        self.right = right
        trim_quantile, trim_method = _CombinedPattern._resolve_trim(
            left.trim_quantile,
            left.trim_method,
            right.trim_quantile,
            right.trim_method,
        )
        left_name = left.name if isinstance(left.name, str) and left.name else "left_pattern"
        right_name = right.name if isinstance(right.name, str) and right.name else "right_pattern"
        resolved_name = name or f"{left_name} | {right_name}"
        super().__init__(name=resolved_name)
        if trim_quantile is not None:
            self.trim(trim_quantile, method=trim_method)
        self._cached_child_mask_key: tuple[object, ...] | None = None
        self._cached_left_mask: np.ndarray | None = None
        self._cached_right_mask: np.ndarray | None = None

    def _get_cached_child_masks(self, values: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        prices = np.asarray(values, dtype=np.float64)
        left_stock_keys = tuple(
            (field, self._array_cache_token(arr))
            for field, arr in sorted(self.left._stock_values.items())
        )
        right_stock_keys = tuple(
            (field, self._array_cache_token(arr))
            for field, arr in sorted(self.right._stock_values.items())
        )
        cache_key = (
            self._array_cache_token(prices),
            self._array_cache_token(self.left._market_values),
            left_stock_keys,
            self._array_cache_token(self.right._market_values),
            right_stock_keys,
        )
        if self._cached_child_mask_key != cache_key:
            self._cached_left_mask = np.asarray(self.left(values), dtype=np.bool_)
            self._cached_right_mask = np.asarray(self.right(values), dtype=np.bool_)
            self._cached_child_mask_key = cache_key
        return self._cached_left_mask, self._cached_right_mask

    def _base_mask(self, values: np.ndarray) -> np.ndarray:
        left_mask, right_mask = self._get_cached_child_masks(values)
        if left_mask.shape != right_mask.shape:
            raise ValueError("분기 패턴의 mask shape이 일치하지 않습니다.")
        return left_mask | right_mask

    def _exit_mask(self, values: np.ndarray) -> np.ndarray:
        left_exit = np.asarray(self.left.exit_mask(values), dtype=np.bool_)
        right_exit = np.asarray(self.right.exit_mask(values), dtype=np.bool_)
        if left_exit.shape != right_exit.shape:
            raise ValueError("분기 패턴의 exit mask shape이 일치하지 않습니다.")
        return left_exit | right_exit

    def has_exit_rule(self) -> bool:
        return self.left.has_exit_rule() or self.right.has_exit_rule()

    def has_entry_dependent_exit(self) -> bool:
        return self.has_exit_rule()

    def first_exit_index(
        self,
        values: np.ndarray,
        entry_idx: int,
        last_idx: int,
    ) -> int:
        left_mask, right_mask = self._get_cached_child_masks(values)
        candidates: list[int] = []
        if 0 <= entry_idx < len(left_mask) and bool(left_mask[entry_idx]):
            left_idx = self.left.first_exit_index(values, entry_idx, last_idx)
            if left_idx >= 0:
                candidates.append(int(left_idx))
        if 0 <= entry_idx < len(right_mask) and bool(right_mask[entry_idx]):
            right_idx = self.right.first_exit_index(values, entry_idx, last_idx)
            if right_idx >= 0:
                candidates.append(int(right_idx))
        if not candidates:
            return -1
        return min(candidates)

    def _resolved_trade_profile(
        self,
    ) -> tuple[object | None, float | None, float | None, float | None]:
        direct = self._trade_profile()
        if not self._is_empty_trade_profile(direct):
            return direct
        left_profile = self.left._resolved_trade_profile()
        right_profile = self.right._resolved_trade_profile()
        if self._is_empty_trade_profile(left_profile) and self._is_empty_trade_profile(right_profile):
            return left_profile
        if left_profile == right_profile:
            return left_profile
        raise ValueError(
            "분기 패턴의 trade 설정이 branch별로 다릅니다. "
            "분기 패턴은 branch별 policy id를 사용해야 합니다."
        )

    def _resolved_nmax_profile(self) -> tuple[int | None, bool]:
        if self._max_cohort_size is not None:
            return self._nmax_profile()
        return self._merge_nmax_profile(
            self.left._resolved_nmax_profile(),
            self.right._resolved_nmax_profile(),
        )

    def _build_policy_id_mask(
        self,
        values: np.ndarray,
        profile_to_id: dict[tuple[object | None, float | None, float | None, float | None], int],
        id_to_profile: dict[int, tuple[object | None, float | None, float | None, float | None]],
    ) -> np.ndarray:
        direct = self._trade_profile()
        if not self._is_empty_trade_profile(direct):
            return super()._build_policy_id_mask(values, profile_to_id, id_to_profile)

        left_mask, right_mask = self._get_cached_child_masks(values)
        try:
            left_ids = np.asarray(
                self.left._build_policy_id_mask_from_mask(
                    left_mask,
                    profile_to_id,
                    id_to_profile,
                ),
                dtype=np.int16,
            )
        except ValueError:
            left_ids = np.asarray(
                self.left._build_policy_id_mask(values, profile_to_id, id_to_profile),
                dtype=np.int16,
            )
        try:
            right_ids = np.asarray(
                self.right._build_policy_id_mask_from_mask(
                    right_mask,
                    profile_to_id,
                    id_to_profile,
                ),
                dtype=np.int16,
            )
        except ValueError:
            right_ids = np.asarray(
                self.right._build_policy_id_mask(values, profile_to_id, id_to_profile),
                dtype=np.int16,
            )
        if left_ids.shape != right_ids.shape:
            raise ValueError("분기 패턴의 policy id mask shape이 일치하지 않습니다.")
        out = left_ids.copy()
        fill = out == 0
        out[fill] = right_ids[fill]
        return out


__all__ = [
    "BasePattern",
]
