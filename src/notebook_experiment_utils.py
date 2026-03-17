from __future__ import annotations

import numpy as np
import pandas as pd

from src.simulate import BUY_FEE, SELL_FEE


HORIZONS = ["1M", "2M", "3M", "6M"]
HORIZON_DAYS = {"1M": 20, "2M": 40, "3M": 60, "6M": 120}
WINDOWS = [
    ("overall", "2000-01-01", "2025-12-31"),
    ("2000-2006", "2000-01-01", "2006-12-31"),
    ("2007-2012", "2007-01-01", "2012-12-31"),
    ("2013-2018", "2013-01-01", "2018-12-31"),
    ("2019-2025", "2019-01-01", "2025-12-31"),
]


def apply_cost(value: float) -> float:
    if not np.isfinite(value):
        return float("nan")
    out = ((1.0 + value) * (1.0 - SELL_FEE) / (1.0 + BUY_FEE)) - 1.0
    return out if out > -1.0 else float("nan")


def annualize_geom(value: float, horizon_days: int) -> float:
    if not np.isfinite(value) or value <= -1.0:
        return float("nan")
    return float((1.0 + value) ** (240.0 / float(horizon_days)) - 1.0)


def summarize_vs_benchmark(
    stats,
    benchmark_name: str,
    pattern_names: list[str],
    windows: list[tuple[str, str, str]] | None = None,
) -> pd.DataFrame:
    window_defs = WINDOWS if windows is None else windows
    rows: list[dict[str, object]] = []

    for window_label, start, end in window_defs:
        frame = stats.to_frame(start=start, end=end).reset_index()
        frame = frame[(frame["scope"] != "empty") & (frame["period"].isin(HORIZONS))].copy()
        benchmark_frame = frame[frame["pattern"] == benchmark_name][
            ["period", "count", "geom_mean", "rise_prob"]
        ].rename(
            columns={
                "count": "benchmark_count",
                "geom_mean": "benchmark_geom_mean",
                "rise_prob": "benchmark_rise_prob",
            }
        )
        merged = frame.merge(benchmark_frame, on="period", how="left")
        merged = merged[merged["pattern"].isin(pattern_names)].copy()
        merged["count_ratio"] = merged["count"] / merged["benchmark_count"]
        merged["geom_after_cost"] = merged["geom_mean"].map(apply_cost)
        merged["benchmark_geom_after_cost"] = merged["benchmark_geom_mean"].map(apply_cost)
        merged["geom_ann_after_cost"] = [
            annualize_geom(value, HORIZON_DAYS[period])
            for value, period in zip(merged["geom_after_cost"], merged["period"])
        ]
        merged["benchmark_geom_ann_after_cost"] = [
            annualize_geom(value, HORIZON_DAYS[period])
            for value, period in zip(merged["benchmark_geom_after_cost"], merged["period"])
        ]
        merged["geom_ann_gap_after_cost"] = (
            merged["geom_ann_after_cost"] - merged["benchmark_geom_ann_after_cost"]
        )

        for _, row in merged.iterrows():
            rows.append(
                {
                    "window": window_label,
                    "pattern": row["pattern"],
                    "period": row["period"],
                    "count": float(row["count"]),
                    "count_ratio": float(row["count_ratio"]),
                    "rise_prob": float(row["rise_prob"]),
                    "geom_ann_after_cost": float(row["geom_ann_after_cost"]),
                    "geom_ann_gap_after_cost": float(row["geom_ann_gap_after_cost"]),
                }
            )

    out = pd.DataFrame(rows)
    out["window"] = pd.Categorical(
        out["window"],
        categories=[label for label, _, _ in window_defs],
        ordered=True,
    )
    out["pattern"] = pd.Categorical(out["pattern"], categories=pattern_names, ordered=True)
    return out.sort_values(["window", "pattern", "period"]).reset_index(drop=True)


def overall_pivot(summary: pd.DataFrame, pattern_names: list[str], value_col: str) -> pd.DataFrame:
    overall = summary[summary["window"] == "overall"].copy()
    out = overall.pivot(index="pattern", columns="period", values=value_col)
    return out.reindex(pattern_names)


def window_pivot(summary: pd.DataFrame, pattern_names: list[str], value_col: str) -> pd.DataFrame:
    out = summary.pivot(index=["window", "pattern"], columns="period", values=value_col)
    desired_index = pd.MultiIndex.from_tuples(
        [(label, name) for label, _, _ in WINDOWS for name in pattern_names],
        names=["window", "pattern"],
    )
    return out.reindex(desired_index)


__all__ = [
    "HORIZONS",
    "HORIZON_DAYS",
    "WINDOWS",
    "apply_cost",
    "annualize_geom",
    "summarize_vs_benchmark",
    "overall_pivot",
    "window_pivot",
]
