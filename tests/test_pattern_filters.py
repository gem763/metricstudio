from __future__ import annotations

import numpy as np
import unittest

from metricstudio.patterns import AmountSurge, BasePattern, PanicRebound, RelativeStrength, RetestBreakout


class PatternFilterTests(unittest.TestCase):
    def test_pattern_nmax_supports_chaining_and_reset(self):
        pattern = BasePattern(name="base")

        self.assertIs(pattern.nmax(10, market_cap=True), pattern)
        self.assertEqual(pattern._resolved_max_cohort_size(), 10)
        self.assertTrue(pattern._resolved_nmax_market_cap())

        pattern.nmax(None)

        self.assertIsNone(pattern._resolved_max_cohort_size())
        self.assertFalse(pattern._resolved_nmax_market_cap())

    def test_pattern_mask_cache_reuses_same_underlying_price_series(self):
        class _CountingPattern(BasePattern):
            def __init__(self):
                super().__init__(name="count")
                self.calls = 0

            def _base_mask(self, values: np.ndarray) -> np.ndarray:
                self.calls += 1
                return np.ones(values.shape[0], dtype=np.bool_)

        prices = np.array([1.0, 2.0, 3.0], dtype=np.float64)
        pattern = _CountingPattern()

        pattern(prices)
        pattern(prices[:])

        self.assertEqual(pattern.calls, 1)

    def test_pattern_mask_cache_invalidates_when_stock_field_changes(self):
        class _AmountPattern(BasePattern):
            def __init__(self):
                super().__init__(name="amount_count")
                self.calls = 0

            def _base_mask(self, values: np.ndarray) -> np.ndarray:
                self.calls += 1
                amount = self._get_stock_values("amount")
                return np.asarray(amount > 0.0, dtype=np.bool_)

        prices = np.array([1.0, 2.0, 3.0], dtype=np.float64)
        amount = np.array([1.0, 1.0, 1.0], dtype=np.float64)
        pattern = _AmountPattern()

        pattern._set_stock_values("amount", amount)
        pattern(prices)
        pattern(prices[:])
        pattern._set_stock_values("amount", amount.copy())
        pattern(prices)

        self.assertEqual(pattern.calls, 2)

    def test_relative_strength_supports_on_market_argument(self):
        prices = np.array([100.0, 100.0, 100.0, 105.0, 110.0, 115.0])
        market = np.array([100.0, 101.0, 102.0, 103.0, 104.0, 105.0])

        pattern = RelativeStrength(name="rs").on(
            market="kospi",
            window=3,
            threshold=0.0,
        )
        pattern._set_market_values(market)

        self.assertEqual(
            pattern(prices).tolist(),
            [False, False, False, True, True, True],
        )

    def test_relative_strength_supports_market_then_on(self):
        prices = np.array([100.0, 100.0, 100.0, 105.0, 110.0, 115.0])
        market = np.array([100.0, 101.0, 102.0, 103.0, 104.0, 105.0])

        pattern = RelativeStrength(name="rs").market("kospi").on(
            window=3,
            threshold=0.0,
        )
        pattern._set_market_values(market)

        self.assertEqual(
            pattern(prices).tolist(),
            [False, False, False, True, True, True],
        )

    def test_relative_strength_supports_below_trigger_for_recent_losers(self):
        prices = np.array([100.0, 101.0, 102.0, 100.0, 95.0, 90.0])
        market = np.array([100.0, 101.0, 102.0, 103.0, 104.0, 105.0])

        pattern = RelativeStrength(name="rs").on(
            market="kospi",
            window=3,
            trigger="below",
            threshold=-0.08,
        )
        pattern._set_market_values(market)

        self.assertEqual(
            pattern(prices).tolist(),
            [False, False, False, False, True, True],
        )

    def test_amount_surge_marks_amount_spike_against_rolling_mean(self):
        prices = np.array([100.0, 101.0, 102.0, 103.0, 104.0, 105.0])
        amount = np.array([10.0, 12.0, 11.0, 30.0, 9.0, 50.0])

        pattern = AmountSurge(name="amt").on(window=3, threshold=1.5)
        pattern._set_stock_values("amount", amount)

        self.assertEqual(
            pattern(prices).tolist(),
            [False, False, False, True, False, True],
        )

    def test_retest_breakout_marks_first_rebound_after_retest(self):
        prices = np.array([10.0, 10.1, 10.2, 10.15, 10.25, 10.3, 10.6, 10.95, 10.35, 10.62, 10.7])

        pattern = RetestBreakout(name="retest").on(
            breakout_window=5,
            retest_tolerance=0.03,
        )

        self.assertEqual(
            pattern(prices).tolist(),
            [False, False, False, False, False, False, False, False, False, True, False],
        )

    def test_retest_breakout_ignores_failed_retest_below_tolerance(self):
        prices = np.array([10.0, 10.1, 10.2, 10.15, 10.25, 10.3, 10.6, 10.95, 9.9, 10.4, 10.5])

        pattern = RetestBreakout(name="retest").on(
            breakout_window=5,
            retest_tolerance=0.03,
        )

        self.assertEqual(
            pattern(prices).tolist(),
            [False, False, False, False, False, False, False, False, False, False, False],
        )

    def test_retest_breakout_expires_when_retest_takes_too_long(self):
        prices = np.array([10.0, 10.1, 10.2, 10.15, 10.25, 10.3, 10.6, 10.7, 10.8, 10.9, 10.28, 10.35])

        pattern = RetestBreakout(name="retest").on(
            breakout_window=5,
            retest_tolerance=0.03,
            max_retest_days=3,
        )

        self.assertEqual(
            pattern(prices).tolist(),
            [False, False, False, False, False, False, False, False, False, False, False, False],
        )

    def test_retest_breakout_can_require_amount_surge_on_breakout_day(self):
        prices = np.array([10.0, 10.1, 10.2, 10.15, 10.25, 10.3, 10.6, 10.55, 10.28, 10.35, 10.4])
        amount = np.array([10.0, 10.0, 10.0, 10.0, 10.0, 10.0, 25.0, 12.0, 11.0, 12.0, 11.0])

        pattern = RetestBreakout(name="retest").on(
            breakout_window=5,
            retest_tolerance=0.03,
            breakout_amount_threshold=1.5,
            breakout_amount_window=5,
        )
        pattern._set_stock_values("amount", amount)

        self.assertEqual(
            pattern(prices).tolist(),
            [False, False, False, False, False, False, False, False, False, True, False],
        )

    def test_retest_breakout_skips_breakout_without_amount_surge(self):
        prices = np.array([10.0, 10.1, 10.2, 10.15, 10.25, 10.3, 10.6, 10.55, 10.28, 10.35, 10.4])
        amount = np.array([10.0, 10.0, 10.0, 10.0, 10.0, 10.0, 12.0, 12.0, 11.0, 12.0, 11.0])

        pattern = RetestBreakout(name="retest").on(
            breakout_window=5,
            retest_tolerance=0.03,
            breakout_amount_threshold=1.5,
            breakout_amount_window=5,
        )
        pattern._set_stock_values("amount", amount)

        self.assertEqual(
            pattern(prices).tolist(),
            [False, False, False, False, False, False, False, False, False, False, False],
        )

    def test_panic_rebound_marks_rebound_after_sharp_drawdown(self):
        prices = np.array([100.0, 98.0, 95.0, 90.0, 82.0, 84.0, 87.0, 90.0, 89.0])

        pattern = PanicRebound(name="panic").on(
            drawdown_window=5,
            drawdown_min=-0.15,
            rebound_days=3,
            volume_spike=False,
        )

        self.assertEqual(
            pattern(prices).tolist(),
            [False, False, False, False, False, False, False, True, False],
        )

    def test_panic_rebound_requires_recent_panic_drawdown(self):
        prices = np.array([100.0, 99.0, 98.0, 97.0, 96.0, 97.0, 98.0, 99.0, 100.0])

        pattern = PanicRebound(name="panic").on(
            drawdown_window=5,
            drawdown_min=-0.15,
            rebound_days=3,
            volume_spike=False,
        )

        self.assertEqual(
            pattern(prices).tolist(),
            [False, False, False, False, False, False, False, False, False],
        )

    def test_panic_rebound_can_require_volume_spike(self):
        prices = np.array([100.0, 98.0, 95.0, 90.0, 82.0, 84.0, 87.0, 90.0, 89.0])
        volume = np.array([10.0, 10.0, 10.0, 10.0, 10.0, 10.0, 10.0, 25.0, 10.0])

        pattern = PanicRebound(name="panic").on(
            drawdown_window=5,
            drawdown_min=-0.15,
            rebound_days=3,
            volume_spike=True,
            volume_window=5,
            volume_threshold=1.5,
        )
        pattern._set_stock_values("volume", volume)

        self.assertEqual(
            pattern(prices).tolist(),
            [False, False, False, False, False, False, False, True, False],
        )

    def test_panic_rebound_skips_signal_without_volume_spike(self):
        prices = np.array([100.0, 98.0, 95.0, 90.0, 82.0, 84.0, 87.0, 90.0, 89.0])
        volume = np.array([10.0, 10.0, 10.0, 10.0, 10.0, 10.0, 10.0, 12.0, 10.0])

        pattern = PanicRebound(name="panic").on(
            drawdown_window=5,
            drawdown_min=-0.15,
            rebound_days=3,
            volume_spike=True,
            volume_window=5,
            volume_threshold=1.5,
        )
        pattern._set_stock_values("volume", volume)

        self.assertEqual(
            pattern(prices).tolist(),
            [False, False, False, False, False, False, False, False, False],
        )


if __name__ == "__main__":
    unittest.main()
