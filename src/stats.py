"""Statistical aggregation and plotting helpers for backtests."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Iterable, List, Tuple

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd


Horizon = Tuple[str, int]


@dataclass
class Stats:
    dates: np.ndarray
    horizons: List[Horizon]
    counts: np.ndarray
    sum_ret: np.ndarray
    sum_log: np.ndarray
    pos_counts: np.ndarray
    geom_invalid: np.ndarray

    @classmethod
    def create(cls, dates: np.ndarray, horizons: Iterable[Horizon]) -> "Stats":
        horizon_list = list(horizons)
        length = len(dates)
        num_h = len(horizon_list)
        return cls(
            dates=dates.copy(),
            horizons=horizon_list,
            counts=np.zeros((num_h, length), dtype=np.int64),
            sum_ret=np.zeros((num_h, length), dtype=np.float64),
            sum_log=np.zeros((num_h, length), dtype=np.float64),
            pos_counts=np.zeros((num_h, length), dtype=np.int64),
            geom_invalid=np.zeros((num_h, length), dtype=np.bool_),
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

    def to_frame(self, start=None, end=None) -> pd.DataFrame:
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
            cnt = float(self.counts[h_idx, start_idx:end_idx].sum())
            sum_ret = float(self.sum_ret[h_idx, start_idx:end_idx].sum())
            sum_log = float(self.sum_log[h_idx, start_idx:end_idx].sum())
            pos = float(self.pos_counts[h_idx, start_idx:end_idx].sum())
            invalid = bool(self.geom_invalid[h_idx, start_idx:end_idx].any())

            if cnt == 0:
                rows.append(
                    {
                        "period": label,
                        "scope": scope_label,
                        "count": 0.0,
                        "arith_mean": float("nan"),
                        "geom_mean": float("nan"),
                        "rise_prob": float("nan"),
                    }
                )
                continue

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

    def plot(self, start=None, end=None, figsize=(12, 4), rise_ylim=None, return_ylim=None):
        df = self.to_frame(start, end).reset_index()
        if df.empty:
            raise ValueError("No data available for the specified range.")

        periods = df["period"].tolist()
        arith = df["arith_mean"].to_numpy(dtype=float)
        geom = df["geom_mean"].to_numpy(dtype=float)
        rise = df["rise_prob"].to_numpy(dtype=float)
        x = np.arange(len(periods))

        fig, axes = plt.subplots(1, 3, figsize=figsize, constrained_layout=True)

        axes[0].plot(x, arith, marker="o", color="tab:blue")
        axes[0].axhline(0.0, color="gray", linewidth=0.8, linestyle="--")
        axes[0].set_xticks(x)
        axes[0].set_xticklabels(periods, rotation=45)
        axes[0].set_ylabel("Return")
        axes[0].set_title("Arithmetic Mean")

        axes[1].plot(x, geom, marker="o", linestyle="--", color="tab:orange")
        axes[1].axhline(0.0, color="gray", linewidth=0.8, linestyle="--")
        axes[1].set_xticks(x)
        axes[1].set_xticklabels(periods, rotation=45)
        axes[1].set_title("Geometric Mean")

        axes[1].sharey(axes[0])

        combined = np.concatenate(
            [
                arith[np.isfinite(arith)],
                geom[np.isfinite(geom)],
            ]
        )
        if return_ylim is not None:
            axes[0].set_ylim(*return_ylim)
            axes[1].set_ylim(*return_ylim)
        elif combined.size:
            ymin = float(combined.min())
            ymax = float(combined.max())
            span = ymax - ymin
            margin = max(1e-4, 0.05 * span)
            axes[0].set_ylim(ymin - margin, ymax + margin)
            axes[1].set_ylim(ymin - margin, ymax + margin)

        axes[2].plot(x, rise * 100.0, marker="o", color="tab:green")
        axes[2].set_xticks(x)
        axes[2].set_xticklabels(periods, rotation=45)
        axes[2].set_ylabel("Rise Probability (%)")
        axes[2].set_title("Rise Probability")
        if rise_ylim is None:
            pass
        else:
            axes[2].set_ylim(*rise_ylim)

        return fig, axes

    def plot_history(
        self,
        horizon: str | int = "1M",
        start=None,
        end=None,
        figsize=(12, 4),
        history_window: int = 252,
        rise_ylim=None,
        return_ylim=None,
    ):
        dates, arith, geom, rise, label = self._history_series_for_plot(
            horizon=horizon, start=start, end=end, history_window=history_window
        )

        fig, axes = plt.subplots(1, 3, figsize=figsize, constrained_layout=True, sharex=True)

        axes[0].plot(dates, arith, color="tab:blue")
        axes[0].axhline(0.0, color="gray", linewidth=0.8, linestyle="--")
        axes[0].set_title(f"{label} Arithmetic Mean")
        axes[0].set_ylabel("Return")

        axes[1].plot(dates, geom, color="tab:orange", linestyle="--")
        axes[1].axhline(0.0, color="gray", linewidth=0.8, linestyle="--")
        axes[1].set_title(f"{label} Geometric Mean")

        axes[2].plot(dates, rise * 100.0, color="tab:green")
        axes[2].set_title(f"{label} Rise Probability")
        axes[2].set_ylabel("Rise Probability (%)")

        if return_ylim is not None:
            axes[0].set_ylim(*return_ylim)
            axes[1].set_ylim(*return_ylim)
        if rise_ylim is not None:
            axes[2].set_ylim(*rise_ylim)

        return fig, axes

    @staticmethod
    def _rolling_sum_1d(values: np.ndarray, window: int) -> np.ndarray:
        if window <= 1:
            return values.astype(np.float64, copy=True)
        cumsum = np.cumsum(values, dtype=np.float64)
        out = cumsum.copy()
        if values.shape[0] >= window:
            out[window:] = cumsum[window:] - cumsum[:-window]
        return out

    def _history_series_for_plot(self, horizon="1M", start=None, end=None, history_window: int = 252):
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

        roll_counts = self._rolling_sum_1d(counts, window)
        roll_sum_ret = self._rolling_sum_1d(sum_ret, window)
        roll_sum_log = self._rolling_sum_1d(sum_log, window)
        roll_pos = self._rolling_sum_1d(pos, window)
        roll_invalid = self._rolling_sum_1d(invalid.astype(np.float64), window) > 0.0

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

        return dates, arith, geom, rise, self.horizons[h_idx][0]


@dataclass
class StatsCollection:
    stats_map: Dict[str, Stats]

    def patterns(self) -> List[str]:
        return list(self.stats_map.keys())

    def get(self, name: str) -> Stats:
        if name not in self.stats_map:
            raise KeyError(f"Unknown pattern: {name}")
        return self.stats_map[name]

    def to_frame(self, start=None, end=None, pattern: str | None = None) -> pd.DataFrame:
        if not self.stats_map:
            return pd.DataFrame(
                columns=["period", "scope", "count", "arith_mean", "geom_mean", "rise_prob"]
            ).set_index(["period", "scope"])

        if pattern is not None:
            return self.get(pattern).to_frame(start, end)

        frames = []
        keys = []
        for name, stats in self.stats_map.items():
            frames.append(stats.to_frame(start, end))
            keys.append(name)
        combined = pd.concat(frames, keys=keys, names=["pattern"])
        return combined

    def plot(
        self,
        patterns: Iterable[str] | None = None,
        start=None,
        end=None,
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

        # plot single pattern via Stats.plot for consistency
        if len(names) == 1:
            stats = self.get(names[0])
            return stats.plot(
                start=start, end=end, figsize=figsize, rise_ylim=rise_ylim, return_ylim=return_ylim
            )

        frames = []
        for name in names:
            df = self.get(name).to_frame(start, end).reset_index()
            df["pattern"] = name
            frames.append(df)
        combined = pd.concat(frames, ignore_index=True)

        periods = combined["period"].unique().tolist()
        x = np.arange(len(periods))
        period_index = {label: idx for idx, label in enumerate(periods)}

        fig, axes = plt.subplots(1, 3, figsize=figsize, constrained_layout=True)

        for name, group in combined.groupby("pattern"):
            xs = group["period"].map(period_index).to_numpy(dtype=float)
            axes[0].plot(xs, group["arith_mean"], marker="o", label=name)
            axes[1].plot(xs, group["geom_mean"], marker="o", linestyle="--", label=name)
            axes[2].plot(xs, group["rise_prob"] * 100.0, marker="o", label=name)

        for ax, title, ylabel, draw_zero in [
            (axes[0], "Arithmetic Mean", "Return", True),
            (axes[1], "Geometric Mean", "Return", True),
            (axes[2], "Rise Probability (%)", "Rise Probability (%)", False),
        ]:
            if draw_zero:
                ax.axhline(0.0, color="gray", linewidth=0.8, linestyle="--")
            ax.set_xticks(x)
            ax.set_xticklabels(periods, rotation=45)
            ax.set_title(title)
            ax.legend()

        axes[0].set_ylabel("Return")
        axes[1].set_ylabel("Return")
        axes[2].set_ylabel("Rise Probability (%)")
        if rise_ylim is not None:
            axes[2].set_ylim(*rise_ylim)

        arith_vals = combined["arith_mean"].to_numpy(dtype=float)
        geom_vals = combined["geom_mean"].to_numpy(dtype=float)
        finite_vals = np.concatenate(
            [arith_vals[np.isfinite(arith_vals)], geom_vals[np.isfinite(geom_vals)]]
        )
        if return_ylim is not None:
            axes[0].set_ylim(*return_ylim)
            axes[1].set_ylim(*return_ylim)
        elif finite_vals.size:
            ymin = float(finite_vals.min())
            ymax = float(finite_vals.max())
            span = ymax - ymin
            margin = max(1e-4, 0.05 * span)
            axes[0].set_ylim(ymin - margin, ymax + margin)
            axes[1].set_ylim(ymin - margin, ymax + margin)

        return fig, axes

    def plot_history(
        self,
        horizon: str | int = "1M",
        patterns: Iterable[str] | None = None,
        start=None,
        end=None,
        figsize=(12, 4),
        history_window: int = 252,
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

        if len(names) == 1:
            return self.get(names[0]).plot_history(
                horizon=horizon,
                start=start,
                end=end,
                figsize=figsize,
                history_window=history_window,
                rise_ylim=rise_ylim,
                return_ylim=return_ylim,
            )

        fig, axes = plt.subplots(1, 3, figsize=figsize, constrained_layout=True, sharex=True)

        first_dates = None
        label = None
        for name in names:
            stats = self.get(name)
            dates, arith, geom, rise, current_label = stats._history_series_for_plot(
                horizon=horizon, start=start, end=end, history_window=history_window
            )
            if first_dates is None:
                first_dates = dates
                label = current_label
            elif not np.array_equal(first_dates, dates):
                raise ValueError("All patterns must share the same date index for plot_history.")
            axes[0].plot(dates, arith, label=name)
            axes[1].plot(dates, geom, linestyle="--", label=name)
            axes[2].plot(dates, rise * 100.0, label=name)

        axes[0].axhline(0.0, color="gray", linewidth=0.8, linestyle="--")
        axes[1].axhline(0.0, color="gray", linewidth=0.8, linestyle="--")
        axes[0].set_ylabel("Return")
        axes[1].set_ylabel("Return")
        axes[2].set_ylabel("Rise Probability (%)")

        title_prefix = label if label is not None else "Horizon"
        axes[0].set_title(f"{title_prefix} Arithmetic Mean")
        axes[1].set_title(f"{title_prefix} Geometric Mean")
        axes[2].set_title(f"{title_prefix} Rise Probability")

        axes[0].legend()
        axes[1].legend()
        axes[2].legend()

        if return_ylim is not None:
            axes[0].set_ylim(*return_ylim)
            axes[1].set_ylim(*return_ylim)
        if rise_ylim is not None:
            axes[2].set_ylim(*rise_ylim)

        return fig, axes
