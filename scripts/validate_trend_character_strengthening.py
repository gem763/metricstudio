"""
trend_amount1.5x를 기준으로 패턴 특성 강화 후보를 비교한다.

목표:
1. 코호트 종목 수(selected/active count)를 줄인다.
2. CAGR, 변동성, IR, MDD, payoff_ratio 같은 운용 성과 특성 희생을 최소화한다.
3. 전체 기간 포트폴리오 성과와 이벤트 품질(benchmark 대비 gap)을 함께 본다.
"""

from __future__ import annotations

import contextlib
from dataclasses import dataclass
import io
from pathlib import Path
import sys

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.backtest import Backtest, Univ
from src.notebook_experiment_utils import summarize_vs_benchmark
from src.pattern import (
    AmountSurge,
    Bollinger,
    High,
    MFI,
    Pattern,
    RelativeStrength,
    Trending,
)
from src.simulate import TRADING_DAYS_PER_YEAR


START = "2000-01-01"
END = "2025-12-31"
BASELINE_NAME = "trend_amount1.5x"


@dataclass(frozen=True)
class TrendSpec:
    name: str
    bandwidth_max: float = 0.05
    high_threshold: float = 0.90
    mfi_threshold: float = 50.0
    amount_threshold: float | None = 1.5
    extra_trend_window: int | None = None
    rs_window: int | None = None
    rs_threshold: float = 0.0


def _build_pattern(spec: TrendSpec) -> Pattern:
    bb = Bollinger(name=f"{spec.name}_bb").on(
        trigger="breakout_up",
        breakout_cooldown_days=3,
        bandwidth_max=spec.bandwidth_max,
    )
    high52w = High(name=f"{spec.name}_52w").on(
        window=240,
        threshold=spec.high_threshold,
        stay_days=1,
    )
    uptrend = Trending(name=f"{spec.name}_ma200").on(trigger="ma_trend_up", window=200)
    mfi_high = MFI(name=f"{spec.name}_mfi").on(trigger="above", threshold=spec.mfi_threshold)

    pattern = bb + high52w + uptrend + mfi_high

    if spec.amount_threshold is not None:
        pattern = pattern + AmountSurge(name=f"{spec.name}_amt").on(
            window=20,
            threshold=spec.amount_threshold,
        )

    if spec.extra_trend_window is not None:
        pattern = pattern + Trending(name=f"{spec.name}_ma{spec.extra_trend_window}").on(
            trigger="ma_trend_up",
            window=spec.extra_trend_window,
        )

    if spec.rs_window is not None:
        pattern = pattern + RelativeStrength(name=f"{spec.name}_rs").on(
            market="kospi",
            window=spec.rs_window,
            threshold=spec.rs_threshold,
        )

    return pattern.named(spec.name)


def _candidate_specs() -> list[TrendSpec]:
    return [
        TrendSpec(name="trend_base", amount_threshold=None),
        TrendSpec(name="trend_amount1.5x"),
        TrendSpec(name="trend_bb0.04_amount1.5x", bandwidth_max=0.04),
        TrendSpec(name="trend_bb0.03_amount1.5x", bandwidth_max=0.03),
        TrendSpec(name="trend_high93_amount1.5x", high_threshold=0.93),
        TrendSpec(name="trend_high95_amount1.5x", high_threshold=0.95),
        TrendSpec(name="trend_mfi55_amount1.5x", mfi_threshold=55.0),
        TrendSpec(name="trend_mfi60_amount1.5x", mfi_threshold=60.0),
        TrendSpec(name="trend_amount1.8x", amount_threshold=1.8),
        TrendSpec(name="trend_amount2.0x", amount_threshold=2.0),
        TrendSpec(name="trend_ma60_amount1.5x", extra_trend_window=60),
        TrendSpec(name="trend_rs60_0p_amount1.5x", rs_window=60, rs_threshold=0.0),
        TrendSpec(name="trend_rs60_5p_amount1.5x", rs_window=60, rs_threshold=0.05),
        TrendSpec(
            name="trend_high93_mfi55_amount1.5x",
            high_threshold=0.93,
            mfi_threshold=55.0,
        ),
        TrendSpec(
            name="trend_high93_amount1.8x",
            high_threshold=0.93,
            amount_threshold=1.8,
        ),
        TrendSpec(
            name="trend_high93_ma60_amount1.5x",
            high_threshold=0.93,
            extra_trend_window=60,
        ),
    ]


def _annual_volatility(frame: pd.DataFrame) -> float:
    wealth = frame["wealth"].to_numpy(dtype=np.float64)
    if wealth.size < 3:
        return float("nan")
    daily_ret = wealth[1:] / wealth[:-1] - 1.0
    daily_ret = daily_ret[np.isfinite(daily_ret)]
    if daily_ret.size < 2:
        return float("nan")
    return float(np.std(daily_ret, ddof=1) * np.sqrt(float(TRADING_DAYS_PER_YEAR)))


def _cohort_metric(frame: pd.DataFrame, column: str, agg: str) -> float:
    values = frame[column].to_numpy(dtype=np.float64)
    values = values[np.isfinite(values)]
    if values.size == 0:
        return float("nan")
    if agg == "mean":
        return float(np.mean(values))
    if agg == "p90":
        return float(np.quantile(values, 0.90))
    if agg == "max":
        return float(np.max(values))
    raise ValueError(f"지원하지 않는 agg입니다: {agg}")


def _portfolio_summary(bt: Backtest, pattern_names: list[str]) -> pd.DataFrame:
    rows: list[dict[str, float | str]] = []
    for pattern_name in pattern_names:
        sim = bt.run(
            pattern=pattern_name,
            target_horizon="1M",
            trade_price_mode="당일종가",
        )
        frame = sim.to_frame(copy=False)
        meta = sim.summary()
        ann_vol = _annual_volatility(frame)
        cagr = float(meta["cagr"])
        rows.append(
            {
                "pattern": pattern_name,
                "final_wealth": 1.0 + float(meta["total_return"]),
                "cagr": cagr,
                "mdd": float(meta["max_drawdown"]),
                "ann_vol": ann_vol,
                "ir": cagr / ann_vol if np.isfinite(cagr) and np.isfinite(ann_vol) and ann_vol > 0.0 else float("nan"),
                "cohort_win_rate": float(meta["cohort_win_rate"]),
                "payoff_ratio": float(meta["cohort_payoff_ratio"]),
                "active_day_ratio": float(meta["active_day_ratio"]),
                "mean_exposure": float(np.nanmean(frame["exposure"].to_numpy(dtype=np.float64))),
                "selected_mean": _cohort_metric(frame, "selected_count", "mean"),
                "selected_p90": _cohort_metric(frame, "selected_count", "p90"),
                "selected_max": _cohort_metric(frame, "selected_count", "max"),
                "active_mean": _cohort_metric(frame, "active_count", "mean"),
                "active_p90": _cohort_metric(frame, "active_count", "p90"),
                "active_max": _cohort_metric(frame, "active_count", "max"),
            }
        )

    out = pd.DataFrame(rows).set_index("pattern")
    base = out.loc[BASELINE_NAME]
    out["selected_ratio"] = out["selected_mean"] / float(base["selected_mean"])
    out["active_ratio"] = out["active_mean"] / float(base["active_mean"])
    out["cagr_ratio"] = out["cagr"] / float(base["cagr"])
    out["ir_ratio"] = out["ir"] / float(base["ir"])
    out["payoff_ratio_rel"] = out["payoff_ratio"] / float(base["payoff_ratio"])
    out["mdd_ratio"] = np.abs(out["mdd"]) / abs(float(base["mdd"]))
    out["ann_vol_ratio"] = out["ann_vol"] / float(base["ann_vol"])

    retention_inputs = pd.DataFrame(
        {
            "cagr_keep": out["cagr_ratio"].clip(lower=0.0, upper=1.0),
            "ir_keep": out["ir_ratio"].clip(lower=0.0, upper=1.0),
            "payoff_keep": out["payoff_ratio_rel"].clip(lower=0.0, upper=1.0),
            "mdd_keep": (1.0 / out["mdd_ratio"]).clip(lower=0.0, upper=1.0),
            "vol_keep": (1.0 / out["ann_vol_ratio"]).clip(lower=0.0, upper=1.0),
        }
    )
    out["quality_retention"] = (
        0.35 * retention_inputs["cagr_keep"]
        + 0.30 * retention_inputs["ir_keep"]
        + 0.15 * retention_inputs["payoff_keep"]
        + 0.10 * retention_inputs["mdd_keep"]
        + 0.10 * retention_inputs["vol_keep"]
    )
    out["cohort_reduction"] = (1.0 - out["selected_ratio"]).clip(lower=0.0)
    out["efficiency_score"] = out["cohort_reduction"] * out["quality_retention"]
    return out.sort_values(
        ["efficiency_score", "quality_retention", "cagr"],
        ascending=[False, False, False],
        kind="stable",
    )


def _event_summary_table(stats, pattern_names: list[str]) -> pd.DataFrame:
    summary = summarize_vs_benchmark(
        stats=stats,
        benchmark_name="benchmark",
        pattern_names=pattern_names,
    )
    overall = summary[summary["window"] == "overall"].copy()
    overall_group = overall.groupby("pattern", observed=True).agg(
        count_ratio_mean=("count_ratio", "mean"),
        geom_gap_mean=("geom_ann_gap_after_cost", "mean"),
        rise_prob_mean=("rise_prob", "mean"),
    )
    overall_1m = (
        overall[overall["period"] == "1M"][
            ["pattern", "count_ratio", "geom_ann_gap_after_cost"]
        ]
        .rename(
            columns={
                "count_ratio": "count_ratio_1m",
                "geom_ann_gap_after_cost": "geom_gap_1m",
            }
        )
        .set_index("pattern")
    )

    windows = summary[summary["window"] != "overall"].copy()
    window_group = windows.groupby("pattern", observed=True).agg(
        positive_gap_cells=("geom_ann_gap_after_cost", lambda x: float(np.mean(x > 0.0))),
        worst_gap=("geom_ann_gap_after_cost", "min"),
        median_gap=("geom_ann_gap_after_cost", "median"),
    )

    out = overall_group.join(overall_1m, how="left").join(window_group, how="left")
    return out.reindex(pattern_names)


def _shortlist(portfolio: pd.DataFrame) -> pd.DataFrame:
    mask = (
        (portfolio["selected_ratio"] <= 0.85)
        & (portfolio["quality_retention"] >= 0.90)
    )
    out = portfolio[mask].copy()
    return out.sort_values(
        ["efficiency_score", "cagr"],
        ascending=[False, False],
        kind="stable",
    )


def main() -> None:
    pd.options.display.float_format = "{:.4f}".format

    specs = _candidate_specs()
    patterns = [_build_pattern(spec) for spec in specs]
    pattern_names = [spec.name for spec in specs]

    with contextlib.redirect_stderr(io.StringIO()):
        bt = Backtest(
            start=START,
            end=END,
            by="day",
            benchmark=Pattern(name="benchmark"),
            univ=Univ(market=["KOSPI", "KOSDAQ"]),
            db=0,
        )
        stats = bt.analyze(*patterns)
        portfolio = _portfolio_summary(bt, pattern_names)
    events = _event_summary_table(stats, pattern_names)
    merged = portfolio.join(events, how="left")

    shortlist = _shortlist(merged)
    if shortlist.empty:
        shortlist = merged.head(5).copy()

    display_cols = [
        "final_wealth",
        "cagr",
        "mdd",
        "ann_vol",
        "ir",
        "cohort_win_rate",
        "payoff_ratio",
        "selected_mean",
        "selected_p90",
        "active_mean",
        "selected_ratio",
        "quality_retention",
        "efficiency_score",
    ]
    shortlist_cols = [
        "cagr",
        "ann_vol",
        "ir",
        "mdd",
        "payoff_ratio",
        "selected_mean",
        "selected_ratio",
        "quality_retention",
        "efficiency_score",
        "geom_gap_mean",
        "geom_gap_1m",
        "positive_gap_cells",
        "worst_gap",
    ]

    print("=== Portfolio Summary | 1M cohort run ===")
    print(merged[display_cols].round(4).to_string())
    print()

    print("=== Shortlist | selected_ratio<=0.85 & quality_retention>=0.90 ===")
    print(shortlist[shortlist_cols].round(4).to_string())
    print()

    print("=== Event Quality vs benchmark | overall + subwindows ===")
    print(
        merged[
            [
                "count_ratio_mean",
                "count_ratio_1m",
                "geom_gap_mean",
                "geom_gap_1m",
                "rise_prob_mean",
                "positive_gap_cells",
                "worst_gap",
                "median_gap",
            ]
        ]
        .round(4)
        .to_string()
    )


if __name__ == "__main__":
    main()
