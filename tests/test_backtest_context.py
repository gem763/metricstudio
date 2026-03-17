from __future__ import annotations

import unittest
import warnings
from types import SimpleNamespace

import matplotlib
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

from src.backtest import Backtest
from src.pattern import MFI, Pattern
from src.regime import Regime
from src.simulate import Simulator
from src.stats import Stats, StatsCollection

matplotlib.use("Agg")


class _FakeSimulator:
    def __init__(self, pattern: str, cagr: float, regime_mask: np.ndarray | None = None):
        self.pattern = pattern
        self._frame = pd.DataFrame(
            {
                "wealth": [1.0, 1.05, 1.10],
                "exposure": [0.5, 0.6, 0.7],
            },
            index=pd.date_range("2025-01-01", periods=3, freq="B"),
        )
        if regime_mask is not None:
            self._frame.attrs["regime_active_mask"] = np.asarray(regime_mask, dtype=np.bool_).copy()
        self._summary = {
            "pattern": pattern,
            "total_return": 0.10,
            "cagr": cagr,
            "max_drawdown": -0.08,
            "cohort_win_rate": 0.6,
            "cohort_payoff_ratio": 1.4,
            "active_day_ratio": 0.7,
            "total_fee_paid": 0.01,
        }

    def to_frame(self, copy: bool = True) -> pd.DataFrame:
        return self._frame.copy() if copy else self._frame

    def summary(self) -> dict[str, float | str]:
        return dict(self._summary)


class BacktestContextTests(unittest.TestCase):
    def tearDown(self):
        plt.close("all")

    def test_pattern_named_supports_chaining_call(self):
        pattern = MFI(name="before").on(trigger="above", threshold=50)

        renamed = pattern.named("after")

        self.assertIs(renamed, pattern)
        self.assertEqual(pattern.name, "after")

    def test_apply_default_regime_wraps_pattern_without_attached_regime(self):
        bt = Backtest.__new__(Backtest)
        bt.regime = Regime().on(kind="trend_friendly", market="kospi")

        pattern = MFI(name="mfi").on(trigger="above", threshold=50)
        resolved = bt._apply_default_regime(pattern)

        self.assertIsNot(resolved, pattern)
        attached = list(bt._iter_attached_regimes(resolved))
        self.assertEqual(len(attached), 1)
        self.assertIs(attached[0], bt.regime)
        self.assertEqual(resolved.name, pattern.name)

    def test_apply_default_regime_keeps_explicit_regime_pattern(self):
        bt = Backtest.__new__(Backtest)
        bt.regime = Regime().on(kind="trend_friendly", market="kospi")

        explicit_regime = Regime().on(kind="panic_rebound_risk", market="kospi")
        pattern = Pattern(name="explicit").when(explicit_regime)
        resolved = bt._apply_default_regime(pattern)

        self.assertIs(resolved, pattern)

    def test_store_runtime_cache_expands_partial_mask_to_full_dates(self):
        bt = Backtest.__new__(Backtest)
        bt.dates = np.arange(6)
        bt.start_idx = 1
        bt.end_idx = 4
        bt.horizon_offsets = np.asarray([5, 10], dtype=np.int64)
        bt._pattern_mask_cache = {}
        bt._pattern_exit_mask_cache = {}
        bt._pattern_exit_index_cache = {}

        partial_mask = np.array(
            [
                [True, False],
                [False, True],
                [True, True],
            ],
            dtype=np.bool_,
        )

        bt._store_runtime_cache("demo", mask_matrix=partial_mask)

        full_mask = bt._pattern_mask_cache[("demo", False)]
        self.assertEqual(full_mask.shape, (6, 2))
        self.assertEqual(full_mask[:1].sum(), 0)
        self.assertTrue(np.array_equal(full_mask[1:4], partial_mask))
        self.assertEqual(full_mask[4:].sum(), 0)

    def test_plot_wealth_curves_uses_last_analyze_order_and_returns_summary(self):
        bt = Backtest.__new__(Backtest)
        bt._last_stats_collection = SimpleNamespace(
            stats_map={
                "trend_benchmark": object(),
                "trend_base": object(),
                "trend_amount1.5x": object(),
            }
        )

        def _fake_run(*, pattern: str, target_horizon, trade_price_mode, **kwargs):
            cagr_map = {
                "trend_benchmark": 0.08,
                "trend_base": 0.11,
                "trend_amount1.5x": 0.13,
            }
            return _FakeSimulator(pattern=pattern, cagr=cagr_map[pattern])

        bt.run = _fake_run

        with warnings.catch_warnings():
            warnings.simplefilter("ignore", UserWarning)
            summary = bt.plot_wealth_curves(target_horizon="1M", trade_price_mode="당일종가")

        self.assertEqual(
            list(summary.index),
            ["trend_benchmark", "trend_base", "trend_amount1.5x"],
        )
        self.assertTrue(np.isclose(summary.loc["trend_amount1.5x", "cagr"], 0.13))
        self.assertTrue(np.isclose(summary.loc["trend_base", "final_wealth"], 1.10))
        self.assertIn("mdd", summary.columns)
        self.assertIn("ann_vol", summary.columns)

    def test_plot_wealth_curves_reuses_stats_color_map(self):
        class _FakeStatsCollection:
            def __init__(self):
                self.stats_map = {
                    "benchmark": object(),
                    "trend_base": object(),
                    "trend_amount1.5x": object(),
                }

            def _ordered_pattern_names(self, patterns=None):
                if patterns is None:
                    return list(self.stats_map.keys())
                return list(patterns)

            def _pattern_colors(self, names):
                return {
                    "benchmark": "black",
                    "trend_base": "#D56062",
                    "trend_amount1.5x": "#067BC2",
                }

            def _apply_legend_order(self, ax, names, display_map=None):
                ax.legend(loc="upper left", fontsize=9, frameon=True)

        bt = Backtest.__new__(Backtest)
        bt._last_stats_collection = _FakeStatsCollection()

        def _fake_run(*, pattern: str, target_horizon, trade_price_mode, **kwargs):
            cagr_map = {
                "benchmark": 0.08,
                "trend_base": 0.11,
                "trend_amount1.5x": 0.13,
            }
            return _FakeSimulator(pattern=pattern, cagr=cagr_map[pattern])

        bt.run = _fake_run

        with warnings.catch_warnings():
            warnings.simplefilter("ignore", UserWarning)
            bt.plot_wealth_curves(target_horizon="1M", trade_price_mode="당일종가")

        ax = plt.gcf().axes[0]
        colors = [line.get_color() for line in ax.lines]
        self.assertEqual(colors, ["black", "#D56062", "#067BC2"])

    def test_stats_plot_uses_absolute_day_exposure(self):
        dates = pd.date_range("2025-01-01", periods=5, freq="B").to_numpy()
        horizons = [("1W", 5)]
        benchmark = Stats.create_daily(dates, horizons)
        alpha = Stats.create_daily(dates, horizons)

        benchmark.daily_arith[0, 0] = 0.01
        benchmark.daily_rise[0, 0] = 1.0
        alpha.daily_arith[0, :2] = [0.01, 0.02]
        alpha.daily_rise[0, :2] = [1.0, 1.0]

        stats = StatsCollection(
            {"benchmark": benchmark, "alpha": alpha},
            benchmark_names={"benchmark"},
        )

        with warnings.catch_warnings():
            warnings.simplefilter("ignore", UserWarning)
            _, axes = stats.plot(return_handles=True)

        exposure_lines = axes[3].lines
        self.assertEqual(axes[3].get_title(), "Pattern Exposure (%)")
        self.assertTrue(np.isclose(exposure_lines[0].get_ydata()[0], 20.0))
        self.assertTrue(np.isclose(exposure_lines[1].get_ydata()[0], 40.0))

    def test_stats_plot_uses_absolute_event_exposure(self):
        dates = pd.date_range("2025-01-01", periods=4, freq="B").to_numpy()
        horizons = [("1W", 5)]
        benchmark = Stats.create(dates, horizons)
        alpha = Stats.create(dates, horizons)

        benchmark.counts[0, 0] = 4
        benchmark.sum_ret[0, 0] = 0.20
        benchmark.sum_log[0, 0] = 4.0 * np.log1p(0.05)
        benchmark.pos_counts[0, 0] = 4

        alpha.counts[0, 0] = 3
        alpha.sum_ret[0, 0] = 0.15
        alpha.sum_log[0, 0] = 3.0 * np.log1p(0.05)
        alpha.pos_counts[0, 0] = 3

        stats = StatsCollection(
            {"benchmark": benchmark, "alpha": alpha},
            benchmark_names={"benchmark"},
            exposure_opportunity_counts=np.asarray([[10, 10, 0, 0]], dtype=np.int64),
        )

        with warnings.catch_warnings():
            warnings.simplefilter("ignore", UserWarning)
            _, axes = stats.plot(return_handles=True)

        exposure_lines = axes[3].lines
        self.assertTrue(np.isclose(exposure_lines[0].get_ydata()[0], 20.0))
        self.assertTrue(np.isclose(exposure_lines[1].get_ydata()[0], 15.0))

    def test_simulator_plot_shades_regime_spans_when_present(self):
        dates = pd.date_range("2025-01-01", periods=4, freq="B")
        sim = Simulator(
            dates=dates.to_numpy(),
            prices=np.ones((4, 1), dtype=np.float64),
        )
        sim.data = pd.DataFrame(
            {
                "wealth": [1.0, 1.02, 1.03, 1.05],
                "exposure": [0.2, 0.25, 0.15, 0.3],
                "selected_count": [1.0, 0.0, 1.0, 0.0],
                "active_count": [1.0, 1.0, 1.0, 1.0],
            },
            index=dates,
        )
        sim.data.attrs["regime_active_mask"] = np.asarray([True, False, True, True], dtype=np.bool_)
        sim.pattern = "demo"
        sim.target_horizon = "1M"
        sim.target_horizon_days = 20
        sim.aggregate_lookback = "1Y"
        sim.fallback_exposure = 0.5
        sim.max_weight_per_stock = float("nan")
        sim.run_years = 1.0
        sim.total_return = 0.05
        sim.cagr = 0.05
        sim.max_drawdown = -0.02
        sim.cohort_win_rate = 0.6
        sim.cohort_payoff_ratio = 1.4
        sim.active_day_ratio = 0.5
        sim.total_buy_fee_paid = 0.001
        sim.total_sell_fee_paid = 0.002

        with warnings.catch_warnings():
            warnings.simplefilter("ignore", UserWarning)
            _, axes = sim.plot(return_handles=True)

        self.assertGreaterEqual(len(axes[0].patches), 2)
        self.assertGreaterEqual(len(axes[2].patches), 2)

    def test_plot_wealth_curves_shades_regime_spans_when_present(self):
        bt = Backtest.__new__(Backtest)
        bt._last_stats_collection = SimpleNamespace(
            stats_map={
                "benchmark": object(),
                "trend_base": object(),
            }
        )

        def _fake_run(*, pattern: str, target_horizon, trade_price_mode, **kwargs):
            return _FakeSimulator(
                pattern=pattern,
                cagr=0.1,
                regime_mask=np.asarray([True, False, True], dtype=np.bool_),
            )

        bt.run = _fake_run

        with warnings.catch_warnings():
            warnings.simplefilter("ignore", UserWarning)
            bt.plot_wealth_curves(target_horizon="1M", trade_price_mode="당일종가")

        ax = plt.gcf().axes[0]
        self.assertGreaterEqual(len(ax.patches), 2)


if __name__ == "__main__":
    unittest.main()
