"""
trend_friendly 레짐에서 기본 breakout 패턴과 거래대금 필터를 구간별로 비교한다.
"""

from __future__ import annotations

from pathlib import Path
import sys

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from research.notebook_experiment_utils import build_default_backtest, summarize_vs_benchmark
from metricstudio.patterns import AllStockPattern, Bollinger, High, Trending, MFI, AmountSurge
from metricstudio.regime import Regime


def _build_base_pattern(regime: Regime):
    bb = Bollinger(name="볼린저돌파").on(
        trigger="breakout_up",
        breakout_cooldown_days=3,
        bandwidth_max=0.05,
    )
    high52w = High(name="52주 고가").on(window=240, threshold=0.90, stay_days=1)
    uptrend = Trending(name="이평상향").on(trigger="ma_trend_up", window=200)
    mfi_high = MFI(name="MFI상승").on(trigger="above", threshold=50)
    pattern = (bb + high52w + uptrend + mfi_high).when(regime)
    pattern.name = "base"
    return pattern


def _build_amount_pattern(regime: Regime):
    pattern = _build_base_pattern(regime) + AmountSurge(name="거래대금1.5x").on(
        window=20,
        threshold=1.5,
    )
    pattern.name = "base+amount1.5x"
    return pattern


def build_validation_table() -> pd.DataFrame:
    bt = build_default_backtest()
    regime = Regime().on(kind="trend_friendly", market="kospi")
    benchmark = AllStockPattern(name="benchmark").when(regime)
    base = _build_base_pattern(regime)
    amount = _build_amount_pattern(regime)
    stats = bt.analyze(benchmark, base, amount)
    return summarize_vs_benchmark(
        stats,
        "benchmark",
        ["base", "base+amount1.5x"],
    )


def main() -> None:
    table = build_validation_table()
    print(table.to_string(index=False))


if __name__ == "__main__":
    main()
