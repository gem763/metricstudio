"""
contrarian 패턴을 trend 기준과 함께 재검증한다.

비교 범위:
- contrarian 레짐 inside에서 trend vs contra vs contra+exit
- 완화된 regime switch router
"""

from __future__ import annotations

from pathlib import Path
import sys

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.backtest import Backtest, Univ
from src.pattern import Bollinger, High, MFI, AmountSurge, Pattern, RelativeStrength, Trending
from src.regime import Regime


def _build_trend_pattern(name: str, regime: Regime | None = None):
    bb = Bollinger(name="볼린저돌파").on(
        trigger="breakout_up",
        breakout_cooldown_days=3,
        bandwidth_max=0.05,
    )
    high52w = High(name="52주 고가").on(window=240, threshold=0.90, stay_days=1)
    uptrend = Trending(name="이평상향").on(trigger="ma_trend_up", window=200)
    mfi_high = MFI(name="MFI상승").on(trigger="above", threshold=50)
    amount15 = AmountSurge(name="거래대금1.5x").on(window=20, threshold=1.5)

    pattern = bb + high52w + uptrend + mfi_high + amount15
    if regime is not None:
        pattern = pattern.when(regime)
    return pattern.named(name)


def _build_contra_pattern(name: str, regime: Regime | None = None):
    pattern = (
        RelativeStrength(name="5D상대낙폭").on(
            market="kospi",
            window=5,
            trigger="below",
            threshold=-0.08,
            cooldown_days=5,
        )
        + MFI(name="MFI<35").on(
            trigger="below",
            threshold=35,
            stay_days=1,
            cooldown_days=0,
        )
    )
    if regime is not None:
        pattern = pattern.when(regime)
    return pattern.named(name)


def _build_contra_with_exit(name: str, loss_cut: str):
    stop = Bollinger(name=f"bollinger_{loss_cut}").on(loss_cut=loss_cut)
    return (_build_contra_pattern(name) + stop).named(name)


def build_contrarian_horizon_summary() -> pd.DataFrame:
    contrarian = Regime().on(kind="contrarian", market="kospi")
    bt = Backtest(
        start="2000-01-01",
        end="2025-12-31",
        by="day",
        benchmark=Pattern(name="benchmark"),
        regime=contrarian,
        univ=Univ(market=["KOSPI", "KOSDAQ"]),
        db=0,
    )

    patterns = [
        _build_trend_pattern("trend_amount1.5x"),
        _build_contra_pattern("loser5_mfi35"),
        _build_contra_with_exit("loser5_mfi35_midstop", "mid_stop"),
        _build_contra_with_exit("loser5_mfi35_trail", "trailing_stop"),
    ]
    bt.analyze(*patterns)

    rows: list[dict[str, float | str]] = []
    for horizon in ["1W", "2W", "3W", "1M", "2M", "3M"]:
        for pattern_name in [
            "benchmark",
            "trend_amount1.5x",
            "loser5_mfi35",
            "loser5_mfi35_midstop",
            "loser5_mfi35_trail",
        ]:
            sim = bt.run(
                pattern=pattern_name,
                target_horizon=horizon,
                trade_price_mode="당일종가",
            )
            meta = sim.summary()
            rows.append(
                {
                    "scope": "contrarian_inside",
                    "horizon": horizon,
                    "pattern": pattern_name,
                    "final_wealth": float(1.0 + meta["total_return"]),
                    "cagr": float(meta["cagr"]),
                    "max_drawdown": float(meta["max_drawdown"]),
                    "cohort_win_rate": float(meta["cohort_win_rate"]),
                    "cohort_payoff_ratio": float(meta["cohort_payoff_ratio"]),
                    "active_day_ratio": float(meta["active_day_ratio"]),
                }
            )
    return pd.DataFrame(rows)


def build_router_summary() -> pd.DataFrame:
    panic = Regime().on(kind="panic", market="kospi")
    trend = Regime().on(kind="trend", market="kospi")
    contrarian = Regime().on(kind="contrarian", market="kospi")

    bt = Backtest(
        start="2000-01-01",
        end="2025-12-31",
        by="day",
        univ=Univ(market=["KOSPI", "KOSDAQ"]),
        db=0,
    )

    trend_all = _build_trend_pattern("trend_amount1.5x")
    trend_no_panic = _build_trend_pattern("trend_no_panic", ~panic)
    trend_non_contra = _build_trend_pattern("trend_non_contra", (~panic) - contrarian)
    switch_without_panic = (
        _build_trend_pattern("trend_not_contrarian", ~contrarian)
        | _build_contra_pattern("contra_in_contra0", contrarian)
    ).named("switch_without_panic")
    exclusive_router = (
        _build_trend_pattern("trend_in_trend", trend)
        | _build_contra_pattern("contra_in_contra", contrarian)
    ).named("exclusive_router")
    switch_on_contrarian = (
        _build_trend_pattern("trend_non_contra", (~panic) - contrarian)
        | _build_contra_pattern("contra_in_contra2", contrarian)
    ).named("switch_on_contrarian")
    switch_on_trend = (
        _build_trend_pattern("trend_in_trend2", trend)
        | _build_contra_pattern("contra_non_trend", (~panic) - trend)
    ).named("switch_on_trend")

    bt.analyze(
        trend_all,
        trend_no_panic,
        trend_non_contra,
        switch_without_panic,
        exclusive_router,
        switch_on_contrarian,
        switch_on_trend,
        include_base=False,
    )

    rows: list[dict[str, float | str]] = []
    for horizon in ["1M", "2M", "3M"]:
        for pattern_name in [
            "trend_amount1.5x",
            "trend_no_panic",
            "trend_non_contra",
            "switch_without_panic",
            "exclusive_router",
            "switch_on_contrarian",
            "switch_on_trend",
        ]:
            sim = bt.run(
                pattern=pattern_name,
                target_horizon=horizon,
                trade_price_mode="당일종가",
            )
            meta = sim.summary()
            rows.append(
                {
                    "scope": "router",
                    "horizon": horizon,
                    "pattern": pattern_name,
                    "final_wealth": float(1.0 + meta["total_return"]),
                    "cagr": float(meta["cagr"]),
                    "max_drawdown": float(meta["max_drawdown"]),
                    "cohort_win_rate": float(meta["cohort_win_rate"]),
                    "active_day_ratio": float(meta["active_day_ratio"]),
                }
            )
    return pd.DataFrame(rows)


def main() -> None:
    horizon_summary = build_contrarian_horizon_summary()
    router_summary = build_router_summary()

    print("=== Contrarian Inside Horizon Summary ===")
    print(horizon_summary.round(4).to_string(index=False))
    print()
    print("=== Router Summary ===")
    print(router_summary.round(4).to_string(index=False))


if __name__ == "__main__":
    main()
