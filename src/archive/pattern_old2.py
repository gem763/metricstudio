"""넘파이 가격 배열에 대한 패턴 보조 함수."""

from __future__ import annotations

from typing import Literal

import numpy as np

from src import util as u


def market(values: np.ndarray, name: str | None = None) -> np.ndarray:
    """유효하고 양수인 가격이면 True를 반환한다."""

    prices = np.asarray(values, dtype=np.float64)
    return np.isfinite(prices) & (prices > 0)


def bollinger(
    values: np.ndarray,
    window: int = 20,
    sigma: float = 2.0,
    narrow_width: float = 1.0,
    narrow_stay_days: int = 1,
    narrow_width_type: Literal["absolute", "percentile"] = "absolute",
    narrow_percentile_window: int = 252,
    uptrend_window: int | None = None,
    high_window: int | None = None,
    high_threshold: float = 0.9,
    trigger: Literal["breakout", "topclose"] = "breakout",
    trigger_cooldown_days: int = 3,
    trigger_topclose_tolerance: float = 0.03,
    trigger_topclose_stay_days: int = 3,
    name: str | None = None,
) -> np.ndarray:
    """
    볼린저밴드 상단 돌파 시점과 밴드폭 조건을 만족하는 구간을 True로 반환한다.
    narrow_width는 비율(예: 5%면 0.05) 기준이다.
    """

    narrow_width_type = (narrow_width_type or "absolute").lower()
    if narrow_width_type not in {"absolute", "percentile"}:
        raise ValueError("narrow_width_type은 'absolute' 또는 'percentile'이어야 합니다.")

    trigger = (trigger or "breakout").lower()
    if trigger not in {"breakout", "topclose"}:
        raise ValueError("trigger는 'breakout' 또는 'topclose'여야 합니다.")

    prices = np.asarray(values, dtype=np.float64)
    n = prices.shape[0]
    mask = np.zeros(n, dtype=bool)

    if window <= 0 or n < window:
        return mask

    mean, std, valid_end = u.rolling_mean_std(prices, window)
    if not np.any(valid_end):
        return mask

    # 1) 기본 밴드 계산
    band_width = sigma * std
    upper = mean + band_width
    mask = valid_end.copy()

    # 2) 밴드폭(좁은 구간) 조건
    mode = 0 if narrow_width_type == "absolute" else 1
    mask &= u.bandwidth_mask(
        mean,
        band_width,
        valid_end,
        narrow_width,
        mode,
        int(max(1, narrow_percentile_window)),
    )

    # 3) 52주 고가 근처 조건
    hw = int(high_window) if high_window is not None else 0
    mask &= u.high_mask(prices, hw, high_threshold)

    # 4) 업트렌드 조건(별도 이평선)
    uw = int(uptrend_window) if uptrend_window is not None else 0
    mask &= u.uptrend_mask(prices, uw)

    # 5) 트리거 분기
    trigger_mode = 0 if trigger == "breakout" else 1
    return u.trigger_mask(
        prices,
        upper,
        mask,
        trigger_mode,
        int(max(0, trigger_cooldown_days)),
        float(trigger_topclose_tolerance),
        int(max(1, trigger_topclose_stay_days)),
    )
