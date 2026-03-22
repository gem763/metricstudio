from __future__ import annotations

import unittest

import numpy as np
import pandas as pd

from metricstudio.filter import Filter
from metricstudio.univ import Univ


class _FakeDataLoader:
    def __init__(self, tables: dict[str, pd.DataFrame]):
        self.tables = {str(key): value.copy() for key, value in tables.items()}

    def load_stock_field_table(self, field: str, univ) -> pd.DataFrame:
        return self.tables[str(field)].copy()

    def load_stock_field_tables(self, fields, univ) -> dict[str, pd.DataFrame]:
        return {str(field): self.load_stock_field_table(str(field), univ) for field in fields}


class FilterTests(unittest.TestCase):
    def test_filter_is_active_with_market_cap_min(self):
        flt = Filter(market_cap_min=100.0)

        self.assertTrue(flt.is_active)

    def test_filter_market_cap_min_masks_small_caps(self):
        dates = pd.date_range("2025-01-01", periods=2, freq="B").to_numpy()
        codes = ["A", "B", "C"]
        prices = np.ones((2, 3), dtype=np.float64)
        marketcap = pd.DataFrame(
            [[50.0, 100.0, 150.0], [80.0, 90.0, 110.0]],
            index=pd.DatetimeIndex(dates),
            columns=codes,
        )
        loader = _FakeDataLoader({"marketcap": marketcap})

        flt = Filter(market_cap_min=100.0)
        flt.bind(
            dates=dates,
            codes=codes,
            prices=prices,
            data_loader=loader,
            univ=Univ(),
        )

        mask = flt.mask_matrix()

        self.assertEqual(mask.tolist(), [[False, True, True], [False, False, True]])
        self.assertEqual(flt.get(dates[0]), ["B", "C"])

    def test_filter_market_cap_min_applies_before_market_cap_deciles(self):
        dates = pd.date_range("2025-01-01", periods=1, freq="B").to_numpy()
        codes = ["A", "B", "C", "D"]
        prices = np.ones((1, 4), dtype=np.float64)
        marketcap = pd.DataFrame(
            [[80.0, 100.0, 200.0, 400.0]],
            index=pd.DatetimeIndex(dates),
            columns=codes,
        )
        loader = _FakeDataLoader({"marketcap": marketcap})

        flt = Filter(market_cap=[5, 6, 7, 8, 9, 10], market_cap_min=100.0)
        flt.bind(
            dates=dates,
            codes=codes,
            prices=prices,
            data_loader=loader,
            univ=Univ(),
        )

        mask = flt.mask_matrix()

        self.assertEqual(mask.tolist(), [[False, False, False, True]])


if __name__ == "__main__":
    unittest.main()
