"""
trend_amount1.5x를 기본 패턴으로 두고,
서브레짐별로 trend 강도를 완화/강화했을 때의 효과를 검증한다.

핵심 질문:
1. quiet / broad / narrow / panic 내부에서 어떤 trend 변형이 가장 잘 맞는가?
2. 그 변형을 전체 기간 라우터로 붙였을 때 baseline보다 실제로 개선되는가?
"""

from __future__ import annotations

from pathlib import Path
import sys

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from metricstudio.backtest import Backtest
from research.notebook_experiment_utils import annualize_geom as _annualize_geom, apply_cost as _apply_cost, build_default_backtest
from metricstudio.patterns import AllStockPattern, AmountSurge, BasePattern, Bollinger, High, MFI, RetestBreakout, Trending
from metricstudio.regime import Regime


HORIZONS = ["1M", "2M", "3M"]
HORIZON_DAYS = {"1M": 20, "2M": 40, "3M": 60}
REGIME_SPECS = [
    ("quiet", "quiet_squeeze_expansion"),
    ("broad", "broad_bull_breakout"),
    ("narrow", "narrow_leadership"),
    ("panic", "panic"),
]


def _build_trend_pattern(
    name: str,
    *,
    amount_threshold: float | None = 1.5,
    retest: bool = False,
    breakout_amount_threshold: float | None = None,
    regime: Regime | None = None,
    cohort_scale: float | None = None,
) -> BasePattern:
    if retest:
        entry = RetestBreakout(name=f"{name}_retest").on(
            breakout_window=20,
            retest_tolerance=0.03,
            max_retest_days=10,
            breakout_amount_threshold=breakout_amount_threshold,
            breakout_amount_window=20,
            rebound_confirm="close_up",
        )
    else:
        entry = Bollinger(name=f"{name}_bb").on(
            trigger="breakout_up",
            breakout_cooldown_days=3,
            bandwidth_max=0.05,
        )

    high52w = High(name=f"{name}_52w").on(window=240, threshold=0.90, stay_days=1)
    uptrend = Trending(name=f"{name}_ma200").on(trigger="ma_trend_up", window=200)
    mfi_high = MFI(name=f"{name}_mfi50").on(trigger="above", threshold=50)

    pattern = entry + high52w + uptrend + mfi_high
    if amount_threshold is not None and not retest:
        pattern = pattern + AmountSurge(name=f"{name}_amt").on(window=20, threshold=amount_threshold)
    if regime is not None:
        pattern = pattern.when(regime)

    pattern = pattern.named(name)
    if cohort_scale is not None:
        pattern.trade(cohort_scale=cohort_scale)
    return pattern


def _variant_patterns(regime: Regime) -> list[BasePattern]:
    return [
        _build_trend_pattern("trend_base", amount_threshold=None, regime=regime),
        _build_trend_pattern("trend_amount1.3x", amount_threshold=1.3, regime=regime),
        _build_trend_pattern("trend_amount1.5x", amount_threshold=1.5, regime=regime),
        _build_trend_pattern("trend_amount2.0x", amount_threshold=2.0, regime=regime),
        _build_trend_pattern("trend_retest", retest=True, regime=regime),
        _build_trend_pattern(
            "trend_retest_amt1.5x",
            retest=True,
            breakout_amount_threshold=1.5,
            regime=regime,
        ),
    ]


def build_regime_gap_table(bt: Backtest) -> pd.DataFrame:
    rows: list[pd.DataFrame] = []
    for regime_label, regime_kind in REGIME_SPECS:
        regime = Regime().on(kind=regime_kind, market="kospi")
        stats = bt.analyze(AllStockPattern(name="benchmark").when(regime), *_variant_patterns(regime))
        frame = stats.to_frame(start="2000-01-01", end="2025-12-31").reset_index()
        frame = frame[(frame["scope"] != "empty") & (frame["period"].isin(HORIZONS))].copy()

        benchmark = frame[frame["pattern"] == "benchmark"][["period", "geom_mean", "count"]].rename(
            columns={
                "geom_mean": "benchmark_geom_mean",
                "count": "benchmark_count",
            }
        )
        merged = frame.merge(benchmark, on="period", how="left")
        merged = merged[merged["pattern"] != "benchmark"].copy()
        merged["geom_after_cost"] = merged["geom_mean"].map(_apply_cost)
        merged["benchmark_after_cost"] = merged["benchmark_geom_mean"].map(_apply_cost)
        merged["geom_ann_gap_after_cost"] = [
            _annualize_geom(value, HORIZON_DAYS[period])
            - _annualize_geom(benchmark_value, HORIZON_DAYS[period])
            for value, benchmark_value, period in zip(
                merged["geom_after_cost"],
                merged["benchmark_after_cost"],
                merged["period"],
            )
        ]
        merged["count_ratio"] = merged["count"] / merged["benchmark_count"]

        pivot = merged.pivot(index="pattern", columns="period", values="geom_ann_gap_after_cost")
        pivot = pivot.reindex(
            [
                "trend_base",
                "trend_amount1.3x",
                "trend_amount1.5x",
                "trend_amount2.0x",
                "trend_retest",
                "trend_retest_amt1.5x",
            ]
        )
        count_ratio = merged.pivot(index="pattern", columns="period", values="count_ratio").mean(axis=1)
        pivot["score"] = pivot[HORIZONS].mean(axis=1)
        pivot["count_ratio_mean"] = count_ratio
        pivot.insert(0, "regime", regime_label)
        rows.append(pivot.reset_index())
    return pd.concat(rows, ignore_index=True)


def _build_router_patterns() -> list[BasePattern]:
    broad = Regime().on(kind="broad_bull_breakout", market="kospi")
    quiet = Regime().on(kind="quiet_squeeze_expansion", market="kospi")
    narrow = Regime().on(kind="narrow_leadership", market="kospi")
    panic = Regime().on(kind="panic", market="kospi")

    return [
        _build_trend_pattern("trend_amount1.5x", amount_threshold=1.5),
        (
            _build_trend_pattern("broad_base_else", amount_threshold=1.5, regime=~broad)
            | _build_trend_pattern("broad_base", amount_threshold=None, regime=broad)
        ).named("router_broad_base"),
        (
            _build_trend_pattern("broad_13_else", amount_threshold=1.5, regime=~broad)
            | _build_trend_pattern("broad_13", amount_threshold=1.3, regime=broad)
        ).named("router_broad_13"),
        (
            _build_trend_pattern("quiet_retest_else", amount_threshold=1.5, regime=~quiet)
            | _build_trend_pattern("quiet_retest", retest=True, regime=quiet)
        ).named("router_quiet_retest"),
        (
            _build_trend_pattern("quiet_retest_amt_else", amount_threshold=1.5, regime=~quiet)
            | _build_trend_pattern(
                "quiet_retest_amt",
                retest=True,
                breakout_amount_threshold=1.5,
                regime=quiet,
            )
        ).named("router_quiet_retest_amt"),
        (
            _build_trend_pattern("narrow_20_else", amount_threshold=1.5, regime=~narrow)
            | _build_trend_pattern("narrow_20", amount_threshold=2.0, regime=narrow)
        ).named("router_narrow_20"),
        (
            _build_trend_pattern("panic_20_else", amount_threshold=1.5, regime=~panic)
            | _build_trend_pattern("panic_20", amount_threshold=2.0, regime=panic)
        ).named("router_panic_20"),
        (
            _build_trend_pattern("panic_scale50_else", amount_threshold=1.5, regime=~panic)
            | _build_trend_pattern(
                "panic_scale50",
                amount_threshold=1.5,
                regime=panic,
                cohort_scale=0.5,
            )
        ).named("router_panic_scale50"),
        (
            _build_trend_pattern("bq_else", amount_threshold=1.5, regime=(~broad) - quiet)
            | _build_trend_pattern("bq_broad", amount_threshold=None, regime=broad)
            | _build_trend_pattern("bq_quiet", retest=True, regime=quiet)
        ).named("router_broad_base_quiet_retest"),
        (
            _build_trend_pattern("bn_else", amount_threshold=1.5, regime=(~broad) - narrow)
            | _build_trend_pattern("bn_broad", amount_threshold=None, regime=broad)
            | _build_trend_pattern("bn_narrow", amount_threshold=2.0, regime=narrow)
        ).named("router_broad_base_narrow_20"),
        (
            _build_trend_pattern("bqn_else", amount_threshold=1.5, regime=((~broad) - quiet) - narrow)
            | _build_trend_pattern("bqn_broad", amount_threshold=None, regime=broad)
            | _build_trend_pattern("bqn_quiet", retest=True, regime=quiet)
            | _build_trend_pattern("bqn_narrow", amount_threshold=2.0, regime=narrow)
        ).named("router_broad_base_quiet_retest_narrow_20"),
        (
            _build_trend_pattern(
                "bqnp_else",
                amount_threshold=1.5,
                regime=(((~broad) - quiet) - narrow) - panic,
            )
            | _build_trend_pattern("bqnp_broad", amount_threshold=None, regime=broad)
            | _build_trend_pattern("bqnp_quiet", retest=True, regime=quiet)
            | _build_trend_pattern("bqnp_narrow", amount_threshold=2.0, regime=narrow)
            | _build_trend_pattern(
                "bqnp_panic",
                amount_threshold=1.5,
                regime=panic,
                cohort_scale=0.5,
            )
        ).named("router_broad_base_quiet_retest_narrow_20_panic_scale50"),
        (
            _build_trend_pattern("b13p20_else", amount_threshold=1.5, regime=(~broad) - panic)
            | _build_trend_pattern("b13p20_broad", amount_threshold=1.3, regime=broad)
            | _build_trend_pattern("b13p20_panic", amount_threshold=2.0, regime=panic)
        ).named("router_broad_13_panic_20"),
        (
            _build_trend_pattern(
                "b13n20p20_else",
                amount_threshold=1.5,
                regime=((~broad) - narrow) - panic,
            )
            | _build_trend_pattern("b13n20p20_broad", amount_threshold=1.3, regime=broad)
            | _build_trend_pattern("b13n20p20_narrow", amount_threshold=2.0, regime=narrow)
            | _build_trend_pattern("b13n20p20_panic", amount_threshold=2.0, regime=panic)
        ).named("router_broad_13_narrow_20_panic_20"),
        (
            _build_trend_pattern("n20p20_else", amount_threshold=1.5, regime=(~narrow) - panic)
            | _build_trend_pattern("n20p20_narrow", amount_threshold=2.0, regime=narrow)
            | _build_trend_pattern("n20p20_panic", amount_threshold=2.0, regime=panic)
        ).named("router_narrow_20_panic_20"),
    ]


def build_router_summary(bt: Backtest) -> pd.DataFrame:
    patterns = _build_router_patterns()
    bt.analyze(*patterns, include_base=False)

    rows: list[dict[str, float | str]] = []
    for pattern in patterns:
        sim = bt.run(
            pattern=pattern.name,
            target_horizon="1M",
            trade_price_mode="당일종가",
        )
        meta = sim.summary()
        rows.append(
            {
                "pattern": str(pattern.name),
                "final_wealth": float(1.0 + meta["total_return"]),
                "cagr": float(meta["cagr"]),
                "mdd": float(meta["max_drawdown"]),
                "active_day_ratio": float(meta["active_day_ratio"]),
                "cohort_win_rate": float(meta["cohort_win_rate"]),
            }
        )
    return pd.DataFrame(rows).sort_values(["cagr", "mdd"], ascending=[False, False], kind="stable")


def main() -> None:
    bt = build_default_backtest()

    regime_gap = build_regime_gap_table(bt)
    router_summary = build_router_summary(bt)

    print("=== Regime Fit | gap vs benchmark ===")
    for regime_label, _ in REGIME_SPECS:
        table = regime_gap[regime_gap["regime"] == regime_label].copy()
        table = table.drop(columns=["regime"]).sort_values("score", ascending=False, kind="stable")
        print(f"[{regime_label}]")
        print(table.round(4).to_string(index=False))
        print()

    print("=== Router Summary | 1M ===")
    print(router_summary.round(4).to_string(index=False))


if __name__ == "__main__":
    main()
