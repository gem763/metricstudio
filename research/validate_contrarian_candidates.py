"""
contrarian 레짐 inside에서 short-horizon 후보 패턴을 비교한다.

핵심 비교:
- benchmark: contrarian 레짐 안의 전체 종목
- mfi_oversold
- mfi_failure
- loser5_amt
- loser5_mfi35
- loser7_mfi35
- disp20_mfi35
"""

from __future__ import annotations

from pathlib import Path
import sys

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from metricstudio.backtest import Backtest
from metricstudio.univ import Univ
from research.notebook_experiment_utils import summarize_vs_benchmark, overall_pivot, window_pivot
from metricstudio.patterns import AllStockPattern, AmountSurge, BasePattern, Disparity, MFI, RelativeStrength
from metricstudio.regime import Regime


def _build_candidates():
    return [
        MFI(name="mfi_oversold").on(
            trigger="oversold_rebound",
            lower=20,
            stay_days=1,
            cooldown_days=5,
        ),
        MFI(name="mfi_failure").on(
            trigger="bullish_failure_swing",
            lower=20,
            stay_days=1,
            cooldown_days=5,
        ),
        (
            RelativeStrength(name="loser5_amt_rs").on(
                market="kospi",
                window=5,
                trigger="below",
                threshold=-0.08,
                cooldown_days=5,
            )
            + AmountSurge(name="amt15").on(window=20, threshold=1.5, cooldown_days=3)
        ).named("loser5_amt"),
        (
            RelativeStrength(name="loser5_mfi35_rs").on(
                market="kospi",
                window=5,
                trigger="below",
                threshold=-0.08,
                cooldown_days=5,
            )
            + MFI(name="mfi35").on(trigger="below", threshold=35, stay_days=1, cooldown_days=0)
        ).named("loser5_mfi35"),
        (
            RelativeStrength(name="loser7_mfi35_rs").on(
                market="kospi",
                window=7,
                trigger="below",
                threshold=-0.10,
                cooldown_days=5,
            )
            + MFI(name="mfi35b").on(trigger="below", threshold=35, stay_days=1, cooldown_days=0)
        ).named("loser7_mfi35"),
        (
            Disparity(window=20, name="disp20").on(threshold=0.92, cooldown_days=5)
            + MFI(name="mfi35c").on(trigger="below", threshold=35, stay_days=1, cooldown_days=0)
        ).named("disp20_mfi35"),
    ]


def build_report() -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    contrarian = Regime().on(kind="contrarian", market="kospi")
    bt = Backtest(
        start="2000-01-01",
        end="2025-12-31",
        by="day",
        benchmark=AllStockPattern(name="benchmark"),
        regime=contrarian,
        univ=Univ(market=["KOSPI", "KOSDAQ"]),
        db=0,
    )

    candidates = _build_candidates()
    stats = bt.analyze(*candidates)
    pattern_names = [pattern.name for pattern in candidates]
    summary = summarize_vs_benchmark(stats, "benchmark", pattern_names)

    rows: list[dict[str, float | str]] = []
    for pattern_name in ["benchmark"] + pattern_names:
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
                "max_drawdown": float(meta["max_drawdown"]),
                "active_day_ratio": float(meta["active_day_ratio"]),
                "cohort_win_rate": float(meta["cohort_win_rate"]),
                "cohort_payoff_ratio": float(meta["cohort_payoff_ratio"]),
            }
        )

    run_summary = pd.DataFrame(rows)
    overall_gap = overall_pivot(summary, pattern_names, "geom_ann_gap_after_cost")
    one_month_window_gap = window_pivot(summary, pattern_names, "geom_ann_gap_after_cost")[["1M"]]
    return overall_gap, one_month_window_gap, run_summary


def main() -> None:
    overall_gap, one_month_window_gap, run_summary = build_report()
    print("=== Overall Geometric Annualized Gap After Cost vs benchmark ===")
    print(overall_gap.round(4).to_string())
    print()
    print("=== 1M Gap By Window ===")
    print(one_month_window_gap.round(4).to_string())
    print()
    print("=== 1M Run Summary ===")
    print(run_summary.round(4).to_string(index=False))


if __name__ == "__main__":
    main()
