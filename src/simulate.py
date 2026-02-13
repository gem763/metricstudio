"""Simulation runtime for backtest runs."""

from __future__ import annotations

from dataclasses import dataclass, field

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

BUY_FEE = 0.0003
SELL_FEE = 0.0020


@dataclass
class Simulator:
    dates: np.ndarray
    prices: np.ndarray
    buy_fee: float = BUY_FEE
    sell_fee: float = SELL_FEE

    data: pd.DataFrame | None = field(default=None, init=False)
    pattern: str | None = field(default=None, init=False)
    target_horizon: str | None = field(default=None, init=False)
    target_horizon_days: int | None = field(default=None, init=False)
    aggregate_lookback: int | str | None = field(default=None, init=False)
    fallback_exposure: float | None = field(default=None, init=False)
    max_weight_per_stock: float | None = field(default=None, init=False)
    run_years: float | None = field(default=None, init=False)
    total_return: float | None = field(default=None, init=False)
    cagr: float | None = field(default=None, init=False)
    total_buy_fee_paid: float | None = field(default=None, init=False)
    total_sell_fee_paid: float | None = field(default=None, init=False)

    def _require_result(self) -> pd.DataFrame:
        if self.data is None:
            raise ValueError("Simulator.run()을 먼저 실행해야 합니다.")
        return self.data

    @property
    def total_fee_paid(self) -> float:
        if self.total_buy_fee_paid is None or self.total_sell_fee_paid is None:
            raise ValueError("Simulator.run()을 먼저 실행해야 합니다.")
        return self.total_buy_fee_paid + self.total_sell_fee_paid

    def to_frame(self, copy: bool = True) -> pd.DataFrame:
        out = self._require_result()
        return out.copy() if copy else out

    def summary(self) -> dict[str, float | str]:
        self._require_result()
        if (
            self.pattern is None
            or self.target_horizon is None
            or self.target_horizon_days is None
            or self.aggregate_lookback is None
            or self.fallback_exposure is None
            or self.max_weight_per_stock is None
            or self.run_years is None
            or self.total_return is None
            or self.cagr is None
            or self.total_buy_fee_paid is None
            or self.total_sell_fee_paid is None
        ):
            raise ValueError("Simulator 결과 메타데이터가 준비되지 않았습니다.")
        return {
            "pattern": self.pattern,
            "target_horizon": self.target_horizon,
            "target_horizon_days": float(self.target_horizon_days),
            "aggregate_lookback": str(self.aggregate_lookback),
            "fallback_exposure": float(self.fallback_exposure),
            "max_weight_per_stock": float(self.max_weight_per_stock),
            "buy_fee": float(self.buy_fee),
            "sell_fee": float(self.sell_fee),
            "run_years": float(self.run_years),
            "total_return": float(self.total_return),
            "cagr": float(self.cagr),
            "total_buy_fee_paid": float(self.total_buy_fee_paid),
            "total_sell_fee_paid": float(self.total_sell_fee_paid),
            "total_fee_paid": float(self.total_fee_paid),
        }

    def plot(self, figsize=(12, 4), return_handles: bool = False):
        out = self._require_result()
        if (
            self.cagr is None
            or self.pattern is None
            or self.target_horizon is None
            or self.aggregate_lookback is None
            or self.fallback_exposure is None
            or self.max_weight_per_stock is None
        ):
            raise ValueError("Simulator 결과 메타데이터가 준비되지 않았습니다.")
        if out.empty:
            raise ValueError("Simulator에 플롯할 데이터가 없습니다.")

        cagr_text = f"{self.cagr * 100.0:.2f}%" if np.isfinite(self.cagr) else "nan"
        spread = out["pattern_geom_mean"] - out["all_stock_geom_mean"]
        fig, axes = plt.subplots(1, 3, figsize=figsize, constrained_layout=True, sharex=True)

        axes[0].plot(out.index, spread, color="#D56062", linewidth=1.8)
        axes[0].axhline(0.0, color="gray", linewidth=0.8, linestyle="--")
        axes[0].set_title("Geometic mean spread")
        axes[0].grid(alpha=0.25, linestyle="--")

        axes[1].plot(out.index, out["selected_count"], color="#F37748", linewidth=1.8)
        axes[1].set_title("Portfolio count")
        axes[1].grid(alpha=0.25, linestyle="--")

        axes[2].plot(out.index, out["wealth"], color="#067BC2", linewidth=2.0)
        axes[2].set_title(f"Wealth (CAGR={cagr_text})")
        axes[2].grid(alpha=0.25, linestyle="--")

        fig.suptitle(
            f"pattern={self.pattern}, horizon={self.target_horizon}, "
            f"lookback={self.aggregate_lookback}, fallback_exposure={self.fallback_exposure:.2f}, "
            f"max_weight={self.max_weight_per_stock:.2%}, buy_fee={self.buy_fee:.4f}, "
            f"sell_fee={self.sell_fee:.4f}"
        )
        if return_handles:
            return fig, axes
        plt.show()
        return None

    def run(
        self,
        *,
        start_idx: int,
        end_idx: int,
        pattern: str,
        target_horizon: str,
        target_horizon_days: int,
        aggregate_lookback: int | str,
        pattern_mask: np.ndarray,
        pattern_geom_series: np.ndarray,
        all_stock_geom_series: np.ndarray,
        fallback_exposure: float = 0.5,
        max_weight_per_stock: float = 0.03,
    ) -> Simulator:
        horizon_days = int(target_horizon_days)
        if horizon_days <= 0:
            raise ValueError("target_horizon_days는 1 이상이어야 합니다.")
        if end_idx - start_idx < 2:
            raise ValueError("run 구간에 최소 2개 이상의 거래일이 필요합니다.")

        fallback_exposure_value = float(fallback_exposure)
        max_weight_per_stock_value = float(max_weight_per_stock)
        buy_fee_value = float(self.buy_fee)
        sell_fee_value = float(self.sell_fee)

        regime = np.full(len(self.dates), fallback_exposure_value, dtype=np.float64)
        regime_cond = (
            np.isfinite(pattern_geom_series)
            & np.isfinite(all_stock_geom_series)
            & (pattern_geom_series > all_stock_geom_series)
            & (pattern_geom_series > 0.0)
        )
        regime[regime_cond] = 1.0

        wealth = np.full(len(self.dates), np.nan, dtype=np.float64)
        exposure = np.full(len(self.dates), np.nan, dtype=np.float64)
        selected_count = np.full(len(self.dates), np.nan, dtype=np.float64)
        pattern_geom_out = np.full(len(self.dates), np.nan, dtype=np.float64)
        all_geom_out = np.full(len(self.dates), np.nan, dtype=np.float64)

        wealth[start_idx] = 1.0
        cash = 1.0
        active_buckets: list[dict[str, np.ndarray | int]] = []
        total_buy_fee_paid = 0.0
        total_sell_fee_paid = 0.0

        for t in range(start_idx, end_idx - 1):
            total_alloc = regime[t]
            # 신호일(t) 종가가 아니라 다음 거래일(t+1) 종가에 진입한다.
            can_open = t + horizon_days < (end_idx - 1)
            signal_mask = pattern_mask[t] if can_open else np.zeros(pattern_mask.shape[1], dtype=np.bool_)
            selected = np.where(signal_mask)[0]
            actual_selected = 0

            for bucket in active_buckets:
                idx = np.asarray(bucket["idx"], dtype=np.int64)
                vals = np.asarray(bucket["values"], dtype=np.float64)
                prev_close = self.prices[t, idx]
                next_close = self.prices[t + 1, idx]
                valid = (
                    np.isfinite(prev_close)
                    & np.isfinite(next_close)
                    & (prev_close > 0.0)
                    & (next_close > 0.0)
                )
                ratio = np.ones_like(vals, dtype=np.float64)
                ratio[valid] = next_close[valid] / prev_close[valid]
                vals *= ratio
                bucket["values"] = vals
                bucket["age"] = int(bucket["age"]) + 1

            next_active: list[dict[str, np.ndarray | int]] = []
            for bucket in active_buckets:
                if int(bucket["age"]) >= horizon_days:
                    gross_sell_value = float(np.asarray(bucket["values"], dtype=np.float64).sum())
                    sell_fee_paid = gross_sell_value * sell_fee_value
                    total_sell_fee_paid += sell_fee_paid
                    cash += gross_sell_value - sell_fee_paid
                else:
                    next_active.append(bucket)
            active_buckets = next_active

            if can_open and selected.size > 0:
                curr_wealth = cash
                stock_values = np.zeros(self.prices.shape[1], dtype=np.float64)
                for bucket in active_buckets:
                    idx = np.asarray(bucket["idx"], dtype=np.int64)
                    values = np.asarray(bucket["values"], dtype=np.float64)
                    curr_wealth += float(values.sum())
                    if idx.size > 0:
                        np.add.at(stock_values, idx, values)

                target_alloc = curr_wealth * (total_alloc / horizon_days)
                invest_amount = min(float(target_alloc), float(cash))
                if invest_amount > 0.0:
                    cap_value = curr_wealth * max_weight_per_stock_value
                    remain_capacity = cap_value - stock_values[selected]
                    remain_capacity = np.where(np.isfinite(remain_capacity), remain_capacity, 0.0)
                    remain_capacity = np.maximum(remain_capacity, 0.0)
                    eligible = remain_capacity > 0.0
                    if np.any(eligible):
                        eligible_idx = selected[eligible]
                        eligible_cap = remain_capacity[eligible]

                        gross_budget = invest_amount
                        net_budget = gross_budget / (1.0 + buy_fee_value)
                        equal_net = net_budget / eligible_idx.size
                        alloc_net = np.minimum(eligible_cap, equal_net)
                        positive_alloc = alloc_net > 0.0

                        if np.any(positive_alloc):
                            buy_idx = eligible_idx[positive_alloc].astype(np.int64, copy=False)
                            buy_values = alloc_net[positive_alloc].astype(np.float64, copy=False)
                            invested_net = float(buy_values.sum())
                            buy_fee_paid = invested_net * buy_fee_value
                            gross_spend = invested_net + buy_fee_paid
                            total_buy_fee_paid += buy_fee_paid
                            cash -= gross_spend

                            bucket = {
                                "idx": buy_idx,
                                "values": buy_values,
                                "age": 0,
                            }
                            active_buckets.append(bucket)
                            actual_selected = int(buy_idx.size)

            next_wealth = cash
            for bucket in active_buckets:
                next_wealth += float(np.asarray(bucket["values"], dtype=np.float64).sum())

            wealth[t + 1] = next_wealth
            exposure[t] = total_alloc
            selected_count[t] = float(actual_selected)
            pattern_geom_out[t] = pattern_geom_series[t]
            all_geom_out[t] = all_stock_geom_series[t]

        final_wealth = cash
        for bucket in active_buckets:
            final_wealth += float(np.asarray(bucket["values"], dtype=np.float64).sum())
        wealth[end_idx - 1] = final_wealth
        exposure[end_idx - 1] = regime[end_idx - 1]
        selected_count[end_idx - 1] = 0.0
        pattern_geom_out[end_idx - 1] = pattern_geom_series[end_idx - 1]
        all_geom_out[end_idx - 1] = all_stock_geom_series[end_idx - 1]

        out_index = pd.DatetimeIndex(self.dates[start_idx:end_idx])
        out = pd.DataFrame(
            {
                "wealth": wealth[start_idx:end_idx],
                "exposure": exposure[start_idx:end_idx],
                "selected_count": selected_count[start_idx:end_idx],
                "pattern_geom_mean": pattern_geom_out[start_idx:end_idx],
                "all_stock_geom_mean": all_geom_out[start_idx:end_idx],
            },
            index=out_index,
        )
        out.index.name = "date"

        start_wealth = float(out["wealth"].iloc[0]) if len(out) > 0 else float("nan")
        end_wealth = float(out["wealth"].iloc[-1]) if len(out) > 0 else float("nan")
        total_return = float("nan")
        if (
            np.isfinite(start_wealth)
            and np.isfinite(end_wealth)
            and start_wealth > 0.0
            and end_wealth > 0.0
        ):
            total_return = end_wealth / start_wealth - 1.0

        years = float("nan")
        if len(out.index) >= 2:
            elapsed_days = (out.index[-1] - out.index[0]).days
            if elapsed_days > 0:
                years = float(elapsed_days) / 365.25

        cagr = float("nan")
        if (
            np.isfinite(total_return)
            and np.isfinite(years)
            and years > 0.0
            and (1.0 + total_return) > 0.0
        ):
            cagr = (1.0 + total_return) ** (1.0 / years) - 1.0

        out.attrs["cagr"] = cagr
        out.attrs["total_return"] = total_return
        out.attrs["run_years"] = years
        out.attrs["pattern"] = pattern
        out.attrs["target_horizon"] = target_horizon
        out.attrs["target_horizon_days"] = horizon_days
        out.attrs["aggregate_lookback"] = str(aggregate_lookback)
        out.attrs["fallback_exposure"] = fallback_exposure_value
        out.attrs["max_weight_per_stock"] = max_weight_per_stock_value
        out.attrs["buy_fee"] = buy_fee_value
        out.attrs["sell_fee"] = sell_fee_value
        out.attrs["total_buy_fee_paid"] = total_buy_fee_paid
        out.attrs["total_sell_fee_paid"] = total_sell_fee_paid
        out.attrs["total_fee_paid"] = total_buy_fee_paid + total_sell_fee_paid

        self.data = out
        self.pattern = pattern
        self.target_horizon = target_horizon
        self.target_horizon_days = horizon_days
        self.aggregate_lookback = aggregate_lookback
        self.fallback_exposure = fallback_exposure_value
        self.max_weight_per_stock = max_weight_per_stock_value
        self.run_years = years
        self.total_return = total_return
        self.cagr = cagr
        self.total_buy_fee_paid = total_buy_fee_paid
        self.total_sell_fee_paid = total_sell_fee_paid
        return self
