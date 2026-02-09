"""Statistical aggregation and plotting helpers for backtests."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, Iterable, List, Tuple

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from matplotlib.ticker import MaxNLocator, StrMethodFormatter


Horizon = Tuple[str, int]


def _as_percent(values: np.ndarray) -> np.ndarray:
    return values * 100.0


def _normalize_ylim_percent(ylim):
    if ylim is None:
        return None
    lo = float(ylim[0])
    hi = float(ylim[1])
    # 소수 수익률(예: 0.3) 입력도 허용하고, 퍼센트(예: 30) 입력도 허용한다.
    if max(abs(lo), abs(hi)) <= 2.0:
        return lo * 100.0, hi * 100.0
    return lo, hi


def _apply_integer_y_ticks(axes):
    for ax in axes:
        ax.yaxis.set_major_locator(MaxNLocator(integer=True))
        ax.yaxis.set_major_formatter(StrMethodFormatter("{x:.0f}"))


def _share_return_y_axis(axes):
    axes[1].sharey(axes[0])
    axes[1].set_ylabel("")
    axes[1].tick_params(axis="y", left=False, labelleft=False)


def _apply_date_ticks(axes, dates):
    n_points = len(dates)
    if n_points <= 0:
        return
    dt = pd.to_datetime(dates)
    span_days = max(1, int((dt[-1] - dt[0]).days))

    if span_days <= 120:
        date_fmt = "%Y-%m-%d"
    elif span_days <= 10 * 365:
        date_fmt = "%Y-%m"
    else:
        date_fmt = "%Y"

    # 축 너비(픽셀)와 라벨 길이를 같이 고려해서 라벨 개수를 동적으로 제한한다.
    fig = axes[0].figure
    fig_width_px = float(fig.get_size_inches()[0] * fig.dpi)
    axis_width_px = fig_width_px / max(1, len(axes))
    sample_len = max(4, len(dt[0].strftime(date_fmt)))
    label_px = sample_len * 8.0 + 14.0
    max_ticks_by_width = int(axis_width_px // label_px)
    target_ticks = max(2, min(8, max_ticks_by_width))

    idx = np.linspace(0, n_points - 1, num=min(target_ticks, n_points), dtype=int)
    idx = np.unique(idx)
    tick_dates = dt[idx]
    tick_labels = tick_dates.strftime(date_fmt).tolist()

    # 포맷 때문에 같은 라벨이 반복될 수 있어 중복 라벨을 제거한다.
    uniq_dates = []
    uniq_labels = []
    for tick_date, tick_label in zip(tick_dates, tick_labels):
        if uniq_labels and uniq_labels[-1] == tick_label:
            continue
        uniq_dates.append(tick_date)
        uniq_labels.append(tick_label)
    if len(uniq_dates) >= 2:
        tick_dates = uniq_dates
        tick_labels = uniq_labels

    for ax in axes:
        ax.set_xticks(tick_dates)
        ax.set_xticklabels(tick_labels)
        ax.tick_params(axis="x", labelrotation=0)


def _rolling_sum_1d(values: np.ndarray, window: int) -> np.ndarray:
    if window <= 1:
        return values.astype(np.float64, copy=True)
    cumsum = np.cumsum(values, dtype=np.float64)
    out = cumsum.copy()
    if values.shape[0] >= window:
        out[window:] = cumsum[window:] - cumsum[:-window]
    return out


def _parse_lookback(spec: str):
    text = str(spec).strip().upper()
    if len(text) < 2:
        raise ValueError(f"Invalid lookback format: {spec}")
    unit = text[-1]
    value_text = text[:-1]
    if not value_text.isdigit():
        raise ValueError(f"Invalid lookback format: {spec}")
    value = int(value_text)
    if value <= 0:
        raise ValueError(f"Lookback must be positive: {spec}")
    if unit == "D":
        return pd.Timedelta(days=value)
    if unit == "W":
        return pd.Timedelta(weeks=value)
    if unit == "M":
        return pd.DateOffset(months=value)
    if unit == "Y":
        return pd.DateOffset(years=value)
    raise ValueError(f"Unsupported lookback unit in '{spec}'. Use D/W/M/Y.")


def _lookback_start(asof: pd.Timestamp, lookback: str) -> pd.Timestamp:
    return asof - _parse_lookback(lookback)


def _normalize_trim_quantile(trim_quantile: float | None) -> float | None:
    if trim_quantile is None:
        return None
    value = float(trim_quantile)
    if not np.isfinite(value) or value < 0.0 or value >= 0.5:
        raise ValueError("trim_quantile must be in [0.0, 0.5).")
    return value


@dataclass
class Stats:
    dates: np.ndarray
    horizons: List[Horizon]
    counts: np.ndarray
    sum_ret: np.ndarray
    sum_log: np.ndarray
    pos_counts: np.ndarray
    geom_invalid: np.ndarray
    event_returns_by_horizon: List[np.ndarray] | None = None
    event_date_idx_by_horizon: List[np.ndarray] | None = None

    @classmethod
    def create(
        cls,
        dates: np.ndarray,
        horizons: Iterable[Horizon],
        keep_event_returns: bool = False,
    ) -> "Stats":
        horizon_list = list(horizons)
        length = len(dates)
        num_h = len(horizon_list)
        if keep_event_returns:
            event_returns = [np.empty(0, dtype=np.float32) for _ in range(num_h)]
            event_date_idx = [np.empty(0, dtype=np.int32) for _ in range(num_h)]
        else:
            event_returns = None
            event_date_idx = None
        return cls(
            dates=dates.copy(),
            horizons=horizon_list,
            counts=np.zeros((num_h, length), dtype=np.int64),
            sum_ret=np.zeros((num_h, length), dtype=np.float64),
            sum_log=np.zeros((num_h, length), dtype=np.float64),
            pos_counts=np.zeros((num_h, length), dtype=np.int64),
            geom_invalid=np.zeros((num_h, length), dtype=np.bool_),
            event_returns_by_horizon=event_returns,
            event_date_idx_by_horizon=event_date_idx,
        )

    def _slice_indices(self, start=None, end=None) -> Tuple[int, int]:
        dates = self.dates
        total = len(dates)
        if start is None:
            start_idx = 0
        else:
            start_ts = np.datetime64(pd.Timestamp(start).to_datetime64())
            start_idx = int(np.searchsorted(dates, start_ts, side="left"))
        if end is None:
            end_idx = total
        else:
            end_ts = np.datetime64(pd.Timestamp(end).to_datetime64())
            end_idx = int(np.searchsorted(dates, end_ts, side="right"))
        start_idx = max(0, min(start_idx, total))
        end_idx = max(start_idx, min(end_idx, total))
        return start_idx, end_idx

    @staticmethod
    def _metrics_from_returns(returns: np.ndarray) -> tuple[float, float, float, float]:
        clean = np.asarray(returns, dtype=np.float64)
        clean = clean[np.isfinite(clean)]
        if clean.size == 0:
            return 0.0, float("nan"), float("nan"), float("nan")

        cnt = float(clean.size)
        arith = float(clean.mean())
        rise = float((clean > 0).mean())
        if np.any(clean <= -1.0):
            geom = float("nan")
        else:
            geom = float(np.exp(np.log1p(clean).mean()) - 1.0)
        return cnt, arith, geom, rise

    def _returns_in_range(self, h_idx: int, start_idx: int, end_idx: int) -> np.ndarray:
        if self.event_returns_by_horizon is None or self.event_date_idx_by_horizon is None:
            raise ValueError(
                "trimmed 집계를 위해 event return 버퍼가 필요합니다. "
                "Backtest에서 trim이 있는 패턴으로 실행하세요."
            )

        returns = np.asarray(self.event_returns_by_horizon[h_idx], dtype=np.float64)
        date_idx = np.asarray(self.event_date_idx_by_horizon[h_idx], dtype=np.int64)
        if returns.shape[0] != date_idx.shape[0]:
            raise ValueError("event return 버퍼와 date_idx 길이가 일치하지 않습니다.")
        if returns.size == 0:
            return np.empty(0, dtype=np.float64)

        in_range = (date_idx >= start_idx) & (date_idx < end_idx)
        if not in_range.any():
            return np.empty(0, dtype=np.float64)
        return returns[in_range]

    def _trimmed_metrics(
        self,
        h_idx: int,
        start_idx: int,
        end_idx: int,
        trim_quantile: float,
    ) -> tuple[float, float, float, float]:
        returns = self._returns_in_range(h_idx, start_idx, end_idx)
        returns = returns[np.isfinite(returns)]
        if returns.size == 0:
            return 0.0, float("nan"), float("nan"), float("nan")

        if trim_quantile > 0.0:
            lo = float(np.quantile(returns, trim_quantile))
            hi = float(np.quantile(returns, 1.0 - trim_quantile))
            returns = returns[(returns >= lo) & (returns <= hi)]
        return self._metrics_from_returns(returns)

    def to_frame(self, start=None, end=None, trim_quantile: float | None = None) -> pd.DataFrame:
        trim_q = _normalize_trim_quantile(trim_quantile)
        start_idx, end_idx = self._slice_indices(start, end)
        rows = []
        if start_idx >= end_idx:
            scope_label = "empty"
        else:
            if start is None and end is None:
                scope_label = "overall"
            else:
                start_date = pd.Timestamp(self.dates[start_idx]).date()
                end_date = pd.Timestamp(self.dates[end_idx - 1]).date()
                scope_label = f"{start_date}~{end_date}"
        for h_idx, (label, _) in enumerate(self.horizons):
            if trim_q is not None and trim_q > 0.0:
                cnt, arith, geom, rise = self._trimmed_metrics(
                    h_idx,
                    start_idx,
                    end_idx,
                    trim_q,
                )
            else:
                cnt = float(self.counts[h_idx, start_idx:end_idx].sum())
                sum_ret = float(self.sum_ret[h_idx, start_idx:end_idx].sum())
                sum_log = float(self.sum_log[h_idx, start_idx:end_idx].sum())
                pos = float(self.pos_counts[h_idx, start_idx:end_idx].sum())
                invalid = bool(self.geom_invalid[h_idx, start_idx:end_idx].any())

                if cnt == 0:
                    arith = float("nan")
                    geom = float("nan")
                    rise = float("nan")
                else:
                    arith = sum_ret / cnt
                    if invalid:
                        geom = float("nan")
                    else:
                        geom = float(np.exp(sum_log / cnt) - 1.0)
                    rise = pos / cnt
            rows.append(
                {
                    "period": label,
                    "scope": scope_label,
                    "count": cnt,
                    "arith_mean": arith,
                    "geom_mean": geom,
                    "rise_prob": rise,
                }
            )

        if not rows:
            return pd.DataFrame(
                columns=["period", "scope", "count", "arith_mean", "geom_mean", "rise_prob"]
            ).set_index(["period", "scope"])
        return pd.DataFrame(rows).set_index(["period", "scope"])

    def to_frame_history(
        self,
        horizon: str | int = "1M",
        start=None,
        end=None,
        history_window: int = 252,
        min_count: int = 30,
        require_full_window: bool = True,
    ) -> pd.DataFrame:
        start_idx, end_idx = self._slice_indices(start, end)
        if start_idx >= end_idx:
            raise ValueError("No data available for the specified range.")

        if isinstance(horizon, int):
            h_idx = horizon
            if h_idx < 0 or h_idx >= len(self.horizons):
                raise ValueError(f"Invalid horizon index: {horizon}")
        else:
            labels = [label for label, _ in self.horizons]
            if horizon not in labels:
                raise ValueError(f"Unknown horizon: {horizon}")
            h_idx = labels.index(horizon)

        window = max(1, int(history_window))

        counts = self.counts[h_idx].astype(np.float64)
        sum_ret = self.sum_ret[h_idx]
        sum_log = self.sum_log[h_idx]
        pos = self.pos_counts[h_idx].astype(np.float64)
        invalid = self.geom_invalid[h_idx]

        roll_counts = _rolling_sum_1d(counts, window)
        roll_sum_ret = _rolling_sum_1d(sum_ret, window)
        roll_sum_log = _rolling_sum_1d(sum_log, window)
        roll_pos = _rolling_sum_1d(pos, window)
        roll_invalid = _rolling_sum_1d(invalid.astype(np.float64), window) > 0.0

        dates = self.dates[start_idx:end_idx]
        roll_counts = roll_counts[start_idx:end_idx]
        roll_sum_ret = roll_sum_ret[start_idx:end_idx]
        roll_sum_log = roll_sum_log[start_idx:end_idx]
        roll_pos = roll_pos[start_idx:end_idx]
        roll_invalid = roll_invalid[start_idx:end_idx]

        arith = np.full_like(roll_sum_ret, np.nan, dtype=np.float64)
        geom = np.full_like(roll_sum_ret, np.nan, dtype=np.float64)
        rise = np.full_like(roll_sum_ret, np.nan, dtype=np.float64)

        valid = roll_counts > 0
        arith[valid] = roll_sum_ret[valid] / roll_counts[valid]
        rise[valid] = roll_pos[valid] / roll_counts[valid]

        valid_geom = valid & (~roll_invalid)
        geom[valid_geom] = np.exp(roll_sum_log[valid_geom] / roll_counts[valid_geom]) - 1.0

        support = roll_counts >= max(1, int(min_count))
        if require_full_window:
            global_idx = np.arange(start_idx, end_idx)
            support &= global_idx >= (window - 1)
        out = pd.DataFrame(
            {
                "horizon": self.horizons[h_idx][0],
                "count": roll_counts,
                "arith_mean": np.where(support, arith, np.nan),
                "geom_mean": np.where(support, geom, np.nan),
                "rise_prob": np.where(support, rise, np.nan),
            },
            index=pd.to_datetime(dates),
        )
        out.index.name = "date"
        return out


@dataclass
class StatsCollection:
    stats_map: Dict[str, Stats]
    pattern_trims: Dict[str, float | None] = field(default_factory=dict)

    @staticmethod
    def _pattern_colors(names: Iterable[str]) -> Dict[str, str]:
        palette = [
            "#D56062",
            "#067BC2",
            "#F37748",
            "#84BCDA",
            "#ECC30B",
        ]
        colors = iter(palette)
        mapping: Dict[str, str] = {}
        for name in names:
            if name in {"market", "benchmark", "default"}:
                mapping[name] = "black"
                continue
            color = next(colors, None)
            if color is None:
                colors = iter(palette)
                color = next(colors)
            mapping[name] = color
        return mapping

    def patterns(self) -> List[str]:
        return list(self.stats_map.keys())

    def get(self, name: str) -> Stats:
        if name not in self.stats_map:
            raise KeyError(f"Unknown pattern: {name}")
        return self.stats_map[name]

    def _resolve_trim_for_pattern(
        self,
        pattern_name: str,
        trim_quantile: float | None,
    ) -> float | None:
        if trim_quantile is not None:
            return _normalize_trim_quantile(trim_quantile)
        return _normalize_trim_quantile(self.pattern_trims.get(pattern_name))

    def _ensure_history_trim_supported(
        self,
        pattern_name: str,
        trim_quantile: float | None,
    ) -> None:
        effective_trim = self._resolve_trim_for_pattern(pattern_name, trim_quantile)
        if effective_trim is not None and effective_trim > 0.0:
            raise NotImplementedError(
                "trim_quantile은 현재 to_frame()/plot()에서만 지원됩니다. "
                "to_frame_history()/plot_history()는 아직 미지원입니다."
            )

    def to_frame(
        self,
        start=None,
        end=None,
        pattern: str | None = None,
        trim_quantile: float | None = None,
    ) -> pd.DataFrame:
        if not self.stats_map:
            return pd.DataFrame(
                columns=["period", "scope", "count", "arith_mean", "geom_mean", "rise_prob"]
            ).set_index(["period", "scope"])

        if pattern is not None:
            effective_trim = self._resolve_trim_for_pattern(pattern, trim_quantile)
            return self.get(pattern).to_frame(start, end, trim_quantile=effective_trim)

        frames = []
        keys = []
        for name, stats in self.stats_map.items():
            effective_trim = self._resolve_trim_for_pattern(name, trim_quantile)
            frames.append(stats.to_frame(start, end, trim_quantile=effective_trim))
            keys.append(name)
        combined = pd.concat(frames, keys=keys, names=["pattern"])
        return combined

    def to_frame_history(
        self,
        horizon: str | int = "1M",
        start=None,
        end=None,
        history_window: int = 252,
        min_count: int = 30,
        require_full_window: bool = True,
        pattern: str | None = None,
        trim_quantile: float | None = None,
    ) -> pd.DataFrame:
        if not self.stats_map:
            return pd.DataFrame(
                columns=["horizon", "count", "arith_mean", "geom_mean", "rise_prob"]
            )

        if pattern is not None:
            self._ensure_history_trim_supported(pattern, trim_quantile)
            return self.get(pattern).to_frame_history(
                horizon=horizon,
                start=start,
                end=end,
                history_window=history_window,
                min_count=min_count,
                require_full_window=require_full_window,
            )

        frames = []
        keys = []
        for name, stats in self.stats_map.items():
            self._ensure_history_trim_supported(name, trim_quantile)
            frames.append(
                stats.to_frame_history(
                    horizon=horizon,
                    start=start,
                    end=end,
                    history_window=history_window,
                    min_count=min_count,
                    require_full_window=require_full_window,
                )
            )
            keys.append(name)
        return pd.concat(frames, keys=keys, names=["pattern"])

    def plot(
        self,
        patterns: Iterable[str] | None = None,
        start=None,
        end=None,
        trim_quantile: float | None = None,
        figsize=(12, 4),
        rise_ylim=None,
        return_ylim=None,
    ):
        if not self.stats_map:
            raise ValueError("StatsCollection is empty.")

        if patterns is None:
            names = list(self.stats_map.keys())
        else:
            names = list(patterns)
        if not names:
            raise ValueError("No patterns selected for plotting.")

        color_map = self._pattern_colors(names)
        frames = []
        for name in names:
            effective_trim = self._resolve_trim_for_pattern(name, trim_quantile)
            df = self.get(name).to_frame(start, end, trim_quantile=effective_trim).reset_index()
            df["pattern"] = name
            frames.append(df)
        combined = pd.concat(frames, ignore_index=True)

        periods = combined["period"].unique().tolist()
        x = np.arange(len(periods))
        period_index = {label: idx for idx, label in enumerate(periods)}

        fig, axes = plt.subplots(1, 3, figsize=figsize, constrained_layout=True)

        for name, group in combined.groupby("pattern"):
            color = color_map.get(name, None)
            xs = group["period"].map(period_index).to_numpy(dtype=float)
            axes[0].plot(xs, group["arith_mean"] * 100.0, marker="o", color=color, label=name)
            axes[1].plot(
                xs, group["geom_mean"] * 100.0, marker="o", linestyle="-", color=color, label=name
            )
            axes[2].plot(xs, group["rise_prob"] * 100.0, marker="o", color=color, label=name)

        for ax, title, ylabel, draw_zero in [
            (axes[0], "Arithmetic Mean", "Return (%)", True),
            (axes[1], "Geometric Mean", "Return (%)", True),
            (axes[2], "Rise Probability (%)", "Rise Probability (%)", False),
        ]:
            if draw_zero:
                ax.axhline(0.0, color="gray", linewidth=0.8, linestyle="--")
            ax.set_xticks(x)
            ax.set_xticklabels(periods, rotation=0)
            ax.set_title(title)

        axes[0].set_ylabel("Return (%)")
        axes[2].set_ylabel("Rise Probability (%)")
        axes[0].legend()
        axes[2].axhline(50.0, color="gray", linewidth=0.8, linestyle="--")

        arith_vals = combined["arith_mean"].to_numpy(dtype=float) * 100.0
        geom_vals = combined["geom_mean"].to_numpy(dtype=float) * 100.0
        finite_vals = np.concatenate(
            [arith_vals[np.isfinite(arith_vals)], geom_vals[np.isfinite(geom_vals)]]
        )
        return_ylim_pct = _normalize_ylim_percent(return_ylim)
        if return_ylim_pct is not None:
            axes[0].set_ylim(*return_ylim_pct)
            axes[1].set_ylim(*return_ylim_pct)
        elif finite_vals.size:
            ymin = float(finite_vals.min())
            ymax = float(finite_vals.max())
            span = ymax - ymin
            margin = max(1e-4, 0.05 * span)
            axes[0].set_ylim(ymin - margin, ymax + margin)
            axes[1].set_ylim(ymin - margin, ymax + margin)

        rise_ylim_pct = _normalize_ylim_percent(rise_ylim)
        if rise_ylim_pct is not None:
            axes[2].set_ylim(*rise_ylim_pct)

        _share_return_y_axis(axes)
        _apply_integer_y_ticks(axes)

        return fig, axes

    def plot_compare(
        self,
        asof,
        short: str = "1Y",
        long: str = "3Y",
        patterns: Iterable[str] | None = None,
        trim_quantile: float | None = None,
        figsize=(12, 4),
        rise_ylim=None,
        return_ylim=None,
    ):
        asof_ts = pd.Timestamp(asof)
        short_start = _lookback_start(asof_ts, short)
        long_start = _lookback_start(asof_ts, long)
        if long_start >= short_start:
            raise ValueError("`long` must be longer than `short`.")

        short_result = self.plot(
            patterns=patterns,
            start=short_start,
            end=asof_ts,
            trim_quantile=trim_quantile,
            figsize=figsize,
            rise_ylim=rise_ylim,
            return_ylim=return_ylim,
        )
        long_result = self.plot(
            patterns=patterns,
            start=long_start,
            end=asof_ts,
            trim_quantile=trim_quantile,
            figsize=figsize,
            rise_ylim=rise_ylim,
            return_ylim=return_ylim,
        )

        fig_short, _ = short_result
        fig_long, _ = long_result
        fig_short.suptitle(f"Short ({short}): {short_start.date()} ~ {asof_ts.date()}")
        fig_long.suptitle(f"Long ({long}): {long_start.date()} ~ {asof_ts.date()}")

        return short_result, long_result

    def plot_history(
        self,
        horizon: str | int = "1M",
        patterns: Iterable[str] | None = None,
        start=None,
        end=None,
        trim_quantile: float | None = None,
        figsize=(12, 4),
        history_window: int = 252,
        min_count: int = 30,
        require_full_window: bool = True,
        rise_ylim=None,
        return_ylim=None,
    ):
        if not self.stats_map:
            raise ValueError("StatsCollection is empty.")

        if patterns is None:
            names = list(self.stats_map.keys())
        else:
            names = list(patterns)
        if not names:
            raise ValueError("No patterns selected for plotting.")

        color_map = self._pattern_colors(names)
        fig, axes = plt.subplots(1, 3, figsize=figsize, constrained_layout=True, sharex=True)

        first_dates = None
        label = None
        arith_series = []
        geom_series = []
        for name in names:
            df = self.to_frame_history(
                horizon=horizon,
                start=start,
                end=end,
                history_window=history_window,
                min_count=min_count,
                require_full_window=require_full_window,
                pattern=name,
                trim_quantile=trim_quantile,
            )
            dates = df.index.to_numpy()
            arith = _as_percent(df["arith_mean"].to_numpy(dtype=float))
            geom = _as_percent(df["geom_mean"].to_numpy(dtype=float))
            rise = _as_percent(df["rise_prob"].to_numpy(dtype=float))
            current_label = str(df["horizon"].iloc[0]) if not df.empty else str(horizon)
            if first_dates is None:
                first_dates = dates
                label = current_label
            elif not np.array_equal(first_dates, dates):
                raise ValueError("All patterns must share the same date index for plot_history.")
            color = color_map.get(name, None)
            axes[0].plot(dates, arith, label=name, color=color)
            axes[1].plot(dates, geom, linestyle="-", label=name, color=color)
            axes[2].plot(dates, rise, label=name, color=color)
            arith_series.append(arith)
            geom_series.append(geom)

        axes[0].axhline(0.0, color="gray", linewidth=0.8, linestyle="--")
        axes[1].axhline(0.0, color="gray", linewidth=0.8, linestyle="--")
        axes[0].set_ylabel("Return (%)")
        axes[2].set_ylabel("Rise Probability (%)")

        title_prefix = label if label is not None else "Horizon"
        axes[0].set_title(f"{title_prefix} Arithmetic Mean")
        axes[1].set_title(f"{title_prefix} Geometric Mean")
        axes[2].set_title(f"{title_prefix} Rise Probability")
        axes[2].axhline(50.0, color="gray", linewidth=0.8, linestyle="--")

        axes[0].legend()

        return_ylim_pct = _normalize_ylim_percent(return_ylim)
        if return_ylim_pct is not None:
            axes[0].set_ylim(*return_ylim_pct)
            axes[1].set_ylim(*return_ylim_pct)
        else:
            finite_blocks = []
            for series in arith_series + geom_series:
                finite = series[np.isfinite(series)]
                if finite.size:
                    finite_blocks.append(finite)
            if finite_blocks:
                combined = np.concatenate(finite_blocks)
                ymin = float(combined.min())
                ymax = float(combined.max())
                span = ymax - ymin
                margin = max(1e-4, 0.05 * span)
                axes[0].set_ylim(ymin - margin, ymax + margin)
                axes[1].set_ylim(ymin - margin, ymax + margin)
        rise_ylim_pct = _normalize_ylim_percent(rise_ylim)
        if rise_ylim_pct is not None:
            axes[2].set_ylim(*rise_ylim_pct)

        if first_dates is not None:
            _apply_date_ticks(axes, first_dates)
        _share_return_y_axis(axes)
        _apply_integer_y_ticks(axes)

        return fig, axes
