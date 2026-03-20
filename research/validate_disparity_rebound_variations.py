"""
이격도 기반 contrarian 신호를 반등 확인 조건과 결합해 short-horizon으로 검증한다.

가설:
1. 깊은 이격 + 유지 + MA60 하향은 반전 후보군을 만든다.
2. 반등 확인 신호(Bollinger 하단, MFI, 거래대금, 상대강도)를 더하면 정보 우위가 좋아질 수 있다.
3. 이런 신호는 1M보다 1W~3W에서 더 의미 있을 수 있다.
"""

from __future__ import annotations

from pathlib import Path
import sys

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from metricstudio.backtest import Backtest
from research.notebook_experiment_utils import annualize_geom, apply_cost, build_default_backtest, summarize_periods_vs_benchmark
from metricstudio.patterns import AllStockPattern, AmountSurge, BasePattern, Bollinger, Disparity, High, MFI, RelativeStrength, Trending
from metricstudio.regime import Regime


SCREEN_HORIZONS = ["1W", "2W", "3W"]
HORIZON_DAYS = {
    "1W": 5,
    "2W": 10,
    "3W": 15,
    "1M": 20,
    "2M": 40,
    "3M": 60,
    "6M": 120,
}

CANDIDATE_SPECS = [
    {"name": "disp90_core", "threshold": 0.90, "extras": ()},
    {"name": "disp88_core", "threshold": 0.88, "extras": ()},
    {"name": "disp90_bbnear", "threshold": 0.90, "extras": ("bb_near_down",)},
    {"name": "disp88_bbnear", "threshold": 0.88, "extras": ("bb_near_down",)},
    {"name": "disp90_mfi25", "threshold": 0.90, "extras": ("mfi_below_25",)},
    {"name": "disp88_mfi25", "threshold": 0.88, "extras": ("mfi_below_25",)},
    {"name": "disp90_mfi_rebound", "threshold": 0.90, "extras": ("mfi_rebound",)},
    {"name": "disp88_mfi_rebound", "threshold": 0.88, "extras": ("mfi_rebound",)},
    {"name": "disp90_amt15", "threshold": 0.90, "extras": ("amount_15",)},
    {"name": "disp88_amt15", "threshold": 0.88, "extras": ("amount_15",)},
    {"name": "disp90_rs5", "threshold": 0.90, "extras": ("rs_5d_loser",)},
    {"name": "disp88_rs5", "threshold": 0.88, "extras": ("rs_5d_loser",)},
    {"name": "disp90_bbnear_mfi_rebound", "threshold": 0.90, "extras": ("bb_near_down", "mfi_rebound")},
    {"name": "disp88_bbnear_mfi_rebound", "threshold": 0.88, "extras": ("bb_near_down", "mfi_rebound")},
    {"name": "disp90_mfi_rebound_amt15", "threshold": 0.90, "extras": ("mfi_rebound", "amount_15")},
    {"name": "disp88_mfi_rebound_amt15", "threshold": 0.88, "extras": ("mfi_rebound", "amount_15")},
    {"name": "disp90_mfi25_rs5", "threshold": 0.90, "extras": ("mfi_below_25", "rs_5d_loser")},
    {"name": "disp88_mfi25_rs5", "threshold": 0.88, "extras": ("mfi_below_25", "rs_5d_loser")},
]


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


def _build_disparity_base(name: str, threshold: float) -> BasePattern:
    disparity = Disparity(window=20, name=f"{name}_disp").on(
        threshold=threshold,
        stay_days=5,
        cooldown_days=5,
    )
    ma60_down = Trending(name=f"{name}_ma60down").market("kospi").on(
        window=60,
        trigger="ma_trend_down",
        stay_days=1,
        cooldown_days=0,
    )
    return (disparity + ma60_down).named(f"{name}_base")


def _build_extra(name: str, kind: str) -> BasePattern:
    if kind == "bb_near_down":
        return Bollinger(name=f"{name}_bbnear").on(
            trigger="near_down",
            near_tolerance=0.02,
            near_stay_days=1,
        )
    if kind == "mfi_below_25":
        return MFI(name=f"{name}_mfi25").on(
            trigger="below",
            threshold=25,
            stay_days=1,
            cooldown_days=0,
        )
    if kind == "mfi_rebound":
        return MFI(name=f"{name}_mfi_rebound").on(
            trigger="oversold_rebound",
            lower=20,
            stay_days=1,
            cooldown_days=5,
        )
    if kind == "amount_15":
        return AmountSurge(name=f"{name}_amt15").on(
            window=20,
            threshold=1.5,
            stay_days=1,
            cooldown_days=3,
        )
    if kind == "rs_5d_loser":
        return RelativeStrength(name=f"{name}_rs5").on(
            market="kospi",
            window=5,
            trigger="below",
            threshold=-0.08,
            stay_days=1,
            cooldown_days=5,
        )
    raise ValueError(f"알 수 없는 extra kind: {kind}")


def _build_candidate(spec: dict[str, object]) -> BasePattern:
    name = str(spec["name"])
    pattern = _build_disparity_base(name, float(spec["threshold"]))
    for extra_kind in tuple(spec["extras"]):
        pattern = pattern + _build_extra(name, str(extra_kind))
    return pattern.named(name)


def _summarize_short_vs_benchmark(stats, pattern_names: list[str]) -> pd.DataFrame:
    return summarize_periods_vs_benchmark(
        stats,
        "benchmark",
        pattern_names,
        SCREEN_HORIZONS,
        HORIZON_DAYS,
    )


def _pivot(summary: pd.DataFrame, pattern_names: list[str], value_col: str) -> pd.DataFrame:
    out = summary.pivot(index="pattern", columns="period", values=value_col)
    return out.reindex(pattern_names)


def _build_ranking(
    gap_pivot: pd.DataFrame,
    count_ratio_pivot: pd.DataFrame,
    rise_prob_gap_pivot: pd.DataFrame,
    trend_name: str,
) -> pd.DataFrame:
    rows: list[dict[str, object]] = []
    trend_gap = gap_pivot.loc[trend_name, SCREEN_HORIZONS]
    trend_score = float(trend_gap.mean())

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
                "count_ratio_1w": float(count_ratio_pivot.loc[pattern_name, "1W"]),
                "count_ratio_2w": float(count_ratio_pivot.loc[pattern_name, "2W"]),
                "count_ratio_3w": float(count_ratio_pivot.loc[pattern_name, "3W"]),
                "rise_prob_gap_1w": float(rise_prob_gap_pivot.loc[pattern_name, "1W"]),
                "rise_prob_gap_2w": float(rise_prob_gap_pivot.loc[pattern_name, "2W"]),
                "rise_prob_gap_3w": float(rise_prob_gap_pivot.loc[pattern_name, "3W"]),
                "wins_vs_trend": int((row > trend_gap).sum()),
            }
        )

    return pd.DataFrame(rows).sort_values(
        ["short_gap_score", "wins_vs_trend", "best_gap"],
        ascending=[False, False, False],
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


def _build_router_summary(
    top_candidates: pd.DataFrame,
    spec_map: dict[str, dict[str, object]],
) -> pd.DataFrame:
    quiet_contrarian = Regime().on(kind="quiet_tag", market="kospi") + Regime().on(
        kind="contrarian",
        market="kospi",
    )
    bt = build_default_backtest()

    trend_all = _build_trend_pattern("trend_amount1.5x")
    trend_no_quiet_contrarian = (
        _build_trend_pattern("trend_no_quiet_contrarian")
        .when(~quiet_contrarian)
        .named("trend_no_quiet_contrarian")
    )

    routers: list[BasePattern] = []
    router_names = ["trend_amount1.5x", "trend_no_quiet_contrarian"]
    for _, row in top_candidates.iterrows():
        candidate_name = str(row["pattern"])
        best_horizon = str(row["best_horizon"])
        branch_name = f"{candidate_name}_{best_horizon.lower()}"
        router_name = f"switch_{branch_name}"
        router = (
            _build_trend_pattern(f"trend_ex_{branch_name}")
            .when(~quiet_contrarian)
            .named(f"trend_ex_{branch_name}")
            | _build_candidate(spec_map[candidate_name])
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
    bt = build_default_backtest(
        benchmark=AllStockPattern(name="benchmark"),
    )

    trend = _build_trend_pattern("trend_amount1.5x")
    candidates = [_build_candidate(spec) for spec in CANDIDATE_SPECS]
    candidate_names = [pattern.name for pattern in candidates]
    compare_names = [trend.name, *candidate_names]

    stats = bt.analyze(trend, *candidates)
    summary = _summarize_short_vs_benchmark(stats, compare_names)
    gap_pivot = _pivot(summary, compare_names, "geom_ann_gap_after_cost")
    count_ratio_pivot = _pivot(summary, compare_names, "count_ratio")
    rise_prob_gap_pivot = _pivot(summary, compare_names, "rise_prob_gap")
    ranking = _build_ranking(gap_pivot, count_ratio_pivot, rise_prob_gap_pivot, trend.name)

    top_candidates = ranking.loc[ranking["pattern"] != trend.name, "pattern"].head(5).tolist()
    run_patterns = ["benchmark", trend.name, *top_candidates]
    run_summary = _collect_run_summary(bt, run_patterns)
    spec_map = {str(spec["name"]): spec for spec in CANDIDATE_SPECS}
    router_targets = ranking[ranking["pattern"] != trend.name].head(3)[["pattern", "best_horizon"]].copy()
    router_summary = _build_router_summary(router_targets, spec_map)

    print("=== Short-Horizon Gap After Cost vs benchmark ===")
    print(gap_pivot.round(4).to_string())
    print()

    print("=== Short-Horizon Count Ratio vs benchmark ===")
    print(count_ratio_pivot.round(4).to_string())
    print()

    print("=== Candidate Ranking ===")
    print(ranking.round(4).to_string(index=False))
    print()

    print("=== Run Summary | benchmark + trend + top5 candidates ===")
    print(run_summary.round(4).to_string(index=False))
    print()

    print("=== Router Summary | quiet_contrarian branch with best short horizon ===")
    print(router_summary.round(4).to_string(index=False))


if __name__ == "__main__":
    main()
