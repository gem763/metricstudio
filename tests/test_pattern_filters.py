from __future__ import annotations

import numpy as np
import unittest

from src.pattern import AmountSurge, RelativeStrength, RetestBreakout, PanicRebound


class PatternFilterTests(unittest.TestCase):
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
