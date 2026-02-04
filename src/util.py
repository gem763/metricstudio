"""Numba 기반 패턴 유틸리티 함수 모음."""

from __future__ import annotations

import numpy as np
from numba import njit


@njit(cache=True)
def rolling_high(values: np.ndarray, window: int) -> np.ndarray:
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
def min_run_mask(condition: np.ndarray, min_run: int) -> np.ndarray:
    if min_run <= 1:
        return condition.copy()
    n = condition.shape[0]
    out = np.zeros(n, dtype=np.bool_)
    run = 0
    for i in range(n):
        if condition[i]:
            run += 1
        else:
            run = 0
        if run >= min_run:
            out[i] = True
    return out


@njit(cache=True)
def uptrend_mask(
    prices: np.ndarray,
    window: int,
) -> np.ndarray:
    n = prices.shape[0]
    if window <= 1 or n < window:
        return np.ones(n, dtype=np.bool_)
    mean_up, valid_up = rolling_mean(prices, window)
    out = np.zeros(n, dtype=np.bool_)
    for i in range(1, n):
        if valid_up[i] and valid_up[i - 1] and mean_up[i] > mean_up[i - 1]:
            out[i] = True
    return out


@njit(cache=True)
def trigger_mask(
    prices: np.ndarray,
    upper: np.ndarray,
    base_mask: np.ndarray,
    trigger_mode: int,
    cooldown: int,
    topclose_tolerance: float,
    topclose_stay_days: int,
) -> np.ndarray:
    if trigger_mode == 1:
        closeness = prices >= upper * (1.0 - topclose_tolerance)
        candidate = base_mask & closeness
        return min_run_mask(candidate, topclose_stay_days)

    mask = base_mask & (prices > upper)
    return cooldown_mask(mask, cooldown)


@njit(cache=True)
def narrow_mask(
    mean: np.ndarray,
    band_width: np.ndarray,
    valid_end: np.ndarray,
    narrow_width: float,
    mode: int,
    lookback: int,
    narrow_stay_days: int,
) -> np.ndarray:
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
            return min_run_mask(valid_end, narrow_stay_days)
        out = np.zeros(n, dtype=np.bool_)
        thresh = narrow_width
        for i in range(n):
            v = ratio[i]
            out[i] = np.isfinite(v) and v <= thresh
        return min_run_mask(out, narrow_stay_days)

    thresholds = rolling_percentile_hist(ratio, lookback, narrow_width * 100.0, 128)
    out = np.zeros(n, dtype=np.bool_)
    for i in range(n):
        v = ratio[i]
        t = thresholds[i]
        out[i] = np.isfinite(v) and np.isfinite(t) and v <= t
    return min_run_mask(out, narrow_stay_days)


@njit(cache=True)
def high_mask(
    prices: np.ndarray,
    window: int,
    threshold: float,
) -> np.ndarray:
    n = prices.shape[0]
    if window <= 0:
        return np.ones(n, dtype=np.bool_)
    high_series = rolling_high(prices, window)
    out = np.zeros(n, dtype=np.bool_)
    for i in range(n):
        h = high_series[i]
        if np.isfinite(h) and prices[i] >= threshold * h:
            out[i] = True
    return out
