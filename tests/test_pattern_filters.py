from __future__ import annotations

import numpy as np
import unittest

from src.pattern import AmountSurge, RelativeStrength


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


if __name__ == "__main__":
    unittest.main()
