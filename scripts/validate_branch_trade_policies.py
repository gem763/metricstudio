"""
switch_without_panic에 branch별 horizon/stop 정책을 붙여 비교한다.
"""

from __future__ import annotations

from pathlib import Path
import sys

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.backtest import Backtest, Univ
from src.pattern import AmountSurge, Bollinger, High, MFI, RelativeStrength, Trending
from src.regime import Regime


def _build_trend(name: str, *, horizon: str | int | None = None):
    bb = Bollinger(name="볼린저돌파").on(
        trigger="breakout_up",
        breakout_cooldown_days=3,
        bandwidth_max=0.05,
    )
    high52w = High(name="52주 고가").on(window=240, threshold=0.90, stay_days=1)
    uptrend = Trending(name="이평상향").on(trigger="ma_trend_up", window=200)
    mfi_high = MFI(name="MFI상승").on(trigger="above", threshold=50)
    amount15 = AmountSurge(name="amount1.5x").on(window=20, threshold=1.5)
    pattern = (bb + high52w + uptrend + mfi_high + amount15).named(name)
    if horizon is not None:
        pattern.trade(target_horizon=horizon)
    return pattern


def _build_contra(
    name: str,
    *,
    horizon: str | int | None = None,
    stop_loss_pct: float | None = None,
    take_profit_pct: float | None = None,
    cohort_scale: float | None = None,
):
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
    ).named(name)
    pattern.trade(
        target_horizon=horizon,
        stop_loss_pct=stop_loss_pct,
        take_profit_pct=take_profit_pct,
        cohort_scale=cohort_scale,
    )
    return pattern


def main() -> None:
    contrarian = Regime().on(kind="contrarian", market="kospi")

    bt = Backtest(
        start="2000-01-01",
        end="2025-12-31",
        by="day",
        univ=Univ(market=["KOSPI", "KOSDAQ"]),
        db=0,
    )

    trend_all = _build_trend("trend_amount1.5x")
    switch_without_panic = (
        _build_trend("trend_not_contrarian")
        .when(~contrarian)
        .named("trend_not_contrarian")
        | _build_contra("contra_in_contrarian")
        .when(contrarian)
        .named("contra_in_contrarian")
    ).named("switch_without_panic")
    switch_contra_3w = (
        _build_trend("trend_not_contrarian_3w")
        .when(~contrarian)
        .named("trend_not_contrarian_3w")
        | _build_contra("contra_in_contrarian_3w", horizon="3W")
        .when(contrarian)
        .named("contra_in_contrarian_3w")
    ).named("switch_contra_3w")
    switch_contra_3w_scale50 = (
        _build_trend("trend_not_contrarian_3w_scale50")
        .when(~contrarian)
        .named("trend_not_contrarian_3w_scale50")
        | _build_contra(
            "contra_in_contrarian_3w_scale50",
            horizon="3W",
            cohort_scale=0.5,
        )
        .when(contrarian)
        .named("contra_in_contrarian_3w_scale50")
    ).named("switch_contra_3w_scale50")
    switch_contra_3w_scale35 = (
        _build_trend("trend_not_contrarian_3w_scale35")
        .when(~contrarian)
        .named("trend_not_contrarian_3w_scale35")
        | _build_contra(
            "contra_in_contrarian_3w_scale35",
            horizon="3W",
            cohort_scale=0.35,
        )
        .when(contrarian)
        .named("contra_in_contrarian_3w_scale35")
    ).named("switch_contra_3w_scale35")

    bt.analyze(
        trend_all,
        switch_without_panic,
        switch_contra_3w,
        switch_contra_3w_scale50,
        switch_contra_3w_scale35,
        include_base=False,
    )

    rows: list[dict[str, float | str]] = []
    for pattern_name in [
        "trend_amount1.5x",
        "switch_without_panic",
        "switch_contra_3w",
        "switch_contra_3w_scale50",
        "switch_contra_3w_scale35",
    ]:
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
            }
        )

    summary = pd.DataFrame(rows).set_index("pattern")
    print(summary.round(4).to_string())


if __name__ == "__main__":
    main()
