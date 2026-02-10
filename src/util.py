"""
Numba 기반 패턴 유틸리티 함수 모음.
"""

from __future__ import annotations

import numpy as np
from numba import njit


# ---- Rolling 통계 유틸 ----

@njit(cache=True)
def rolling_high(values: np.ndarray, window: int) -> np.ndarray:
    """
    슬라이딩 윈도우 최대값(rolling high)을 O(n)으로 계산한다.

    유효값은 finite 이고 0보다 큰 값만 인정한다.
    """
    n = values.shape[0]
    out = np.empty(n, dtype=np.float64)
    out[:] = np.nan
    if window <= 0 or n == 0:
        return out

    idx_deque = np.empty(n, dtype=np.int64)
    head = 0
    tail = 0

    for i in range(n):
        val = values[i]
        valid = np.isfinite(val) and val > 0.0
        if valid:
            while tail > head and values[idx_deque[tail - 1]] <= val:
                tail -= 1
            idx_deque[tail] = i
            tail += 1

        cutoff = i - window + 1
        while tail > head and idx_deque[head] < cutoff:
            head += 1

        if i >= window - 1 and tail > head:
            out[i] = values[idx_deque[head]]

    return out


@njit(cache=True)
def rolling_mean_std(values: np.ndarray, window: int):
    """
    롤링 평균/표준편차를 계산한다.

    윈도우 내부 값이 모두 유효(finite, >0)할 때만 결과를 내고,
    그렇지 않으면 해당 위치는 NaN/False로 남긴다.
    """
    n = values.shape[0]
    mean = np.empty(n, dtype=np.float64)
    std = np.empty(n, dtype=np.float64)
    valid_end = np.zeros(n, dtype=np.bool_)
    mean[:] = np.nan
    std[:] = np.nan
    if window <= 0 or n == 0:
        return mean, std, valid_end

    sum_val = 0.0
    sum_sq = 0.0
    count = 0
    for i in range(n):
        v = values[i]
        if np.isfinite(v) and v > 0.0:
            sum_val += v
            sum_sq += v * v
            count += 1

        if i >= window:
            v_old = values[i - window]
            if np.isfinite(v_old) and v_old > 0.0:
                sum_val -= v_old
                sum_sq -= v_old * v_old
                count -= 1

        if i >= window - 1 and count == window:
            m = sum_val / window
            var = sum_sq / window - m * m
            if var < 0.0:
                var = 0.0
            mean[i] = m
            std[i] = np.sqrt(var)
            valid_end[i] = True

    return mean, std, valid_end


@njit(cache=True)
def rolling_mean(values: np.ndarray, window: int):
    """
    롤링 평균을 계산한다.

    윈도우 내부 값이 모두 유효(finite, >0)할 때만 평균을 기록한다.
    """
    n = values.shape[0]
    mean = np.empty(n, dtype=np.float64)
    valid_end = np.zeros(n, dtype=np.bool_)
    mean[:] = np.nan
    if window <= 0 or n == 0:
        return mean, valid_end

    sum_val = 0.0
    count = 0
    for i in range(n):
        v = values[i]
        if np.isfinite(v) and v > 0.0:
            sum_val += v
            count += 1

        if i >= window:
            v_old = values[i - window]
            if np.isfinite(v_old) and v_old > 0.0:
                sum_val -= v_old
                count -= 1

        if i >= window - 1 and count == window:
            mean[i] = sum_val / window
            valid_end[i] = True

    return mean, valid_end


@njit(cache=True)
def rolling_percentile(values: np.ndarray, window: int, percentile: float) -> np.ndarray:
    """
    정확 rolling percentile 계산(윈도우 정렬 기반).

    현재 메인 파이프라인에서는 미사용이며,
    정밀 percentile이 필요할 때 사용할 수 있는 대안 구현이다.
    """
    n = values.shape[0]
    out = np.empty(n, dtype=np.float64)
    out[:] = np.nan
    if window <= 0 or n == 0:
        return out

    buf = np.empty(window, dtype=np.float64)
    pct = percentile / 100.0
    if pct < 0.0:
        pct = 0.0
    elif pct > 1.0:
        pct = 1.0

    for i in range(window - 1, n):
        count = 0
        start = i - window + 1
        for j in range(start, i + 1):
            v = values[j]
            if np.isfinite(v):
                buf[count] = v
                count += 1
        if count == 0:
            continue
        sorted_vals = np.sort(buf[:count])
        idx = int(np.floor(pct * (count - 1)))
        out[i] = sorted_vals[idx]

    return out


@njit(cache=True)
def rolling_percentile_hist(
    values: np.ndarray,
    window: int,
    percentile: float,
    bins: int,
) -> np.ndarray:
    """
    히스토그램 기반 rolling percentile 근사 계산.

    전역 min/max 범위를 bins로 나눠 분위수를 근사한다.
    현재 메인 파이프라인에서는 bandwidth_mask(percentile 모드)에서 사용한다.
    """
    n = values.shape[0]
    out = np.empty(n, dtype=np.float64)
    out[:] = np.nan
    if window <= 0 or n == 0 or n < window or bins <= 1:
        return out

    min_val = np.inf
    max_val = -np.inf
    for i in range(n):
        v = values[i]
        if np.isfinite(v):
            if v < min_val:
                min_val = v
            if v > max_val:
                max_val = v
    if not np.isfinite(min_val):
        return out
    if max_val == min_val:
        for i in range(window - 1, n):
            out[i] = min_val
        return out

    bin_width = (max_val - min_val) / bins
    if bin_width <= 0.0:
        for i in range(window - 1, n):
            out[i] = min_val
        return out

    counts = np.zeros(bins, dtype=np.int64)
    count_valid = 0
    for i in range(window):
        v = values[i]
        if np.isfinite(v):
            idx = int((v - min_val) / bin_width)
            if idx < 0:
                idx = 0
            elif idx >= bins:
                idx = bins - 1
            counts[idx] += 1
            count_valid += 1

    pct = percentile / 100.0
    if pct < 0.0:
        pct = 0.0
    elif pct > 1.0:
        pct = 1.0

    for i in range(window - 1, n):
        if count_valid > 0:
            rank = int(np.floor(pct * (count_valid - 1)))
            cum = 0
            idx = 0
            while idx < bins:
                cum += counts[idx]
                if cum > rank:
                    out[i] = min_val + (idx + 0.5) * bin_width
                    break
                idx += 1
            if idx == bins:
                out[i] = max_val

        if i + 1 < n:
            out_idx = i - window + 1
            v_out = values[out_idx]
            if np.isfinite(v_out):
                idx = int((v_out - min_val) / bin_width)
                if idx < 0:
                    idx = 0
                elif idx >= bins:
                    idx = bins - 1
                counts[idx] -= 1
                count_valid -= 1

            v_in = values[i + 1]
            if np.isfinite(v_in):
                idx = int((v_in - min_val) / bin_width)
                if idx < 0:
                    idx = 0
                elif idx >= bins:
                    idx = bins - 1
                counts[idx] += 1
                count_valid += 1

    return out


@njit(cache=True)
def cooldown_mask(mask: np.ndarray, cooldown: int) -> np.ndarray:
    """
    신호 발생 후 cooldown 기간 동안 재발생을 차단한다.

    입력 mask를 제자리(in-place)로 수정한다.
    """
    if cooldown <= 0:
        return mask
    last_break = -cooldown - 1
    n = mask.shape[0]
    for i in range(n):
        if mask[i]:
            if i - last_break <= cooldown:
                mask[i] = False
            else:
                last_break = i
    return mask


@njit(cache=True)
def stay_mask(condition: np.ndarray, stay_days: int) -> np.ndarray:
    """
    condition이 stay_days일 이상 연속일 때만 True를 남긴다.
    """
    if stay_days <= 1:
        return condition.copy()
    n = condition.shape[0]
    out = np.zeros(n, dtype=np.bool_)
    run = 0
    for i in range(n):
        if condition[i]:
            run += 1
        else:
            run = 0
        if run >= stay_days:
            out[i] = True
    return out


@njit(cache=True)
def cooldown_stay_mask(
    condition: np.ndarray,
    stay_days: int,
    cooldown_days: int,
) -> np.ndarray:
    """
    연속 유지(stay) 조건과 출현 간격(cooldown) 조건을 결합한 마스크.

    - stay_days: condition이 연속으로 유지되어야 하는 최소 일수
    - cooldown_days: 이전 출현과의 최소 간격
    """
    sustained = stay_mask(condition, stay_days)
    if cooldown_days <= 0:
        return sustained

    n = sustained.shape[0]
    entries = np.zeros(n, dtype=np.bool_)
    for i in range(n):
        if sustained[i] and (i == 0 or not sustained[i - 1]):
            entries[i] = True
    entries = cooldown_mask(entries, cooldown_days)

    out = np.zeros(n, dtype=np.bool_)
    i = 0
    while i < n:
        if not sustained[i]:
            i += 1
            continue
        run_start = i
        while i < n and sustained[i]:
            i += 1
        if entries[run_start]:
            for j in range(run_start, i):
                out[j] = True
    return out


@njit(cache=True)
def uptrend_mask(
    prices: np.ndarray,
    window: int,
    stay_days: int = 1,
    cooldown_days: int = 0,
) -> np.ndarray:
    """
    이동평균 기울기(전일 대비 상승) 기반 추세 마스크.

    - stay_days: 상승 추세가 연속으로 유지되어야 하는 최소 일수
    - cooldown_days: 출현 직전 최소 비출현 일수(쿨다운)
    """
    n = prices.shape[0]
    if window <= 1 or n < window:
        return np.ones(n, dtype=np.bool_)

    min_stay = max(1, int(stay_days))
    cooldown = max(0, int(cooldown_days))

    mean_up, valid_up = rolling_mean(prices, window)
    up = np.zeros(n, dtype=np.bool_)
    for i in range(1, n):
        if valid_up[i] and valid_up[i - 1] and mean_up[i] > mean_up[i - 1]:
            up[i] = True

    return cooldown_stay_mask(up, min_stay, cooldown)


@njit(cache=True)
def break_mask(
    prices: np.ndarray,
    trigger_line: np.ndarray,
    base_mask: np.ndarray,
    direction: int,
    cooldown: int,
) -> np.ndarray:
    """
    기준선(trigger_line) 돌파 마스크를 계산한다.

    - direction >= 0: 상단(위쪽) 돌파, prices > trigger_line
    - direction < 0: 하단(아래쪽) 돌파, prices < trigger_line
    - cooldown: 돌파 신호 후 재발생 제한 일수
    """
    n = prices.shape[0]
    out = np.zeros(n, dtype=np.bool_)
    is_up = direction >= 0
    for i in range(n):
        if not base_mask[i]:
            continue
        p = prices[i]
        t = trigger_line[i]
        if not (np.isfinite(p) and np.isfinite(t)):
            continue
        if is_up:
            out[i] = p > t
        else:
            out[i] = p < t
    return cooldown_mask(out, cooldown)


@njit(cache=True)
def approach_mask(
    prices: np.ndarray,
    trigger_line: np.ndarray,
    base_mask: np.ndarray,
    tolerance: float,
    stay_days: int,
    direction: int,
) -> np.ndarray:
    """
    기준선(trigger_line) 근접 마스크를 계산한다.

    - direction >= 0: 상단 근접, prices >= trigger_line * (1 - tolerance)
    - direction < 0: 하단 근접, prices <= trigger_line * (1 + tolerance)
    - stay_days: 연속 충족 일수
    """
    n = prices.shape[0]
    out = np.zeros(n, dtype=np.bool_)
    tol = tolerance
    if tol < 0.0:
        tol = 0.0
    is_up = direction >= 0

    for i in range(n):
        if not base_mask[i]:
            continue
        p = prices[i]
        t = trigger_line[i]
        if not (np.isfinite(p) and np.isfinite(t)):
            continue
        if is_up:
            out[i] = p >= t * (1.0 - tol)
        else:
            out[i] = p <= t * (1.0 + tol)

    return stay_mask(out, stay_days)


@njit(cache=True)
def bandwidth_mask(
    mean: np.ndarray,
    band_width: np.ndarray,
    valid_end: np.ndarray,
    narrow_width: float,
    mode: int,
    lookback: int,
    narrow_stay_days: int,
) -> np.ndarray:
    """
    밴드 폭 축소 구간 마스크를 계산한다.

    - mode=0: 절대폭 기준
    - mode=1: percentile 기준(rolling_percentile_hist 사용)
    """
    if narrow_width >= 1.0:
        return valid_end.copy()

    n = mean.shape[0]
    ratio = np.empty(n, dtype=np.float64)
    for i in range(n):
        if valid_end[i] and mean[i] > 0.0:
            ratio[i] = band_width[i] / mean[i]
        else:
            ratio[i] = np.nan

    if mode == 0 or narrow_width <= 0:
        if narrow_width <= 0:
            return stay_mask(valid_end, narrow_stay_days)
        out = np.zeros(n, dtype=np.bool_)
        thresh = narrow_width
        for i in range(n):
            v = ratio[i]
            out[i] = np.isfinite(v) and v <= thresh
        return stay_mask(out, narrow_stay_days)

    thresholds = rolling_percentile_hist(ratio, lookback, narrow_width * 100.0, 128)
    out = np.zeros(n, dtype=np.bool_)
    for i in range(n):
        v = ratio[i]
        t = thresholds[i]
        out[i] = np.isfinite(v) and np.isfinite(t) and v <= t
    return stay_mask(out, narrow_stay_days)


@njit(cache=True)
def high_mask(
    prices: np.ndarray,
    window: int,
    threshold: float,
    stay_days: int = 1,
    cooldown_days: int = 0,
) -> np.ndarray:
    """
    rolling high 대비 threshold 이상인 구간 마스크.
    """
    n = prices.shape[0]
    if window <= 0:
        return np.ones(n, dtype=np.bool_)
    min_stay = max(1, int(stay_days))
    cooldown = max(0, int(cooldown_days))
    high_series = rolling_high(prices, window)
    out = np.zeros(n, dtype=np.bool_)
    for i in range(n):
        h = high_series[i]
        if np.isfinite(h) and prices[i] >= threshold * h:
            out[i] = True
    return cooldown_stay_mask(out, min_stay, cooldown)
