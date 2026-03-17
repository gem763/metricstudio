"""
panic_rebound_risk 레짐에서 반등 후보 패턴들을 비교한다.
"""

from __future__ import annotations

from pathlib import Path
import sys

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.backtest import Backtest, Univ
from src.pattern import Pattern, Disparity, MFI, PanicRebound
from src.regime import Regime
from src.simulate import BUY_FEE, SELL_FEE


HORIZONS = ["1M", "2M", "3M", "6M"]
HORIZON_DAYS = {"1M": 20, "2M": 40, "3M": 60, "6M": 120}
WINDOWS = [
    ("2000-01-01", "2006-12-31", "2000-2006"),
    ("2007-01-01", "2012-12-31", "2007-2012"),
    ("2013-01-01", "2018-12-31", "2013-2018"),
    ("2019-01-01", "2025-12-31", "2019-2025"),
]


def _apply_cost(value: float) -> float:
    if not np.isfinite(value):
        return float("nan")
    out = ((1.0 + value) * (1.0 - SELL_FEE) / (1.0 + BUY_FEE)) - 1.0
    return out if out > -1.0 else float("nan")


def _annualize_geom(value: float, horizon_days: int) -> float:
    if not np.isfinite(value) or value <= -1.0:
        return float("nan")
    return float((1.0 + value) ** (240.0 / float(horizon_days)) - 1.0)


def _build_disparity_pattern(regime: Regime):
    pattern = Disparity(name="disparity0.9", window=20).on(
        threshold=0.9,
        stay_days=1,
        cooldown_days=5,
    ).when(regime)
    pattern.name = "disparity0.9"
    return pattern


def _build_mfi_pattern(regime: Regime):
    pattern = MFI(name="mfi_oversold").on(
        trigger="oversold_rebound",
        lower=20,
        stay_days=1,
        cooldown_days=5,
    ).when(regime)
    pattern.name = "mfi_oversold"
    return pattern


def _build_panic_rebound_pattern(
    regime: Regime,
    name: str,
    volume_spike: bool,
):
    pattern = PanicRebound(name=name).on(
        drawdown_window=20,
        drawdown_min=-0.18,
        rebound_days=3,
        volume_spike=volume_spike,
        volume_window=20,
        volume_threshold=1.5,
    ).when(regime)
    pattern.name = name
    return pattern


def build_validation_table() -> pd.DataFrame:
    bt = Backtest(
        start="2000-01-01",
        end="2025-12-31",
        by="day",
        univ=Univ(market=["KOSPI", "KOSDAQ"]),
        db=0,
    )
    regime = Regime().on(kind="panic_rebound_risk", market="kospi")
    benchmark = Pattern(name="benchmark").when(regime)
    disparity = _build_disparity_pattern(regime)
    mfi = _build_mfi_pattern(regime)
    panic_plain = _build_panic_rebound_pattern(regime, "panic_rebound", volume_spike=False)
    panic_volume = _build_panic_rebound_pattern(regime, "panic_rebound+volume1.5x", volume_spike=True)
    stats = bt.analyze(benchmark, disparity, mfi, panic_plain, panic_volume)

    rows: list[dict[str, object]] = []
    for start, end, window_label in [("2000-01-01", "2025-12-31", "overall"), *WINDOWS]:
        frame = stats.to_frame(start=start, end=end).reset_index()
        frame = frame[(frame["scope"] != "empty") & (frame["period"].isin(HORIZONS))].copy()
        benchmark_frame = frame[frame["pattern"] == "benchmark"][
            ["period", "count", "geom_mean", "rise_prob"]
        ].rename(
            columns={
                "count": "benchmark_count",
                "geom_mean": "benchmark_geom_mean",
                "rise_prob": "benchmark_rise_prob",
            }
        )
        merged = frame.merge(benchmark_frame, on="period", how="left")
        merged = merged[
            merged["pattern"].isin(
                ["disparity0.9", "mfi_oversold", "panic_rebound", "panic_rebound+volume1.5x"]
            )
        ].copy()
        merged["count_ratio"] = merged["count"] / merged["benchmark_count"]
        merged["geom_after_cost"] = merged["geom_mean"].map(_apply_cost)
        merged["benchmark_geom_after_cost"] = merged["benchmark_geom_mean"].map(_apply_cost)
        merged["geom_ann_after_cost"] = [
            _annualize_geom(value, HORIZON_DAYS[period])
            for value, period in zip(merged["geom_after_cost"], merged["period"])
        ]
        merged["benchmark_geom_ann_after_cost"] = [
            _annualize_geom(value, HORIZON_DAYS[period])
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

    return pd.DataFrame(rows)


def main() -> None:
    table = build_validation_table()
    print(table.to_string(index=False))


if __name__ == "__main__":
    main()
