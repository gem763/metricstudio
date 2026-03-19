"""
이격도 기반 contrarian 후보를 단계적으로 검증한다.

1. 레짐 없이 disparity 자체의 정보 우위를 먼저 확인한다.
2. no-regime 상위 후보만 골라 여러 regime 안에서 다시 비교한다.
3. 각 regime에서 benchmark 대비 gap, trend_amount1.5x 대비 CAGR/MDD 우위를 같이 본다.
"""

from __future__ import annotations

from pathlib import Path
import sys
from typing import Callable

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from metricstudio.backtest import Backtest
from metricstudio.univ import Univ
from research.notebook_experiment_utils import summarize_vs_benchmark, overall_pivot
from metricstudio.patterns import AllStockPattern, AmountSurge, BasePattern, Bollinger, Disparity, High, MFI, Trending
from metricstudio.regime import Regime


RUN_HORIZON = "1M"
SHORT_PERIODS = ["1M", "2M", "3M"]

CANDIDATE_SPECS = [
    {"name": "disp20_90_s1", "threshold": 0.90, "stay_days": 1, "ma60_state": None},
    {"name": "disp20_90_s3", "threshold": 0.90, "stay_days": 3, "ma60_state": None},
    {"name": "disp20_90_s5", "threshold": 0.90, "stay_days": 5, "ma60_state": None},
    {"name": "disp20_90_s8", "threshold": 0.90, "stay_days": 8, "ma60_state": None},
    {"name": "disp20_88_s5", "threshold": 0.88, "stay_days": 5, "ma60_state": None},
    {"name": "disp20_85_s5", "threshold": 0.85, "stay_days": 5, "ma60_state": None},
    {"name": "disp20_90_s5_ma60down", "threshold": 0.90, "stay_days": 5, "ma60_state": "down"},
    {"name": "disp20_90_s5_ma60up", "threshold": 0.90, "stay_days": 5, "ma60_state": "up"},
    {"name": "disp20_88_s5_ma60down", "threshold": 0.88, "stay_days": 5, "ma60_state": "down"},
]


def _build_trend_pattern(name: str) -> BasePattern:
    bb = Bollinger(name="볼린저돌파").on(
        trigger="breakout_up",
        breakout_cooldown_days=3,
        bandwidth_max=0.05,
    )
    high52w = High(name="52주 고가").on(window=240, threshold=0.90, stay_days=1)
    uptrend = Trending(name="이평상향").on(trigger="ma_trend_up", window=200)
    mfi_high = MFI(name="MFI상승").on(trigger="above", threshold=50)
    amount15 = AmountSurge(name="거래대금1.5x").on(window=20, threshold=1.5)
    return (bb + high52w + uptrend + mfi_high + amount15).named(name)


def _build_disparity_candidate(spec: dict[str, object]) -> BasePattern:
    pattern: BasePattern = Disparity(window=20, name=spec["name"]).on(
        threshold=float(spec["threshold"]),
        stay_days=int(spec["stay_days"]),
        cooldown_days=5,
    )

    ma60_state = spec["ma60_state"]
    if ma60_state is not None:
        if ma60_state not in {"down", "up"}:
            raise ValueError("ma60_state는 {'down', 'up', None} 중 하나여야 합니다.")
        trigger = "ma_trend_down" if ma60_state == "down" else "ma_trend_up"
        market_trend = Trending(name=f"{spec['name']}_kospi60_{ma60_state}").market("kospi").on(
            window=60,
            trigger=trigger,
            stay_days=1,
            cooldown_days=0,
        )
        pattern = pattern + market_trend

    return pattern.named(str(spec["name"]))


def _build_regime_map() -> dict[str, Callable[[], Regime]]:
    return {
        "panic": lambda: Regime().on(kind="panic", market="kospi"),
        "contrarian": lambda: Regime().on(kind="contrarian", market="kospi"),
        "neutral": lambda: Regime().on(kind="neutral", market="kospi"),
        "contrarian_or_neutral": (
            lambda: Regime().on(kind="contrarian", market="kospi")
            | Regime().on(kind="neutral", market="kospi")
        ),
        "quiet_contrarian": (
            lambda: Regime().on(kind="quiet_tag", market="kospi")
            + Regime().on(kind="contrarian", market="kospi")
        ),
        "quiet_nontrend": (
            lambda: Regime().on(kind="quiet_tag", market="kospi")
            + (
                Regime().on(kind="contrarian", market="kospi")
                | Regime().on(kind="neutral", market="kospi")
            )
        ),
    }


def _short_gap_score(overall_gap: pd.DataFrame) -> pd.Series:
    usable_cols = [period for period in SHORT_PERIODS if period in overall_gap.columns]
    score = overall_gap[usable_cols].mean(axis=1, skipna=True)
    return score.rename("short_gap_score")


def _collect_run_summary(bt: Backtest, pattern_names: list[str]) -> pd.DataFrame:
    rows: list[dict[str, float | str]] = []
    for pattern_name in pattern_names:
        sim = bt.run(
            pattern=pattern_name,
            target_horizon=RUN_HORIZON,
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
    return pd.DataFrame(rows).set_index("pattern")


def build_no_regime_report() -> dict[str, object]:
    bt = Backtest(
        start="2000-01-01",
        end="2025-12-31",
        by="day",
        benchmark=AllStockPattern(name="benchmark"),
        univ=Univ(market=["KOSPI", "KOSDAQ"]),
        db=0,
    )

    trend = _build_trend_pattern("trend_amount1.5x")
    candidates = [_build_disparity_candidate(spec) for spec in CANDIDATE_SPECS]
    candidate_names = [pattern.name for pattern in candidates]
    compare_names = [trend.name, *candidate_names]

    stats = bt.analyze(trend, *candidates)
    summary = summarize_vs_benchmark(stats, "benchmark", compare_names)
    overall_gap = overall_pivot(summary, compare_names, "geom_ann_gap_after_cost")
    short_score = _short_gap_score(overall_gap).sort_values(ascending=False)
    run_summary = _collect_run_summary(bt, ["benchmark", *compare_names])

    return {
        "summary": summary,
        "overall_gap": overall_gap,
        "short_score": short_score,
        "run_summary": run_summary,
    }


def build_regime_report(top_candidate_names: list[str]) -> pd.DataFrame:
    spec_map = {str(spec["name"]): spec for spec in CANDIDATE_SPECS}
    rows: list[dict[str, float | str | bool]] = []

    for regime_name, regime_builder in _build_regime_map().items():
        regime = regime_builder()
        bt = Backtest(
            start="2000-01-01",
            end="2025-12-31",
            by="day",
            benchmark=AllStockPattern(name="benchmark"),
            regime=regime,
            univ=Univ(market=["KOSPI", "KOSDAQ"]),
            db=0,
        )

        trend = _build_trend_pattern("trend_amount1.5x")
        candidates = [_build_disparity_candidate(spec_map[name]) for name in top_candidate_names]
        candidate_names = [pattern.name for pattern in candidates]
        compare_names = [trend.name, *candidate_names]

        stats = bt.analyze(trend, *candidates)
        summary = summarize_vs_benchmark(stats, "benchmark", compare_names)
        overall_gap = overall_pivot(summary, compare_names, "geom_ann_gap_after_cost")
        short_score = _short_gap_score(overall_gap)
        run_summary = _collect_run_summary(bt, ["benchmark", *compare_names])

        trend_short_score = float(short_score.loc[trend.name])
        trend_cagr = float(run_summary.loc[trend.name, "cagr"])
        trend_mdd = float(run_summary.loc[trend.name, "mdd"])
        benchmark_active_day_ratio = float(run_summary.loc["benchmark", "active_day_ratio"])

        for candidate_name in candidate_names:
            candidate_cagr = float(run_summary.loc[candidate_name, "cagr"])
            candidate_mdd = float(run_summary.loc[candidate_name, "mdd"])
            mdd_advantage_vs_trend = abs(trend_mdd) - abs(candidate_mdd)
            rows.append(
                {
                    "regime": regime_name,
                    "benchmark_active_day_ratio": benchmark_active_day_ratio,
                    "pattern": candidate_name,
                    "gap_1m": float(overall_gap.loc[candidate_name, "1M"]),
                    "gap_2m": float(overall_gap.loc[candidate_name, "2M"]),
                    "gap_3m": float(overall_gap.loc[candidate_name, "3M"]),
                    "short_gap_score": float(short_score.loc[candidate_name]),
                    "short_gap_vs_trend": float(short_score.loc[candidate_name] - trend_short_score),
                    "cagr": candidate_cagr,
                    "mdd": candidate_mdd,
                    "cagr_vs_trend": float(candidate_cagr - trend_cagr),
                    "mdd_advantage_vs_trend": float(mdd_advantage_vs_trend),
                    "clear_win_vs_trend": bool(
                        (short_score.loc[candidate_name] > trend_short_score)
                        and (candidate_cagr > trend_cagr)
                        and (mdd_advantage_vs_trend >= 0.0)
                    ),
                }
            )

    out = pd.DataFrame(rows)
    return out.sort_values(
        ["clear_win_vs_trend", "short_gap_score", "cagr_vs_trend", "mdd_advantage_vs_trend"],
        ascending=[False, False, False, False],
        kind="stable",
    ).reset_index(drop=True)


def main() -> None:
    no_regime = build_no_regime_report()

    print("=== Phase 1 | No Regime | Overall Geometric Annualized Gap After Cost vs benchmark ===")
    print(no_regime["overall_gap"].round(4).to_string())
    print()

    print("=== Phase 1 | No Regime | Short-Horizon Score (1M~3M 평균 gap) ===")
    print(no_regime["short_score"].round(4).to_string())
    print()

    print(f"=== Phase 1 | No Regime | {RUN_HORIZON} Run Summary ===")
    print(no_regime["run_summary"].round(4).to_string())
    print()

    top_candidate_names = [
        name for name in no_regime["short_score"].index.tolist() if name != "trend_amount1.5x"
    ][:3]
    print("=== Phase 2 | Regime Sweep Target Candidates ===")
    print(pd.Series(top_candidate_names, name="top_candidates").to_string(index=False))
    print()

    regime_report = build_regime_report(top_candidate_names)
    print("=== Phase 2 | Regime Sweep | Candidate vs benchmark/trend_amount1.5x ===")
    print(regime_report.round(4).to_string(index=False))
    print()

    winners = regime_report[regime_report["clear_win_vs_trend"]].copy()
    print("=== Phase 2 | Clear Wins vs trend_amount1.5x ===")
    if winners.empty:
        print("없음")
    else:
        print(winners.round(4).to_string(index=False))


if __name__ == "__main__":
    main()
