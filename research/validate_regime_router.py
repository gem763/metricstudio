"""
대표 trend 패턴과 contrarian branch를 regime router 형태로 비교한다.

핵심 비교:
- trend_amount1.5x
- trend_no_panic
- blend_oversold
- blend_failure
"""

from __future__ import annotations

from pathlib import Path
import sys

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from research.notebook_experiment_utils import build_default_backtest
from metricstudio.patterns import Bollinger, High, Trending, MFI, AmountSurge
from metricstudio.regime import Regime


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


def _build_contrarian_pattern(name: str, trigger: str, regime: Regime):
    pattern = MFI(name=name).on(
        trigger=trigger,
        lower=20,
        stay_days=1,
        cooldown_days=5,
    ).when(regime)
    return pattern.named(name)


def build_run_summary() -> pd.DataFrame:
    bt = build_default_backtest()
    panic = Regime().on(kind="panic", market="kospi")
    contrarian = Regime().on(kind="contrarian", market="kospi")

    trend = _build_trend_pattern("trend_amount1.5x")
    trend_no_panic = _build_trend_pattern("trend_no_panic", regime=~panic)
    contra_oversold = _build_contrarian_pattern(
        "contra_oversold",
        "oversold_rebound",
        contrarian - panic,
    )
    contra_failure = _build_contrarian_pattern(
        "contra_failure",
        "bullish_failure_swing",
        contrarian - panic,
    )
    blend_oversold = (trend | contra_oversold).named("blend_oversold")
    blend_failure = (trend | contra_failure).named("blend_failure")

    bt.analyze(
        trend,
        trend_no_panic,
        blend_oversold,
        blend_failure,
        include_base=False,
    )

    rows: list[dict[str, float | str]] = []
    for pattern_name in [
        "trend_amount1.5x",
        "trend_no_panic",
        "blend_oversold",
        "blend_failure",
    ]:
        sim = bt.run(
            pattern=pattern_name,
            target_horizon="1M",
            trade_price_mode="당일종가",
        )
        meta = sim.summary()
        frame = sim.to_frame()
        rows.append(
            {
                "pattern": pattern_name,
                "final_wealth": float(1.0 + meta["total_return"]),
                "cagr": float(meta["cagr"]),
                "max_drawdown": float(meta["max_drawdown"]),
                "mean_exposure": float(np.nanmean(frame["exposure"].to_numpy(dtype=float))),
                "active_day_ratio": float(meta["active_day_ratio"]),
                "cohort_win_rate": float(meta["cohort_win_rate"]),
                "cohort_payoff_ratio": float(meta["cohort_payoff_ratio"]),
                "total_fee_paid": float(meta["total_fee_paid"]),
            }
        )
    return pd.DataFrame(rows)


def main() -> None:
    table = build_run_summary()
    print(table.to_string(index=False))


if __name__ == "__main__":
    main()
