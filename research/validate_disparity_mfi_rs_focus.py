"""
이격도 + MFI + 상대강도 조합을 좁은 그리드로 다시 검증한다.

초점:
- disparity threshold: 0.87 / 0.88 / 0.89
- MFI below threshold: 20 / 25 / 30
- 5일 상대강도: -6% / -8% / -10%
- short horizon: 1W / 2W / 3W
"""

from __future__ import annotations

from pathlib import Path
import sys

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from metricstudio.backtest import Backtest
from metricstudio.univ import Univ
from metricstudio.patterns import AllStockPattern, AmountSurge, BasePattern, Bollinger, Disparity, High, MFI, RelativeStrength, Trending
from metricstudio.regime import Regime
from metricstudio.simulate import BUY_FEE, SELL_FEE


SCREEN_HORIZONS = ["1W", "2W", "3W"]
HORIZON_DAYS = {"1W": 5, "2W": 10, "3W": 15}
DISPARITY_THRESHOLDS = [0.87, 0.88, 0.89]
MFI_THRESHOLDS = [20, 25, 30]
RS_THRESHOLDS = [-0.06, -0.08, -0.10]


def apply_cost(value: float) -> float:
    if not np.isfinite(value):
        return float("nan")
    out = ((1.0 + value) * (1.0 - SELL_FEE) / (1.0 + BUY_FEE)) - 1.0
    return out if out > -1.0 else float("nan")


def annualize_geom(value: float, horizon_days: int) -> float:
    if not np.isfinite(value) or value <= -1.0:
        return float("nan")
    return float((1.0 + value) ** (240.0 / float(horizon_days)) - 1.0)


def _build_trend_pattern(name: str) -> BasePattern:
    bb = Bollinger(name=f"{name}_bb").on(
        trigger="breakout_up",
        breakout_cooldown_days=3,
        bandwidth_max=0.05,
    )
    high52w = High(name=f"{name}_52w").on(window=240, threshold=0.90, stay_days=1)
    uptrend = Trending(name=f"{name}_ma200").on(trigger="ma_trend_up", window=200)
    mfi_high = MFI(name=f"{name}_mfi50").on(trigger="above", threshold=50)
    amount15 = AmountSurge(name=f"{name}_amt15").on(window=20, threshold=1.5)
    return (bb + high52w + uptrend + mfi_high + amount15).named(name)


def _candidate_name(
    disparity_threshold: float,
    mfi_threshold: int,
    rs_threshold: float,
) -> str:
    disp_text = int(round(disparity_threshold * 100))
    rs_text = int(round(abs(rs_threshold) * 100))
    return f"disp{disp_text}_mfi{mfi_threshold}_rs{rs_text}"


def _build_candidate(
    disparity_threshold: float,
    mfi_threshold: int,
    rs_threshold: float,
) -> BasePattern:
    name = _candidate_name(disparity_threshold, mfi_threshold, rs_threshold)
    disparity = Disparity(window=20, name=f"{name}_disp").on(
        threshold=disparity_threshold,
        stay_days=5,
        cooldown_days=5,
    )
    ma60_down = Trending(name=f"{name}_ma60down").market("kospi").on(
        window=60,
        trigger="ma_trend_down",
        stay_days=1,
        cooldown_days=0,
    )
    mfi = MFI(name=f"{name}_mfi").on(
        trigger="below",
        threshold=mfi_threshold,
        stay_days=1,
        cooldown_days=0,
    )
    rs = RelativeStrength(name=f"{name}_rs").on(
        market="kospi",
        window=5,
        trigger="below",
        threshold=rs_threshold,
        stay_days=1,
        cooldown_days=5,
    )
    return (disparity + ma60_down + mfi + rs).named(name)


def _build_candidates() -> list[BasePattern]:
    out: list[BasePattern] = []
    for disparity_threshold in DISPARITY_THRESHOLDS:
        for mfi_threshold in MFI_THRESHOLDS:
            for rs_threshold in RS_THRESHOLDS:
                out.append(_build_candidate(disparity_threshold, mfi_threshold, rs_threshold))
    return out


def _summarize_short_vs_benchmark(stats, pattern_names: list[str]) -> pd.DataFrame:
    frame = stats.to_frame(start="2000-01-01", end="2025-12-31").reset_index()
    frame = frame[(frame["scope"] != "empty") & (frame["period"].isin(SCREEN_HORIZONS))].copy()
    benchmark = frame[frame["pattern"] == "benchmark"][
        ["period", "count", "geom_mean", "rise_prob"]
    ].rename(
        columns={
            "count": "benchmark_count",
            "geom_mean": "benchmark_geom_mean",
            "rise_prob": "benchmark_rise_prob",
        }
    )
    merged = frame.merge(benchmark, on="period", how="left")
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
    merged["rise_prob_gap"] = merged["rise_prob"] - merged["benchmark_rise_prob"]
    return merged.sort_values(["pattern", "period"]).reset_index(drop=True)


def _pivot(summary: pd.DataFrame, pattern_names: list[str], value_col: str) -> pd.DataFrame:
    out = summary.pivot(index="pattern", columns="period", values=value_col)
    return out.reindex(pattern_names)


def _build_ranking(
    gap_pivot: pd.DataFrame,
    count_ratio_pivot: pd.DataFrame,
    rise_prob_gap_pivot: pd.DataFrame,
    trend_name: str,
) -> pd.DataFrame:
    trend_gap = gap_pivot.loc[trend_name, SCREEN_HORIZONS]
    trend_score = float(trend_gap.mean())
    rows: list[dict[str, object]] = []

    for pattern_name in gap_pivot.index:
        row = gap_pivot.loc[pattern_name, SCREEN_HORIZONS].astype(float)
        best_horizon = str(row.idxmax())
        rows.append(
            {
                "pattern": pattern_name,
                "gap_1w": float(row["1W"]),
                "gap_2w": float(row["2W"]),
                "gap_3w": float(row["3W"]),
                "short_gap_score": float(row.mean()),
                "score_vs_trend": float(row.mean() - trend_score),
                "best_horizon": best_horizon,
                "best_gap": float(row[best_horizon]),
                "count_ratio_mean": float(count_ratio_pivot.loc[pattern_name, SCREEN_HORIZONS].mean()),
                "rise_prob_gap_mean": float(rise_prob_gap_pivot.loc[pattern_name, SCREEN_HORIZONS].mean()),
                "wins_vs_trend": int((row > trend_gap).sum()),
            }
        )

    return pd.DataFrame(rows).sort_values(
        ["short_gap_score", "wins_vs_trend", "count_ratio_mean", "best_gap"],
        ascending=[False, False, False, False],
        kind="stable",
    ).reset_index(drop=True)


def _collect_run_summary(bt: Backtest, pattern_names: list[str]) -> pd.DataFrame:
    rows: list[dict[str, float | str]] = []
    for horizon in SCREEN_HORIZONS:
        for pattern_name in pattern_names:
            sim = bt.run(
                pattern=pattern_name,
                target_horizon=horizon,
                trade_price_mode="당일종가",
            )
            meta = sim.summary()
            rows.append(
                {
                    "horizon": horizon,
                    "pattern": pattern_name,
                    "final_wealth": float(1.0 + meta["total_return"]),
                    "cagr": float(meta["cagr"]),
                    "mdd": float(meta["max_drawdown"]),
                    "active_day_ratio": float(meta["active_day_ratio"]),
                    "cohort_win_rate": float(meta["cohort_win_rate"]),
                    "cohort_payoff_ratio": float(meta["cohort_payoff_ratio"]),
                }
            )
    return pd.DataFrame(rows)


def _build_router_summary(top_rows: pd.DataFrame) -> pd.DataFrame:
    quiet_contrarian = Regime().on(kind="quiet_tag", market="kospi") + Regime().on(
        kind="contrarian",
        market="kospi",
    )
    bt = Backtest(
        start="2000-01-01",
        end="2025-12-31",
        by="day",
        univ=Univ(market=["KOSPI", "KOSDAQ"]),
        db=0,
    )

    trend_all = _build_trend_pattern("trend_amount1.5x")
    trend_no_quiet_contrarian = (
        _build_trend_pattern("trend_no_quiet_contrarian")
        .when(~quiet_contrarian)
        .named("trend_no_quiet_contrarian")
    )

    routers: list[BasePattern] = []
    router_names = ["trend_amount1.5x", "trend_no_quiet_contrarian"]
    for _, row in top_rows.iterrows():
        pattern_name = str(row["pattern"])
        best_horizon = str(row["best_horizon"])
        tokens = pattern_name.split("_")
        disparity_threshold = float(tokens[0].replace("disp", "")) / 100.0
        mfi_threshold = int(tokens[1].replace("mfi", ""))
        rs_threshold = -float(tokens[2].replace("rs", "")) / 100.0
        branch_name = f"{pattern_name}_{best_horizon.lower()}"
        router_name = f"switch_{branch_name}"
        router = (
            _build_trend_pattern(f"trend_ex_{branch_name}")
            .when(~quiet_contrarian)
            .named(f"trend_ex_{branch_name}")
            | _build_candidate(disparity_threshold, mfi_threshold, rs_threshold)
            .trade(target_horizon=best_horizon)
            .when(quiet_contrarian)
            .named(f"contra_{branch_name}")
        ).named(router_name)
        routers.append(router)
        router_names.append(router_name)

    bt.analyze(trend_all, trend_no_quiet_contrarian, *routers, include_base=False)

    rows: list[dict[str, float | str]] = []
    for pattern_name in router_names:
        sim = bt.run(
            pattern=pattern_name,
            target_horizon="1M",
            trade_price_mode="당일종가",
        )
        meta = sim.summary()
        rows.append(
            {
                "pattern": pattern_name,
                "final_wealth": float(1.0 + meta["total_return"]),
                "cagr": float(meta["cagr"]),
                "mdd": float(meta["max_drawdown"]),
                "active_day_ratio": float(meta["active_day_ratio"]),
                "cohort_win_rate": float(meta["cohort_win_rate"]),
                "cohort_payoff_ratio": float(meta["cohort_payoff_ratio"]),
            }
        )
    return pd.DataFrame(rows)


def main() -> None:
    bt = Backtest(
        start="2000-01-01",
        end="2025-12-31",
        by="day",
        benchmark=AllStockPattern(name="benchmark"),
        univ=Univ(market=["KOSPI", "KOSDAQ"]),
        db=0,
    )

    trend = _build_trend_pattern("trend_amount1.5x")
    candidates = _build_candidates()
    candidate_names = [pattern.name for pattern in candidates]
    compare_names = [trend.name, *candidate_names]

    stats = bt.analyze(trend, *candidates)
    summary = _summarize_short_vs_benchmark(stats, compare_names)
    gap_pivot = _pivot(summary, compare_names, "geom_ann_gap_after_cost")
    count_ratio_pivot = _pivot(summary, compare_names, "count_ratio")
    rise_prob_gap_pivot = _pivot(summary, compare_names, "rise_prob_gap")
    ranking = _build_ranking(gap_pivot, count_ratio_pivot, rise_prob_gap_pivot, trend.name)

    top_candidates = ranking.loc[ranking["pattern"] != trend.name, "pattern"].head(5).tolist()
    run_summary = _collect_run_summary(bt, ["benchmark", trend.name, *top_candidates])
    router_targets = ranking[ranking["pattern"] != trend.name].head(3)[["pattern", "best_horizon"]].copy()
    router_summary = _build_router_summary(router_targets)

    print("=== Focus Grid Ranking ===")
    print(ranking.round(4).to_string(index=False))
    print()

    print("=== Gap Pivot vs benchmark ===")
    print(gap_pivot.round(4).to_string())
    print()

    print("=== Run Summary | benchmark + trend + top5 ===")
    print(run_summary.round(4).to_string(index=False))
    print()

    print("=== Router Summary | quiet_contrarian branch ===")
    print(router_summary.round(4).to_string(index=False))


if __name__ == "__main__":
    main()
