from __future__ import annotations

import unittest
import warnings

import matplotlib
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

from metricstudio.backtest import (
    TRIM_MODE_REMOVE,
    Backtest,
    _numba_accumulate_all_stock_window,
    _numba_accumulate_trim_for_date,
    _pattern_cache_signature,
)
from metricstudio.univ import Univ
from metricstudio.patterns import AllStockPattern, BasePattern, Bollinger, Disparity, MFI
from metricstudio.regime import Regime
from metricstudio.simulate import Simulator
from metricstudio.stats import Stats, StatsCollection

matplotlib.use("Agg")


class _FakeSimulator:
    def __init__(
        self,
        pattern: str,
        cagr: float,
        regime_mask: np.ndarray | None = None,
        kospi_curve: np.ndarray | None = None,
    ):
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
        if kospi_curve is not None:
            self._frame.attrs["kospi_reference_curve"] = np.asarray(kospi_curve, dtype=np.float64).copy()
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

    def plot(
        self,
        figsize=(12, 5),
        show_kospi: bool = False,
        return_handles: bool = False,
        axes=None,
    ):
        created_axes = axes is None
        if created_axes:
            fig, axes = plt.subplots(1, 3, figsize=figsize)
        else:
            axes = np.asarray(axes, dtype=object).reshape(-1)
            fig = axes[0].figure
            for ax in axes:
                ax.clear()

        axes[0].plot(self._frame.index, self._frame["exposure"], label="Daily exposure")
        axes[1].plot(self._frame.index, np.arange(len(self._frame)), label="New cohort count")
        axes[2].plot(self._frame.index, self._frame["wealth"], label=self.pattern)
        if show_kospi and "kospi_reference_curve" in self._frame.attrs:
            axes[2].plot(
                self._frame.index,
                self._frame.attrs["kospi_reference_curve"],
                label="KOSPI",
            )

        if return_handles:
            return fig, axes
        if created_axes:
            plt.show()
        return None


class _MaskPattern(BasePattern):
    def __init__(self, name: str, mask):
        super().__init__(name=name)
        self._mask = np.asarray(mask, dtype=np.bool_)

    def _base_mask(self, values: np.ndarray) -> np.ndarray:
        return self._mask.copy()


class _RankMetricPattern(BasePattern):
    def __init__(self, name: str, mask, source: str = "price", default_order: str = "desc"):
        super().__init__(name=name)
        self._mask = np.asarray(mask, dtype=np.bool_)
        self._source = str(source)
        self._default_order = str(default_order)

    def _required_stock_fields(self) -> tuple[str, ...]:
        if self._source == "price":
            return ()
        return (self._source,)

    def _base_mask(self, values: np.ndarray) -> np.ndarray:
        return self._mask.copy()

    def rank_metrics(self) -> dict[str, str]:
        return {"value": self._default_order}

    def _compute_rank_metric_series(self, metric: str, prices: np.ndarray, get_stock_field):
        if str(metric) != "value":
            raise KeyError(metric)
        if self._source == "price":
            return np.asarray(prices, dtype=np.float64)
        return np.asarray(get_stock_field(self._source), dtype=np.float64)


class BacktestContextTests(unittest.TestCase):
    def tearDown(self):
        plt.close("all")

    def test_univ_excludes_reits_by_default(self):
        univ = Univ()

        self.assertTrue(univ.exclude_reits)
        self.assertNotIn("리츠", univ.dept_excludes)

    def _bind_regime(self, kind: str, values: list[bool]) -> Regime:
        regime = Regime().on(kind=kind, market="kospi")
        dates = pd.date_range("2025-01-01", periods=len(values), freq="B")
        frame = pd.DataFrame(
            {regime.kind: values},
            index=dates,
        )
        regime._bind(
            dates.to_numpy(),
            np.asarray(values, dtype=np.bool_),
            frame,
        )
        return regime

    def test_regime_invert_expression_inverts_bound_mask(self):
        panic = self._bind_regime("panic", [True, False, True])

        self.assertEqual((~panic).mask().tolist(), [False, True, False])

    def test_regime_set_operations_build_expected_masks(self):
        trend = self._bind_regime("trend", [True, True, False, False])
        contrarian = self._bind_regime("contrarian", [False, True, True, False])
        panic = self._bind_regime("panic", [True, False, True, False])

        self.assertEqual((trend + ~panic).mask().tolist(), [False, True, False, False])
        self.assertEqual((contrarian - panic).mask().tolist(), [False, True, False, False])
        self.assertEqual((trend | contrarian).mask().tolist(), [True, True, True, False])

    def test_regime_bool_guides_use_invert_operator(self):
        panic = self._bind_regime("panic", [True, False, True])

        with self.assertRaises(TypeError):
            not panic

    def test_regime_pattern_accepts_composed_regime(self):
        contrarian = self._bind_regime("contrarian", [False, True, True, False])
        panic = self._bind_regime("panic", [False, False, True, False])
        pattern = BasePattern(name="base").when(contrarian - panic)
        prices = np.asarray([10.0, 11.0, 12.0, 13.0], dtype=np.float64)

        self.assertEqual(
            pattern(prices).tolist(),
            [False, True, False, False],
        )

    def test_pattern_named_supports_chaining_call(self):
        pattern = MFI(name="before").on(trigger="above", threshold=50)

        renamed = pattern.named("after")

        self.assertIs(renamed, pattern)
        self.assertEqual(pattern.name, "after")

    def test_name_first_constructor_is_supported_for_windowed_patterns(self):
        bollinger = Bollinger("bb", 30, 1.5)
        disparity = Disparity("disp", 40)
        mfi = MFI("mfi", 10)

        self.assertEqual(bollinger.name, "bb")
        self.assertEqual(bollinger.window, 30)
        self.assertEqual(bollinger.sigma, 1.5)
        self.assertEqual(disparity.name, "disp")
        self.assertEqual(disparity.window, 40)
        self.assertEqual(mfi.name, "mfi")
        self.assertEqual(mfi.window, 10)

    def test_legacy_window_first_constructor_style_remains_supported(self):
        bollinger = Bollinger(30, name="bb")
        disparity = Disparity(40, name="disp")
        mfi = MFI(10, name="mfi")

        self.assertEqual(bollinger.name, "bb")
        self.assertEqual(bollinger.window, 30)
        self.assertEqual(disparity.name, "disp")
        self.assertEqual(disparity.window, 40)
        self.assertEqual(mfi.name, "mfi")
        self.assertEqual(mfi.window, 10)

    def test_bollinger_on_no_longer_accepts_loss_cut(self):
        with self.assertRaises(TypeError):
            Bollinger("bb").on(loss_cut="mid_stop")

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
        pattern = BasePattern(name="explicit").when(explicit_regime)
        resolved = bt._apply_default_regime(pattern)

        self.assertIs(resolved, pattern)

    def test_store_runtime_cache_expands_partial_mask_to_full_dates(self):
        bt = Backtest.__new__(Backtest)
        bt.dates = np.arange(6)
        bt.start_idx = 1
        bt.end_idx = 4
        bt.horizon_offsets = np.asarray([5, 10], dtype=np.int64)
        bt._pattern_mask_cache = {}
        bt._pattern_policy_id_cache = {}
        bt._pattern_trade_profile_cache = {}
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

    def test_build_pattern_policy_id_matrix_preserves_branch_trade_profiles(self):
        bt = Backtest.__new__(Backtest)
        bt.dates = pd.date_range("2025-01-01", periods=4, freq="B").to_numpy()
        bt.prices = np.ones((4, 1), dtype=np.float64)
        bt.codes = ["A"]
        bt.start_idx = 0
        bt.end_idx = 4
        bt.regime = None
        bt._pattern_mask_cache = {}
        bt._pattern_policy_id_cache = {}
        bt._pattern_trade_profile_cache = {}
        bt._stock_field_matrix_cache = {}
        bt._market_values_cache = {}
        bt._regime_frame_cache = {}

        trend = _MaskPattern("trend", [True, True, False, False]).trade(target_horizon="1M")
        contra = _MaskPattern("contra", [False, False, True, False]).trade(
            target_horizon="3W",
            stop_loss_pct=8,
            cohort_scale=0.5,
        )
        router = (trend | contra).named("router")

        policy_ids, profiles = bt._build_pattern_policy_id_matrix("router", router)

        self.assertEqual(policy_ids[:, 0].tolist(), [1, 1, 2, 0])
        self.assertEqual(profiles[1], ("1M", None, None, None))
        self.assertEqual(profiles[2], ("3W", 0.08, None, 0.5))

    def test_build_pattern_policy_id_matrix_preserves_branch_profile_when_other_branch_is_default(self):
        bt = Backtest.__new__(Backtest)
        bt.dates = pd.date_range("2025-01-01", periods=4, freq="B").to_numpy()
        bt.prices = np.ones((4, 1), dtype=np.float64)
        bt.codes = ["A"]
        bt.start_idx = 0
        bt.end_idx = 4
        bt.regime = None
        bt._pattern_mask_cache = {}
        bt._pattern_policy_id_cache = {}
        bt._pattern_trade_profile_cache = {}
        bt._stock_field_matrix_cache = {}
        bt._market_values_cache = {}
        bt._regime_frame_cache = {}

        trend = _MaskPattern("trend", [True, True, False, False])
        contra = _MaskPattern("contra", [False, False, True, False]).trade(
            target_horizon="3W",
            cohort_scale=0.35,
        )
        router = (trend | contra).named("router")

        policy_ids, profiles = bt._build_pattern_policy_id_matrix("router", router)

        self.assertEqual(policy_ids[:, 0].tolist(), [1, 1, 2, 0])
        self.assertEqual(profiles[1], (None, None, None, None))
        self.assertEqual(profiles[2], ("3W", None, None, 0.35))

    def test_build_pattern_mask_matrix_applies_pattern_nmax_rank_order(self):
        bt = Backtest.__new__(Backtest)
        bt.dates = pd.date_range("2025-01-01", periods=4, freq="B").to_numpy()
        bt.prices = np.ones((4, 4), dtype=np.float64)
        bt.codes = ["A", "B", "C", "D"]
        bt.start_idx = 0
        bt.end_idx = 4
        bt.regime = None
        bt._pattern_mask_cache = {}
        bt._pattern_policy_id_cache = {}
        bt._pattern_trade_profile_cache = {}
        bt._pattern_exit_mask_cache = {}
        bt._pattern_exit_index_cache = {}
        bt._stock_field_matrix_cache = {}
        bt._pattern_nmax_node_cache = {}
        bt._pattern_nmax_series_cache = {}
        bt._market_values_cache = {}
        bt._regime_frame_cache = {}

        pattern = _MaskPattern("cap", [True, True, False, False]).nmax(2)
        bt._get_nmax_rank_key = lambda pattern_fn, date_idx, col_idx: {
            0: (0.20, -2.0, -0.99, -70.0, 0),
            1: (0.10, -2.0, -0.90, -50.0, 1),
            2: (0.10, -1.5, -0.96, -54.0, 2),
            3: (0.10, -1.5, -0.96, -56.0, 3),
        }[int(col_idx)]

        mask_matrix = bt._build_pattern_mask_matrix("cap", pattern)
        policy_ids, profiles = bt._build_pattern_policy_id_matrix("cap", pattern)

        self.assertEqual(mask_matrix[0].tolist(), [False, True, False, True])
        self.assertEqual(mask_matrix[1].tolist(), [False, True, False, True])
        self.assertEqual(policy_ids[0].tolist(), [0, 1, 0, 1])
        self.assertEqual(policy_ids[1].tolist(), [0, 1, 0, 1])
        self.assertEqual(profiles[1], (None, None, None, None))

    def test_build_pattern_mask_matrix_can_apply_rank_sum_profile(self):
        bt = Backtest.__new__(Backtest)
        bt.dates = pd.date_range("2025-01-01", periods=4, freq="B").to_numpy()
        bt.prices = np.asarray(
            [
                [100.0, 90.0, 80.0],
                [100.0, 90.0, 80.0],
                [1.0, 1.0, 1.0],
                [1.0, 1.0, 1.0],
            ],
            dtype=np.float64,
        )
        bt.codes = ["A", "B", "C"]
        bt.start_idx = 0
        bt.end_idx = 4
        bt.regime = None
        bt._pattern_mask_cache = {}
        bt._pattern_policy_id_cache = {}
        bt._pattern_trade_profile_cache = {}
        bt._pattern_exit_mask_cache = {}
        bt._pattern_exit_index_cache = {}
        bt._stock_field_matrix_cache = {
            "amount": np.asarray(
                [
                    [100.0, 10.0, 20.0],
                    [100.0, 10.0, 20.0],
                    [1.0, 1.0, 1.0],
                    [1.0, 1.0, 1.0],
                ],
                dtype=np.float64,
            )
        }
        bt._pattern_nmax_node_cache = {}
        bt._pattern_nmax_series_cache = {}
        bt._market_values_cache = {}
        bt._regime_frame_cache = {}

        price_rank = _RankMetricPattern("price_rank", [True, True, False, False], source="price", default_order="desc")
        amount_rank = _RankMetricPattern("amount_rank", [True, True, False, False], source="amount", default_order="asc")
        pattern = (price_rank + amount_rank).rank_by(
            (price_rank, "value.desc"),
            (amount_rank, "value.asc"),
        ).nmax(1)

        mask_matrix = bt._build_pattern_mask_matrix("rank_sum", pattern)

        self.assertEqual(mask_matrix[0].tolist(), [False, True, False])
        self.assertEqual(mask_matrix[1].tolist(), [False, True, False])

    def test_build_pattern_mask_matrix_can_use_market_cap_as_nmax_tiebreaker(self):
        bt = Backtest.__new__(Backtest)
        bt.dates = pd.date_range("2025-01-01", periods=4, freq="B").to_numpy()
        bt.prices = np.ones((4, 4), dtype=np.float64)
        bt.codes = ["A", "B", "C", "D"]
        bt.start_idx = 0
        bt.end_idx = 4
        bt.regime = None
        bt._pattern_mask_cache = {}
        bt._pattern_policy_id_cache = {}
        bt._pattern_trade_profile_cache = {}
        bt._pattern_exit_mask_cache = {}
        bt._pattern_exit_index_cache = {}
        bt._stock_field_matrix_cache = {
            "marketcap": np.tile(
                np.asarray([[100.0, 200.0, 400.0, 300.0]], dtype=np.float64),
                (4, 1),
            )
        }
        bt._pattern_nmax_node_cache = {}
        bt._pattern_nmax_series_cache = {}
        bt._market_values_cache = {}
        bt._regime_frame_cache = {}

        pattern = _MaskPattern("cap_mc", [True, True, False, False]).nmax(2, market_cap=True)
        bt._get_nmax_metric_series = lambda node, metric_name, col_idx: np.asarray(
            [0.0, {0: 0.01, 1: 0.02, 2: 0.30, 3: 0.20}[int(col_idx)], 0.0, 0.0],
            dtype=np.float64,
        )

        mask_matrix = bt._build_pattern_mask_matrix("cap_mc", pattern)

        self.assertEqual(mask_matrix[0].tolist(), [False, False, True, True])
        self.assertEqual(mask_matrix[1].tolist(), [False, False, True, True])

    def test_backtest_plot_stacks_last_stats_and_simulator_into_single_figure(self):
        dates = pd.date_range("2025-01-01", periods=5, freq="B").to_numpy()
        horizons = [("1W", 5), ("1M", 20)]
        benchmark = Stats.create_daily(dates, horizons)
        alpha = Stats.create_daily(dates, horizons)

        benchmark.daily_arith[0, :2] = [0.01, 0.015]
        benchmark.daily_rise[0, :2] = [1.0, 1.0]
        benchmark.daily_arith[1, :2] = [0.02, 0.018]
        benchmark.daily_rise[1, :2] = [1.0, 0.0]

        alpha.daily_arith[0, :2] = [0.02, 0.025]
        alpha.daily_rise[0, :2] = [1.0, 1.0]
        alpha.daily_arith[1, :2] = [0.03, 0.028]
        alpha.daily_rise[1, :2] = [1.0, 1.0]

        stats = StatsCollection(
            {"benchmark": benchmark, "alpha": alpha},
            benchmark_names={"benchmark"},
        )
        sim = _FakeSimulator(
            pattern="alpha",
            cagr=0.12,
            kospi_curve=np.asarray([1.0, 1.01, 1.02], dtype=np.float64),
        )
        bt = Backtest.__new__(Backtest)
        bt._last_stats_collection = stats
        bt._last_simulator = sim

        with warnings.catch_warnings():
            warnings.simplefilter("ignore", UserWarning)
            fig, axes_map = bt.plot(
                patterns=["benchmark", "alpha"],
                annualized=True,
                show_kospi=True,
                return_handles=True,
            )

        self.assertEqual(len(fig.axes), 7)
        self.assertEqual(len(axes_map["stats"]), 4)
        self.assertEqual(len(axes_map["simulator"]), 3)
        self.assertIs(axes_map["stats"][0].figure, fig)
        self.assertIs(axes_map["simulator"][0].figure, fig)
        self.assertEqual(
            [line.get_label() for line in axes_map["simulator"][2].lines],
            ["alpha", "KOSPI"],
        )

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
        sim.data.attrs["kospi_reference_curve"] = np.asarray([1.0, 1.01, 1.02, 1.03], dtype=np.float64)
        sim.pattern = "demo"
        sim.target_horizon = "1M"
        sim.target_horizon_days = 20
        sim.max_weight_per_stock_in_cohort = float("nan")
        sim.run_years = 1.0
        sim.total_return = 0.05
        sim.cagr = 0.05
        sim.max_drawdown = -0.02
        sim.cohort_win_rate = 0.6
        sim.cohort_payoff_ratio = 1.4
        sim.active_day_ratio = 0.5
        sim.mean_turnover = 0.02
        sim.annual_turnover = 4.8
        sim.total_buy_fee_paid = 0.001
        sim.total_sell_fee_paid = 0.002

        with warnings.catch_warnings():
            warnings.simplefilter("ignore", UserWarning)
            _, axes = sim.plot(show_kospi=True, return_handles=True)

        self.assertGreaterEqual(len(axes[0].patches), 2)
        self.assertIn("포트 평균 종목수", axes[1].texts[0].get_text())
        self.assertIn("코호트 평균 종목수", axes[1].texts[0].get_text())
        self.assertGreaterEqual(len(axes[2].patches), 2)
        self.assertEqual([line.get_label() for line in axes[2].lines], ["demo", "KOSPI"])
        self.assertIn("회전율(연환산)", axes[2].texts[0].get_text())

    def test_simulator_plot_keeps_wealth_y_ticks_on_left_when_axes_are_injected(self):
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
        sim.data.attrs["kospi_reference_curve"] = np.asarray([1.0, 1.01, 1.02, 1.03], dtype=np.float64)
        sim.pattern = "demo"
        sim.target_horizon = "1M"
        sim.target_horizon_days = 20
        sim.max_weight_per_stock_in_cohort = float("nan")
        sim.run_years = 1.0
        sim.total_return = 0.05
        sim.cagr = 0.05
        sim.max_drawdown = -0.02
        sim.cohort_win_rate = 0.6
        sim.cohort_payoff_ratio = 1.4
        sim.active_day_ratio = 0.5
        sim.mean_turnover = 0.02
        sim.annual_turnover = 4.8
        sim.total_buy_fee_paid = 0.001
        sim.total_sell_fee_paid = 0.002

        fig, axes = plt.subplots(1, 3, figsize=(12, 5))
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", UserWarning)
            sim.plot(show_kospi=True, axes=axes)

        self.assertEqual(axes[2].yaxis.get_ticks_position(), "left")
        self.assertEqual([line.get_label() for line in axes[2].lines], ["demo", "KOSPI"])

    def test_simulator_summary_includes_annualized_turnover(self):
        dates = pd.date_range("2025-01-01", periods=4, freq="B")
        prices = np.ones((4, 1), dtype=np.float64)
        sim = Simulator(
            dates=dates.to_numpy(),
            prices=prices,
            codes=["A"],
            buy_fee=0.0,
            sell_fee=0.0,
        )

        pattern_mask = np.zeros((4, 1), dtype=np.bool_)
        pattern_mask[1, 0] = True

        sim.run(
            start_idx=0,
            end_idx=4,
            pattern="turnover",
            target_horizon="1M",
            target_horizon_days=3,
            pattern_mask=pattern_mask,
            pattern_policy_id_matrix=None,
            policy_horizon_days=np.asarray([3], dtype=np.int32),
            policy_stop_loss_pct=np.asarray([np.nan], dtype=np.float64),
            policy_take_profit_pct=np.asarray([np.nan], dtype=np.float64),
            policy_cohort_scale=np.asarray([1.0], dtype=np.float64),
            pattern_exit_mask=np.zeros((4, 1), dtype=np.bool_),
            pattern_dynamic_exit_index=None,
            stop_loss_pct=None,
            take_profit_pct=None,
            execution_lag_days=0,
            execution_price_mode="same_close",
            allow_reentry=True,
            min_cohort_size=1,
        )

        summary = sim.summary()

        self.assertTrue(np.isclose(summary["mean_turnover"], 1.0 / 12.0))
        self.assertTrue(np.isclose(summary["annual_turnover"], 20.0))

    def test_simulator_respects_branch_specific_horizon_days(self):
        dates = pd.date_range("2025-01-01", periods=5, freq="B")
        prices = np.ones((5, 2), dtype=np.float64)
        sim = Simulator(
            dates=dates.to_numpy(),
            prices=prices,
            codes=["A", "B"],
        )

        pattern_mask = np.zeros((5, 2), dtype=np.bool_)
        pattern_mask[1] = [True, True]
        pattern_policy_id_matrix = np.zeros((5, 2), dtype=np.int16)
        pattern_policy_id_matrix[1] = [1, 2]

        sim.run(
            start_idx=0,
            end_idx=5,
            pattern="router",
            target_horizon="1M",
            target_horizon_days=3,
            pattern_mask=pattern_mask,
            pattern_policy_id_matrix=pattern_policy_id_matrix,
            policy_horizon_days=np.asarray([3, 1, 3], dtype=np.int32),
            policy_stop_loss_pct=np.asarray([np.nan, np.nan, np.nan], dtype=np.float64),
            policy_take_profit_pct=np.asarray([np.nan, np.nan, np.nan], dtype=np.float64),
            policy_cohort_scale=np.asarray([1.0, 1.0, 1.0], dtype=np.float64),
            pattern_exit_mask=np.zeros((5, 2), dtype=np.bool_),
            pattern_dynamic_exit_index=None,
            stop_loss_pct=None,
            take_profit_pct=None,
            execution_lag_days=0,
            execution_price_mode="same_close",
            allow_reentry=True,
            min_cohort_size=1,
        )

        self.assertEqual(len(sim.port_at(dates[1])), 2)
        self.assertEqual(len(sim.port_at(dates[2])), 1)

    def test_simulator_respects_branch_specific_stop_loss(self):
        dates = pd.date_range("2025-01-01", periods=4, freq="B")
        prices = np.asarray(
            [
                [1.0, 1.0],
                [1.0, 1.0],
                [0.84, 1.0],
                [0.84, 1.0],
            ],
            dtype=np.float64,
        )
        sim = Simulator(
            dates=dates.to_numpy(),
            prices=prices,
            codes=["A", "B"],
        )

        pattern_mask = np.zeros((4, 2), dtype=np.bool_)
        pattern_mask[1] = [True, True]
        pattern_policy_id_matrix = np.zeros((4, 2), dtype=np.int16)
        pattern_policy_id_matrix[1] = [1, 2]

        sim.run(
            start_idx=0,
            end_idx=4,
            pattern="router",
            target_horizon="1M",
            target_horizon_days=3,
            pattern_mask=pattern_mask,
            pattern_policy_id_matrix=pattern_policy_id_matrix,
            policy_horizon_days=np.asarray([3, 3, 3], dtype=np.int32),
            policy_stop_loss_pct=np.asarray([np.nan, 0.10, np.nan], dtype=np.float64),
            policy_take_profit_pct=np.asarray([np.nan, np.nan, np.nan], dtype=np.float64),
            policy_cohort_scale=np.asarray([1.0, 1.0, 1.0], dtype=np.float64),
            pattern_exit_mask=np.zeros((4, 2), dtype=np.bool_),
            pattern_dynamic_exit_index=None,
            stop_loss_pct=None,
            take_profit_pct=None,
            execution_lag_days=0,
            execution_price_mode="same_close",
            allow_reentry=True,
            min_cohort_size=1,
        )

        self.assertEqual(len(sim.port_at(dates[1])), 2)
        self.assertEqual(len(sim.port_at(dates[2])), 1)

    def test_simulator_caps_new_cohort_size(self):
        dates = pd.date_range("2025-01-01", periods=4, freq="B")
        prices = np.ones((4, 3), dtype=np.float64)
        sim = Simulator(
            dates=dates.to_numpy(),
            prices=prices,
            codes=["A", "B", "C"],
        )

        pattern_mask = np.zeros((4, 3), dtype=np.bool_)
        pattern_mask[1] = [True, True, True]

        sim.run(
            start_idx=0,
            end_idx=4,
            pattern="cap_test",
            target_horizon="1M",
            target_horizon_days=3,
            pattern_mask=pattern_mask,
            pattern_policy_id_matrix=None,
            policy_horizon_days=np.asarray([3], dtype=np.int32),
            policy_stop_loss_pct=np.asarray([np.nan], dtype=np.float64),
            policy_take_profit_pct=np.asarray([np.nan], dtype=np.float64),
            policy_cohort_scale=np.asarray([1.0], dtype=np.float64),
            pattern_exit_mask=np.zeros((4, 3), dtype=np.bool_),
            pattern_dynamic_exit_index=None,
            stop_loss_pct=None,
            take_profit_pct=None,
            execution_lag_days=0,
            execution_price_mode="same_close",
            allow_reentry=True,
            min_cohort_size=1,
            max_cohort_size=2,
        )

        self.assertEqual(len(sim.port_at(dates[1])), 2)
        self.assertTrue(np.isclose(sim.summary()["max_cohort_size"], 2.0))

    def test_simulator_keeps_full_cohort_budget_with_partial_fill(self):
        dates = pd.date_range("2025-01-01", periods=4, freq="B")
        prices = np.ones((4, 1), dtype=np.float64)
        sim = Simulator(
            dates=dates.to_numpy(),
            prices=prices,
            codes=["A"],
            buy_fee=0.0,
            sell_fee=0.0,
        )

        pattern_mask = np.zeros((4, 1), dtype=np.bool_)
        pattern_mask[1, 0] = True

        sim.run(
            start_idx=0,
            end_idx=4,
            pattern="partial_fill",
            target_horizon="1M",
            target_horizon_days=3,
            pattern_mask=pattern_mask,
            pattern_policy_id_matrix=None,
            policy_horizon_days=np.asarray([3], dtype=np.int32),
            policy_stop_loss_pct=np.asarray([np.nan], dtype=np.float64),
            policy_take_profit_pct=np.asarray([np.nan], dtype=np.float64),
            policy_cohort_scale=np.asarray([1.0], dtype=np.float64),
            pattern_exit_mask=np.zeros((4, 1), dtype=np.bool_),
            pattern_dynamic_exit_index=None,
            stop_loss_pct=None,
            take_profit_pct=None,
            execution_lag_days=0,
            execution_price_mode="same_close",
            allow_reentry=True,
            min_cohort_size=1,
            max_weight_per_stock_in_cohort=0.2,
        )

        holding = sim.port_at(dates[1]).reset_index().iloc[0]
        self.assertTrue(np.isclose(float(holding["value"]), 1.0 / 15.0))
        self.assertTrue(np.isclose(float(holding["cohort_value"]), 1.0 / 3.0))
        self.assertTrue(np.isclose(float(holding["weight_in_cohort"]), 0.2))
        self.assertTrue(np.isclose(sim.summary()["max_weight_per_stock_in_cohort"], 0.2))

    def test_simulator_fully_invests_single_selected_stock_by_default(self):
        dates = pd.date_range("2025-01-01", periods=4, freq="B")
        prices = np.ones((4, 1), dtype=np.float64)
        sim = Simulator(
            dates=dates.to_numpy(),
            prices=prices,
            codes=["A"],
            buy_fee=0.0,
            sell_fee=0.0,
        )

        pattern_mask = np.zeros((4, 1), dtype=np.bool_)
        pattern_mask[1, 0] = True

        sim.run(
            start_idx=0,
            end_idx=4,
            pattern="full_fill",
            target_horizon="1M",
            target_horizon_days=3,
            pattern_mask=pattern_mask,
            pattern_policy_id_matrix=None,
            policy_horizon_days=np.asarray([3], dtype=np.int32),
            policy_stop_loss_pct=np.asarray([np.nan], dtype=np.float64),
            policy_take_profit_pct=np.asarray([np.nan], dtype=np.float64),
            policy_cohort_scale=np.asarray([1.0], dtype=np.float64),
            pattern_exit_mask=np.zeros((4, 1), dtype=np.bool_),
            pattern_dynamic_exit_index=None,
            stop_loss_pct=None,
            take_profit_pct=None,
            execution_lag_days=0,
            execution_price_mode="same_close",
            allow_reentry=True,
            min_cohort_size=1,
        )

        holding = sim.port_at(dates[1]).reset_index().iloc[0]
        self.assertTrue(np.isclose(float(holding["value"]), 1.0 / 3.0))
        self.assertTrue(np.isclose(float(holding["cohort_value"]), 1.0 / 3.0))
        self.assertTrue(np.isclose(float(holding["weight_in_cohort"]), 1.0))
        self.assertTrue(np.isnan(sim.summary()["max_weight_per_stock_in_cohort"]))

    def test_simulator_respects_branch_specific_cohort_scale(self):
        dates = pd.date_range("2025-01-01", periods=4, freq="B")
        prices = np.ones((4, 2), dtype=np.float64)
        sim = Simulator(
            dates=dates.to_numpy(),
            prices=prices,
            codes=["A", "B"],
        )

        pattern_mask = np.zeros((4, 2), dtype=np.bool_)
        pattern_mask[1] = [True, True]
        pattern_policy_id_matrix = np.zeros((4, 2), dtype=np.int16)
        pattern_policy_id_matrix[1] = [1, 2]

        sim.run(
            start_idx=0,
            end_idx=4,
            pattern="router",
            target_horizon="1M",
            target_horizon_days=3,
            pattern_mask=pattern_mask,
            pattern_policy_id_matrix=pattern_policy_id_matrix,
            policy_horizon_days=np.asarray([3, 3, 3], dtype=np.int32),
            policy_stop_loss_pct=np.asarray([np.nan, np.nan, np.nan], dtype=np.float64),
            policy_take_profit_pct=np.asarray([np.nan, np.nan, np.nan], dtype=np.float64),
            policy_cohort_scale=np.asarray([1.0, 1.0, 0.5], dtype=np.float64),
            pattern_exit_mask=np.zeros((4, 2), dtype=np.bool_),
            pattern_dynamic_exit_index=None,
            stop_loss_pct=None,
            take_profit_pct=None,
            execution_lag_days=0,
            execution_price_mode="same_close",
            allow_reentry=True,
            min_cohort_size=1,
        )

        holdings = sim.port_at(dates[1]).reset_index()
        cohort_values = holdings.groupby("cohort_id")["cohort_value"].first().sort_index()
        self.assertEqual(cohort_values.index.tolist(), [1, 2])
        self.assertTrue(np.isclose(cohort_values.iloc[0] / cohort_values.iloc[1], 2.0, atol=0.05))

    def test_all_stock_fast_accumulator_matches_trim_zero_daily(self):
        prices = np.asarray(
            [
                [10.0, 20.0, np.nan],
                [11.0, 18.0, 30.0],
                [12.0, 21.0, 27.0],
                [13.0, np.nan, 29.0],
                [14.0, 25.0, 31.0],
            ],
            dtype=np.float64,
        )
        horizon_offsets = np.asarray([1, 2], dtype=np.int64)
        num_h = len(horizon_offsets)
        num_dates = prices.shape[0]

        fast_counts = np.zeros((num_h, num_dates), dtype=np.int64)
        fast_sum_ret = np.zeros((num_h, num_dates), dtype=np.float64)
        fast_sum_log = np.zeros((num_h, num_dates), dtype=np.float64)
        fast_pos_counts = np.zeros((num_h, num_dates), dtype=np.int64)
        fast_geom_invalid = np.zeros((num_h, num_dates), dtype=np.bool_)
        fast_daily_arith = np.full((num_h, num_dates), np.nan, dtype=np.float64)
        fast_daily_rise = np.full((num_h, num_dates), np.nan, dtype=np.float64)

        ref_counts = np.zeros((num_h, num_dates), dtype=np.int64)
        ref_sum_ret = np.zeros((num_h, num_dates), dtype=np.float64)
        ref_sum_log = np.zeros((num_h, num_dates), dtype=np.float64)
        ref_pos_counts = np.zeros((num_h, num_dates), dtype=np.int64)
        ref_geom_invalid = np.zeros((num_h, num_dates), dtype=np.bool_)
        ref_daily_arith = np.full((num_h, num_dates), np.nan, dtype=np.float64)
        ref_daily_rise = np.full((num_h, num_dates), np.nan, dtype=np.float64)

        _numba_accumulate_all_stock_window(
            prices,
            0,
            num_dates,
            horizon_offsets,
            fast_counts,
            fast_sum_ret,
            fast_sum_log,
            fast_pos_counts,
            fast_geom_invalid,
            fast_daily_arith,
            fast_daily_rise,
            True,
        )

        exit_mask = np.zeros((num_dates, prices.shape[1]), dtype=np.bool_)
        dynamic_exit_index = np.full((1, 1), -1, dtype=np.int32)
        for date_idx in range(num_dates):
            mask_row = np.isfinite(prices[date_idx]) & (prices[date_idx] > 0.0)
            _numba_accumulate_trim_for_date(
                prices,
                mask_row,
                date_idx,
                horizon_offsets,
                exit_mask,
                False,
                dynamic_exit_index,
                0,
                False,
                0.0,
                TRIM_MODE_REMOVE,
                ref_counts,
                ref_sum_ret,
                ref_sum_log,
                ref_pos_counts,
                ref_geom_invalid,
                ref_daily_arith,
                ref_daily_rise,
            )

        np.testing.assert_array_equal(fast_counts, ref_counts)
        np.testing.assert_allclose(fast_sum_ret, ref_sum_ret)
        np.testing.assert_allclose(fast_sum_log, ref_sum_log)
        np.testing.assert_array_equal(fast_pos_counts, ref_pos_counts)
        np.testing.assert_array_equal(fast_geom_invalid, ref_geom_invalid)
        np.testing.assert_allclose(fast_daily_arith, ref_daily_arith, equal_nan=True)
        np.testing.assert_allclose(fast_daily_rise, ref_daily_rise, equal_nan=True)

    def test_pattern_cache_signature_ignores_name_but_reflects_config(self):
        left = AllStockPattern("a")
        right = AllStockPattern("b")
        self.assertEqual(_pattern_cache_signature(left), _pattern_cache_signature(right))

        trimmed = AllStockPattern("c").trim(0.1)
        self.assertNotEqual(_pattern_cache_signature(left), _pattern_cache_signature(trimmed))

        capped = AllStockPattern("d").nmax(10)
        self.assertNotEqual(_pattern_cache_signature(left), _pattern_cache_signature(capped))

if __name__ == "__main__":
    unittest.main()
