"""Pattern helpers that operate directly on numpy price arrays."""

from __future__ import annotations

import numpy as np
from numpy.lib.stride_tricks import sliding_window_view


def market(values: np.ndarray, name: str | None = None) -> np.ndarray:
    """Return True for every date with a finite, positive price."""

    prices = np.asarray(values, dtype=np.float64)
    return np.isfinite(prices) & (prices > 0)


def bollinger(
    values: np.ndarray,
    window: int = 20,
    sigma: float = 2.0,
    max_band_pct: float = 100.0,
    min_narrow_days: int = 1,
    band_pct_type: str = "absolute",
    band_pct_percentile_window: int = 252,
    uptrend_window: int | None = None,
    high_window: int | None = None,
    high_threshold: float = 0.9,
    style: str = "breakout",
    name: str | None = None,
) -> np.ndarray:
    """
    Return True when prices break above the upper Bollinger band and the band width
    is within `max_band_pct` of the moving average.
    """

    prices = np.asarray(values, dtype=np.float64)
    n = prices.shape[0]
    mask = np.zeros(n, dtype=bool)

    if window <= 0 or n < window:
        return mask

    windows = sliding_window_view(prices, window)
    valid_win = np.isfinite(windows) & (windows > 0)
    all_valid = valid_win.all(axis=1)
    if not np.any(all_valid):
        return mask

    safe_windows = np.where(valid_win, windows, 0.0)
    sum_win = safe_windows.sum(axis=1)
    sum_sq = (safe_windows * safe_windows).sum(axis=1)

    mean = sum_win / window

    variance = np.maximum(sum_sq / window - mean * mean, 0.0)
    band_width = sigma * np.sqrt(variance)
    upper = mean + band_width

    band_type = (band_pct_type or "absolute").lower()
    if band_type not in {"absolute", "percentile"}:
        raise ValueError("band_pct_type must be 'absolute' or 'percentile'")

    if band_type == "absolute" or max_band_pct <= 0:
        if max_band_pct <= 0:
            narrow_condition = np.ones_like(mean, dtype=bool)
        else:
            ratio = band_width / np.clip(mean, 1e-12, None)
            narrow_condition = ratio <= (max_band_pct / 100.0)
    else:
        ratio = band_width / np.clip(mean, 1e-12, None)
        ratio = np.where(np.isfinite(ratio), ratio, np.nan)
        lookback = int(max(1, band_pct_percentile_window))
        ratio_len = ratio.shape[0]
        if ratio_len >= lookback:
            ratio_windows = sliding_window_view(ratio, lookback)
            percentile_vals = np.nanpercentile(ratio_windows, max_band_pct, axis=1)
            thresholds = np.full(ratio_len, np.nan)
            thresholds[lookback - 1 :] = percentile_vals
            narrow_condition = np.isfinite(thresholds) & (ratio <= thresholds)
        else:
            narrow_condition = np.zeros_like(ratio, dtype=bool)

    if min_narrow_days <= 1:
        narrow_mask = narrow_condition
    else:
        narrow_mask = np.zeros_like(narrow_condition, dtype=bool)
        run = 0
        for i, cond in enumerate(narrow_condition):
            if cond:
                run += 1
            else:
                run = 0
            if run >= min_narrow_days:
                narrow_mask[i] = True

    if high_window is not None and high_window > 0 and n >= high_window:
        hw = int(high_window)
        high_windows = sliding_window_view(prices, hw)
        high_valid = np.isfinite(high_windows) & (high_windows > 0)
        safe_high = np.where(high_valid, high_windows, -np.inf)
        rolling_high = safe_high.max(axis=1)
        rolling_high[~high_valid.any(axis=1)] = np.nan
        high_series = np.full(n, np.nan)
        high_series[hw - 1 :] = rolling_high
        high_mask = np.isfinite(high_series) & (prices >= high_threshold * high_series)
    else:
        high_mask = np.ones(n, dtype=bool)

    # optional uptrend filter using independent moving average window
    if uptrend_window is not None and uptrend_window > 1 and n >= uptrend_window:
        uw = int(uptrend_window)
        trend_end = np.zeros(n, dtype=bool)
        up_windows = sliding_window_view(prices, uw)
        up_valid = np.isfinite(up_windows) & (up_windows > 0)
        up_all = up_valid.all(axis=1)
        if np.any(up_all):
            safe_up = np.where(up_valid, up_windows, 0.0)
            sum_up = safe_up.sum(axis=1)
            mean_up = np.full_like(sum_up, np.nan)
            valid_idx = np.where(up_all)[0]
            mean_up[valid_idx] = sum_up[valid_idx] / uw
            prev_up = np.empty_like(mean_up)
            prev_up[:] = np.nan
            prev_up[1:] = mean_up[:-1]
            trend_windows = up_all & np.isfinite(prev_up) & (mean_up > prev_up)
            trend_indices = np.where(trend_windows)[0] + uw - 1
            trend_end[trend_indices] = True
    else:
        trend_end = np.ones(n, dtype=bool)

    style_key = (style or "breakout").lower()
    if style_key not in {"breakout", "topclose"}:
        raise ValueError("style must be 'breakout' or 'topclose'")

    idx = np.where(all_valid & narrow_mask)[0]
    if idx.size == 0:
        return mask

    end_pos = idx + window - 1
    base_valid = trend_end[end_pos] & high_mask[end_pos]

    if style_key == "topclose":
        tolerance = 0.03
        closeness = prices[end_pos] >= upper[idx] * (1.0 - tolerance)
        candidate = np.zeros(n, dtype=bool)
        candidate[end_pos] = base_valid & closeness
        run = 0
        for i in range(n):
            if candidate[i]:
                run += 1
            else:
                run = 0
            mask[i] = run >= 3
        return mask

    breakout = prices[end_pos] > upper[idx]
    breakout &= base_valid
    mask[end_pos] = breakout

    cooldown = 3  # require no breakout during the previous 3 days
    if cooldown > 0:
        last_break = -cooldown - 1
        for pos in np.where(mask)[0]:
            if pos - last_break <= cooldown:
                mask[pos] = False
            else:
                last_break = pos

    return mask
