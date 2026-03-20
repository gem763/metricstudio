"""
panic_rebound_risk 레짐에서 반등 후보 패턴들을 비교한다.
"""

from __future__ import annotations

from pathlib import Path
import sys

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from research.notebook_experiment_utils import build_default_backtest, summarize_vs_benchmark
from metricstudio.patterns import AllStockPattern, Disparity, MFI, PanicRebound
from metricstudio.regime import Regime


def _build_disparity_pattern(regime: Regime):
    pattern = Disparity(name="disparity0.9", window=20).on(
        threshold=0.9,
        stay_days=1,
        cooldown_days=5,
    ).when(regime)
    pattern.name = "disparity0.9"
    return pattern


def _build_mfi_pattern(regime: Regime):
    pattern = MFI(name="mfi_oversold").on(
        trigger="oversold_rebound",
        lower=20,
        stay_days=1,
        cooldown_days=5,
    ).when(regime)
    pattern.name = "mfi_oversold"
    return pattern


def _build_panic_rebound_pattern(
    regime: Regime,
    name: str,
    volume_spike: bool,
):
    pattern = PanicRebound(name=name).on(
        drawdown_window=20,
        drawdown_min=-0.18,
        rebound_days=3,
        volume_spike=volume_spike,
        volume_window=20,
        volume_threshold=1.5,
    ).when(regime)
    pattern.name = name
    return pattern


def build_validation_table() -> pd.DataFrame:
    bt = build_default_backtest()
    regime = Regime().on(kind="panic_rebound_risk", market="kospi")
    benchmark = AllStockPattern(name="benchmark").when(regime)
    disparity = _build_disparity_pattern(regime)
    mfi = _build_mfi_pattern(regime)
    panic_plain = _build_panic_rebound_pattern(regime, "panic_rebound", volume_spike=False)
    panic_volume = _build_panic_rebound_pattern(regime, "panic_rebound+volume1.5x", volume_spike=True)
    stats = bt.analyze(benchmark, disparity, mfi, panic_plain, panic_volume)
    return summarize_vs_benchmark(
        stats,
        "benchmark",
        ["disparity0.9", "mfi_oversold", "panic_rebound", "panic_rebound+volume1.5x"],
    )


def main() -> None:
    table = build_validation_table()
    print(table.to_string(index=False))


if __name__ == "__main__":
    main()
