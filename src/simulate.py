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
    """
    패턴 신호를 코호트 포트폴리오로 변환해 자산곡선을 계산한다.
    """

    dates: np.ndarray
    prices: np.ndarray
    codes: list[str] | None = None
    code_names: dict[str, str] | None = None
    buy_fee: float = BUY_FEE
    sell_fee: float = SELL_FEE

    data: pd.DataFrame | None = field(default=None, init=False)
    pattern: str | None = field(default=None, init=False)
    target_horizon: str | None = field(default=None, init=False)
    target_horizon_days: int | None = field(default=None, init=False)
    aggregate_lookback: int | str | None = field(default=None, init=False)
    fallback_exposure: float | None = field(default=None, init=False)
    stop_loss_pct: float | None = field(default=None, init=False)
    take_profit_pct: float | None = field(default=None, init=False)
    min_marketcap: float | None = field(default=None, init=False)
    marketcap_top_pct: float | None = field(default=None, init=False)
    execution_lag_days: int | None = field(default=None, init=False)
    execution_price_mode: str | None = field(default=None, init=False)
    max_weight_per_stock: float | None = field(default=None, init=False)
    cohort_top_n: int | None = field(default=None, init=False)
    top_n_type: str | None = field(default=None, init=False)
    run_years: float | None = field(default=None, init=False)
    total_return: float | None = field(default=None, init=False)
    cagr: float | None = field(default=None, init=False)
    total_buy_fee_paid: float | None = field(default=None, init=False)
    total_sell_fee_paid: float | None = field(default=None, init=False)
    _portfolio_snapshots: dict[int, list[dict[str, np.ndarray | int]]] | None = field(
        default=None,
        init=False,
    )

    def _require_result(self) -> pd.DataFrame:
        """
        시뮬레이션 결과 DataFrame이 준비되었는지 확인한다.
        """

        if self.data is None:
            raise ValueError("Simulator.run()을 먼저 실행해야 합니다.")
        return self.data

    @property
    def total_fee_paid(self) -> float:
        """
        누적 매수/매도 수수료 합계를 반환한다.
        """
        self._require_result()
        return float(self.total_buy_fee_paid) + float(self.total_sell_fee_paid)

    def to_frame(self, copy: bool = True) -> pd.DataFrame:
        """
        내부 결과 테이블을 반환한다.
        """

        out = self._require_result()
        return out.copy() if copy else out

    @staticmethod
    def _clone_active_buckets(
        buckets: list[dict[str, np.ndarray | int]],
    ) -> list[dict[str, np.ndarray | int]]:
        """
        활성 코호트 버킷 상태를 스냅샷 용도로 깊은 복사한다.
        """

        copied: list[dict[str, np.ndarray | int]] = []
        for bucket in buckets:
            copied.append(
                {
                    "idx": np.asarray(bucket["idx"], dtype=np.int64).copy(),
                    "values": np.asarray(bucket["values"], dtype=np.float64).copy(),
                    "entry_values": np.asarray(bucket["entry_values"], dtype=np.float64).copy(),
                    "age": int(bucket["age"]),
                    "entry_idx": int(bucket["entry_idx"]),
                    "cohort_id": int(bucket["cohort_id"]),
                }
            )
        return copied

    @staticmethod
    def _normalize_stop_loss_pct(stop_loss_pct: float | None) -> float | None:
        """
        stop_loss 입력을 소수 비율(예: 0.1=10%)로 정규화한다.
        """

        if stop_loss_pct is None:
            return None
        value = float(stop_loss_pct)
        if not np.isfinite(value) or value <= 0.0:
            raise ValueError("stop_loss_pct는 양수여야 합니다.")
        if value >= 1.0:
            value = value / 100.0
        if value <= 0.0 or value >= 1.0:
            raise ValueError("stop_loss_pct는 0~1(소수) 또는 1~100(%) 범위여야 합니다.")
        return value

    @staticmethod
    def _normalize_take_profit_pct(take_profit_pct: float | None) -> float | None:
        """
        take_profit 입력을 소수 비율(예: 0.1=10%)로 정규화한다.
        """

        if take_profit_pct is None:
            return None
        value = float(take_profit_pct)
        if not np.isfinite(value) or value <= 0.0:
            raise ValueError("take_profit_pct는 양수여야 합니다.")
        if value >= 1.0:
            value = value / 100.0
        if value <= 0.0 or value >= 1.0:
            raise ValueError("take_profit_pct는 0~1(소수) 또는 1~100(%) 범위여야 합니다.")
        return value

    @staticmethod
    def _normalize_min_marketcap(min_marketcap: float | None) -> float | None:
        """
        시가총액 하한을 정규화한다.
        """

        if min_marketcap is None:
            return None
        value = float(min_marketcap)
        if not np.isfinite(value) or value <= 0.0:
            raise ValueError("min_marketcap은 양수여야 합니다.")
        return value

    @staticmethod
    def _normalize_marketcap_top_pct(marketcap_top_pct: float | None) -> float | None:
        """
        시가총액 상위 비율을 소수 비율(예: 0.2=상위 20%)로 정규화한다.
        """

        if marketcap_top_pct is None:
            return None
        value = float(marketcap_top_pct)
        if not np.isfinite(value) or value <= 0.0:
            raise ValueError("marketcap_top_pct는 양수여야 합니다.")
        if value > 1.0:
            value = value / 100.0
        if value <= 0.0 or value > 1.0:
            raise ValueError("marketcap_top_pct는 0~1(소수) 또는 1~100(%) 범위여야 합니다.")
        return value

    def port_at(self, date) -> pd.DataFrame:
        """
        지정 날짜의 활성 코호트를 종목 단위로 펼쳐 반환한다.
        """

        self._require_result()
        if self._portfolio_snapshots is None:
            raise ValueError("포트폴리오 스냅샷이 없습니다. Simulator.run()을 다시 실행하세요.")

        date_ts = pd.Timestamp(date)
        target = date_ts.to_datetime64()
        idx = int(np.searchsorted(self.dates, target, side="left"))
        if idx >= len(self.dates) or self.dates[idx] != target:
            prev_text = str(pd.Timestamp(self.dates[idx - 1]).date()) if idx > 0 else None
            next_text = str(pd.Timestamp(self.dates[idx]).date()) if idx < len(self.dates) else None
            raise ValueError(
                f"요청한 날짜({date_ts.date()})는 거래일 데이터에 없습니다. "
                f"이전 거래일={prev_text}, 다음 거래일={next_text}"
            )

        if pd.Timestamp(target) not in self.data.index:
            start_date = str(self.data.index[0].date())
            end_date = str(self.data.index[-1].date())
            raise ValueError(f"date는 run 구간 내 거래일이어야 합니다: {start_date} ~ {end_date}")

        snapshot = self._portfolio_snapshots.get(idx, [])
        rows: list[dict[str, object]] = []
        for bucket in snapshot:
            stock_idx = np.asarray(bucket["idx"], dtype=np.int64)
            stock_vals = np.asarray(bucket["values"], dtype=np.float64)
            stock_entry_vals = np.asarray(bucket["entry_values"], dtype=np.float64)
            cohort_value = float(stock_vals.sum())
            entry_idx = int(bucket["entry_idx"])
            entry_date = pd.Timestamp(self.dates[entry_idx]).date()
            age = int(bucket["age"])
            cohort_id = int(bucket["cohort_id"])
            for i, code_idx in enumerate(stock_idx):
                if self.codes is not None and 0 <= int(code_idx) < len(self.codes):
                    code = str(self.codes[int(code_idx)])
                else:
                    code = str(int(code_idx))
                name = code
                if self.code_names is not None:
                    mapped = self.code_names.get(code)
                    if mapped is not None and str(mapped).strip():
                        name = str(mapped).strip()
                val = float(stock_vals[i])
                entry_val = float(stock_entry_vals[i])
                rows.append(
                    {
                        "cohort_id": cohort_id,
                        "entry_date": entry_date,
                        "age": age,
                        "code": code,
                        "name": name,
                        "value": val,
                        "entry_value": entry_val,
                        "cohort_value": cohort_value,
                        "weight_in_cohort": (val / cohort_value) if cohort_value > 0.0 else np.nan,
                    }
                )

        cols = [
            "cohort_id",
            "entry_date",
            "age",
            "code",
            "name",
            "value",
            "entry_value",
            "cohort_value",
            "weight_in_cohort",
        ]
        if not rows:
            empty = pd.DataFrame(columns=cols)
            return empty.set_index(["cohort_id", "entry_date", "age"])

        out = pd.DataFrame(rows, columns=cols)
        out = out.sort_values(
            ["cohort_id", "entry_date", "age", "value"],
            ascending=[True, True, True, False],
            kind="stable",
        )
        out = out.set_index(["cohort_id", "entry_date", "age"])
        return out

    def summary(self) -> dict[str, float | str]:
        """
        시뮬레이션 메타데이터와 성과 요약을 dict로 반환한다.
        """

        self._require_result()
        return {
            "pattern": str(self.pattern),
            "target_horizon": str(self.target_horizon),
            "target_horizon_days": float(self.target_horizon_days),
            "aggregate_lookback": str(self.aggregate_lookback),
            "fallback_exposure": float(self.fallback_exposure),
            "stop_loss_pct": float(self.stop_loss_pct) if self.stop_loss_pct is not None else float("nan"),
            "take_profit_pct": float(self.take_profit_pct)
            if self.take_profit_pct is not None
            else float("nan"),
            "min_marketcap": float(self.min_marketcap) if self.min_marketcap is not None else float("nan"),
            "marketcap_top_pct": float(self.marketcap_top_pct)
            if self.marketcap_top_pct is not None
            else float("nan"),
            "execution_lag_days": float(self.execution_lag_days)
            if self.execution_lag_days is not None
            else float("nan"),
            "execution_price_mode": str(self.execution_price_mode)
            if self.execution_price_mode is not None
            else "none",
            "max_weight_per_stock": float(self.max_weight_per_stock),
            "cohort_top_n": float(self.cohort_top_n)
            if self.cohort_top_n is not None
            else float("nan"),
            "top_n_type": str(self.top_n_type) if self.top_n_type is not None else "none",
            "buy_fee": float(self.buy_fee),
            "sell_fee": float(self.sell_fee),
            "run_years": float(self.run_years),
            "total_return": float(self.total_return),
            "cagr": float(self.cagr),
            "total_buy_fee_paid": float(self.total_buy_fee_paid),
            "total_sell_fee_paid": float(self.total_sell_fee_paid),
            "total_fee_paid": float(self.total_fee_paid),
        }

    def plot(self, figsize=(12, 5), return_handles: bool = False):
        """
        스프레드/보유종목수/자산곡선을 3개 패널로 시각화한다.
        """

        out = self._require_result()
        if out.empty:
            raise ValueError("Simulator에 플롯할 데이터가 없습니다.")

        meta = self.summary()
        cagr = float(meta["cagr"])
        cagr_text = f"{cagr * 100.0:.2f}%" if np.isfinite(cagr) else "nan"
        wealth_vals = out["wealth"].to_numpy(dtype=float)
        daily_ret = wealth_vals[1:] / wealth_vals[:-1] - 1.0
        daily_ret = daily_ret[np.isfinite(daily_ret)]
        ann_vol = (
            float(np.std(daily_ret, ddof=1) * np.sqrt(252.0))
            if daily_ret.size >= 2
            else float("nan")
        )
        ann_vol_text = f"{ann_vol * 100.0:.2f}%" if np.isfinite(ann_vol) else "nan"
        ir = cagr / ann_vol if np.isfinite(cagr) and np.isfinite(ann_vol) and ann_vol > 0.0 else float("nan")
        ir_text = f"{ir:.2f}" if np.isfinite(ir) else "nan"
        spread = out["pattern_geom_mean"] - out["all_stock_geom_mean"]
        fig, axes = plt.subplots(1, 3, figsize=figsize, constrained_layout=False, sharex=True)
        for ax in axes:
            ax.set_box_aspect(1.0)

        axes[0].plot(out.index, spread, color="#D56062", linewidth=1.8)
        axes[0].axhline(0.0, color="gray", linewidth=0.8, linestyle="--")
        axes[0].set_title("Geometic mean spread")
        axes[0].grid(alpha=0.25, linestyle="--")

        axes[1].plot(
            out.index,
            out["selected_count"],
            color="#F37748",
            linewidth=1.8,
            label="New cohort count",
        )
        if "active_count" in out.columns:
            axes[1].plot(
                out.index,
                out["active_count"],
                color="#067BC2",
                linewidth=1.6,
                alpha=0.9,
                label="Total active count",
            )
            axes[1].legend(loc="upper left", fontsize=9)
        axes[1].set_title("Portfolio count")
        axes[1].grid(alpha=0.25, linestyle="--")

        axes[2].plot(out.index, out["wealth"], color="#067BC2", linewidth=2.0)
        axes[2].set_yscale("log")
        axes[2].set_title("Wealth (Log Scale)")
        axes[2].grid(alpha=0.25, linestyle="--")
        axes[2].text(
            0.02,
            0.98,
            f"CAGR: {cagr_text}\n연변동성: {ann_vol_text}\nIR: {ir_text}",
            transform=axes[2].transAxes,
            ha="left",
            va="top",
            fontsize=10,
            bbox={"facecolor": "white", "alpha": 0.8, "edgecolor": "none"},
        )

        fig.subplots_adjust(top=0.92)
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
        pattern_arith_series: np.ndarray,
        pattern_geom_series: np.ndarray,
        pattern_rise_series: np.ndarray,
        all_stock_arith_series: np.ndarray,
        all_stock_geom_series: np.ndarray,
        all_stock_rise_series: np.ndarray,
        fallback_exposure: float = 0.5,
        top_n_values: np.ndarray | None = None,
        cohort_top_n: int | None = None,
        top_n_type: str = "marketcap",
        stop_loss_pct: float | None = None,
        take_profit_pct: float | None = None,
        marketcap_values: np.ndarray | None = None,
        min_marketcap: float | None = None,
        marketcap_top_pct: float | None = None,
        execution_lag_days: int = 1,
        execution_price_mode: str = "next_vwap",
    ) -> Simulator:
        """
        코호트별 fallback을 적용한 포트폴리오 시뮬레이션.

        - 기본 코호트 크기: 전체자산의 1/horizon
        - 진입일 신호 기준으로 코호트 단위만 100% 또는 fallback_exposure 배정
        - 진입 조건: 기하/산술/상승확률 3조건 동시 만족(AND)
        - 종목별 비중 상한은 적용하지 않음(동등비중)
        """

        horizon_days = int(target_horizon_days)
        if horizon_days <= 0:
            raise ValueError("target_horizon_days는 1 이상이어야 합니다.")
        if end_idx - start_idx < 2:
            raise ValueError("run 구간에 최소 2개 이상의 거래일이 필요합니다.")
        lag_days = int(execution_lag_days)
        if lag_days not in {0, 1}:
            raise ValueError("execution_lag_days는 0(당일) 또는 1(익일)만 지원합니다.")

        fallback_exposure_value = float(fallback_exposure)
        stop_loss_value = self._normalize_stop_loss_pct(stop_loss_pct)
        take_profit_value = self._normalize_take_profit_pct(take_profit_pct)
        min_marketcap_value = self._normalize_min_marketcap(min_marketcap)
        marketcap_top_pct_value = self._normalize_marketcap_top_pct(marketcap_top_pct)
        top_n = None if cohort_top_n is None else int(cohort_top_n)
        if top_n is not None and top_n <= 0:
            raise ValueError("cohort_top_n은 1 이상의 정수이거나 None이어야 합니다.")
        top_n_type_value = str(top_n_type).strip().lower()
        if top_n_type_value not in {"marketcap", "liquidity", "marketcap+liquidity"}:
            raise ValueError(
                "top_n_type은 'marketcap', 'liquidity', 'marketcap+liquidity'만 지원합니다."
            )
        buy_fee_value = float(self.buy_fee)
        sell_fee_value = float(self.sell_fee)
        cohort_weight = 1.0 / float(horizon_days)

        marketcap_top_threshold: np.ndarray | None = None
        if marketcap_top_pct_value is not None:
            if marketcap_values is None:
                raise ValueError("marketcap_top_pct 사용 시 marketcap_values 배열이 필요합니다.")
            q = 1.0 - marketcap_top_pct_value
            marketcap_top_threshold = np.full(len(self.dates), np.nan, dtype=np.float64)
            for i in range(len(self.dates)):
                row = marketcap_values[i]
                valid = row[np.isfinite(row)]
                if valid.size > 0:
                    marketcap_top_threshold[i] = float(np.quantile(valid, q))

        wealth = np.full(len(self.dates), np.nan, dtype=np.float64)
        exposure = np.full(len(self.dates), np.nan, dtype=np.float64)
        selected_count = np.full(len(self.dates), np.nan, dtype=np.float64)
        active_count = np.full(len(self.dates), np.nan, dtype=np.float64)
        pattern_geom_out = np.full(len(self.dates), np.nan, dtype=np.float64)
        all_geom_out = np.full(len(self.dates), np.nan, dtype=np.float64)

        wealth[start_idx] = 1.0
        cash = 1.0
        active_buckets: list[dict[str, np.ndarray | int]] = []
        snapshots: dict[int, list[dict[str, np.ndarray | int]]] = {start_idx: []}
        total_buy_fee_paid = 0.0
        total_sell_fee_paid = 0.0
        next_cohort_id = 1

        for t in range(start_idx, end_idx - 1):
            signal_idx = t if lag_days == 1 else (t + 1)
            signal_mask = pattern_mask[signal_idx]
            selected = np.where(signal_mask)[0]
            if min_marketcap_value is not None and selected.size > 0:
                if marketcap_values is None:
                    raise ValueError("min_marketcap 사용 시 marketcap_values 배열이 필요합니다.")
                mcap_row = marketcap_values[signal_idx, selected]
                selected = selected[np.isfinite(mcap_row) & (mcap_row >= min_marketcap_value)]
            if marketcap_top_threshold is not None and selected.size > 0:
                threshold = marketcap_top_threshold[signal_idx]
                if np.isfinite(threshold):
                    mcap_row = marketcap_values[signal_idx, selected]
                    selected = selected[np.isfinite(mcap_row) & (mcap_row >= threshold)]
                else:
                    selected = selected[:0]
            if top_n is not None and selected.size > top_n:
                if top_n_values is None:
                    raise ValueError("cohort_top_n 사용 시 top_n_values 배열이 필요합니다.")
                ranking_row = top_n_values[signal_idx, selected]
                # NaN 랭킹값은 우선순위를 낮춰 가능한 경우 유효값 종목부터 선택한다.
                ranking = np.where(np.isfinite(ranking_row), ranking_row, -np.inf)
                order = np.argsort(ranking)[::-1]
                selected = selected[order[:top_n]]
            actual_selected = 0

            # lag=1: t 기준 판정 -> t+1 체결, lag=0: t+1 기준 판정 -> t+1 체결
            if (stop_loss_value is not None or take_profit_value is not None) and lag_days == 1:
                for bucket in active_buckets:
                    vals_t = np.asarray(bucket["values"], dtype=np.float64)
                    entry_vals = np.asarray(bucket["entry_values"], dtype=np.float64)
                    hit = np.zeros(vals_t.shape, dtype=np.bool_)
                    valid = np.isfinite(vals_t) & np.isfinite(entry_vals) & (entry_vals > 0.0)
                    if stop_loss_value is not None:
                        hit |= valid & (vals_t <= entry_vals * (1.0 - stop_loss_value))
                    if take_profit_value is not None:
                        hit |= valid & (vals_t >= entry_vals * (1.0 + take_profit_value))
                    bucket["exit_next_mask"] = hit

            # 1) 기존 버킷을 하루 전진(mark-to-market)
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

            if (stop_loss_value is not None or take_profit_value is not None) and lag_days == 0:
                for bucket in active_buckets:
                    vals_t = np.asarray(bucket["values"], dtype=np.float64)
                    entry_vals = np.asarray(bucket["entry_values"], dtype=np.float64)
                    hit = np.zeros(vals_t.shape, dtype=np.bool_)
                    valid = np.isfinite(vals_t) & np.isfinite(entry_vals) & (entry_vals > 0.0)
                    if stop_loss_value is not None:
                        hit |= valid & (vals_t <= entry_vals * (1.0 - stop_loss_value))
                    if take_profit_value is not None:
                        hit |= valid & (vals_t >= entry_vals * (1.0 + take_profit_value))
                    bucket["exit_next_mask"] = hit

            # 2) 보유기간(horizon) 만료 버킷 청산
            next_active: list[dict[str, np.ndarray | int]] = []
            for bucket in active_buckets:
                age = int(bucket["age"])
                idx = np.asarray(bucket["idx"], dtype=np.int64)
                vals = np.asarray(bucket["values"], dtype=np.float64)
                entry_vals = np.asarray(bucket["entry_values"], dtype=np.float64)
                exit_mask = np.asarray(
                    bucket.get("exit_next_mask", np.zeros(idx.size, dtype=np.bool_)),
                    dtype=np.bool_,
                )
                if exit_mask.shape[0] != idx.size:
                    exit_mask = np.zeros(idx.size, dtype=np.bool_)

                if age >= horizon_days:
                    gross_sell_value = float(vals.sum())
                    sell_fee_paid = gross_sell_value * sell_fee_value
                    total_sell_fee_paid += sell_fee_paid
                    cash += gross_sell_value - sell_fee_paid
                    continue

                if np.any(exit_mask):
                    sell_value = float(vals[exit_mask].sum())
                    if sell_value > 0.0:
                        sell_fee_paid = sell_value * sell_fee_value
                        total_sell_fee_paid += sell_fee_paid
                        cash += sell_value - sell_fee_paid

                    keep = ~exit_mask
                    if np.any(keep):
                        bucket["idx"] = idx[keep]
                        bucket["values"] = vals[keep]
                        bucket["entry_values"] = entry_vals[keep]
                        bucket.pop("exit_next_mask", None)
                        next_active.append(bucket)
                    continue

                bucket.pop("exit_next_mask", None)
                next_active.append(bucket)
            active_buckets = next_active

            # 3) 신규 코호트 진입(신호일 t에서 코호트별 fallback 여부 결정)
            if selected.size > 0:
                curr_wealth = cash
                for bucket in active_buckets:
                    curr_wealth += float(np.asarray(bucket["values"], dtype=np.float64).sum())

                pattern_arith = pattern_arith_series[signal_idx]
                market_arith = all_stock_arith_series[signal_idx]
                pattern_geom = pattern_geom_series[signal_idx]
                market_geom = all_stock_geom_series[signal_idx]
                pattern_rise = pattern_rise_series[signal_idx]
                market_rise = all_stock_rise_series[signal_idx]
                full_cohort = (
                    np.isfinite(pattern_arith)
                    and np.isfinite(market_arith)
                    and np.isfinite(pattern_geom)
                    and np.isfinite(market_geom)
                    and np.isfinite(pattern_rise)
                    and np.isfinite(market_rise)
                    and (pattern_geom > max(0.0, market_geom))
                    and (pattern_arith > max(0.0, market_arith))
                    and (pattern_rise > max(0.5, market_rise))
                )
                cohort_scale = 1.0 if full_cohort else fallback_exposure_value

                target_gross = curr_wealth * cohort_weight * cohort_scale
                invest_amount = min(float(target_gross), float(cash))
                if invest_amount > 0.0:
                    net_budget = invest_amount / (1.0 + buy_fee_value)
                    per_stock_value = net_budget / float(selected.size)
                    if per_stock_value > 0.0:
                        buy_idx = selected.astype(np.int64, copy=False)
                        buy_values = np.full(buy_idx.size, per_stock_value, dtype=np.float64)
                        invested_net = float(buy_values.sum())
                        buy_fee_paid = invested_net * buy_fee_value
                        gross_spend = invested_net + buy_fee_paid
                        total_buy_fee_paid += buy_fee_paid
                        cash -= gross_spend
                        active_buckets.append(
                            {
                                "idx": buy_idx,
                                "values": buy_values,
                                "entry_values": buy_values.copy(),
                                "age": 0,
                                "entry_idx": t + 1,
                                "cohort_id": next_cohort_id,
                            }
                        )
                        next_cohort_id += 1
                        actual_selected = int(buy_idx.size)

            # 4) 다음 날짜 기준 자산/메타 시계열 기록
            invested_value = 0.0
            total_active = 0
            for bucket in active_buckets:
                invested_value += float(np.asarray(bucket["values"], dtype=np.float64).sum())
                total_active += int(np.asarray(bucket["idx"], dtype=np.int64).size)
            next_wealth = cash + invested_value
            wealth[t + 1] = next_wealth
            exposure[t] = invested_value / next_wealth if next_wealth > 0.0 else np.nan
            selected_count[t] = float(actual_selected)
            active_count[t] = float(total_active)
            pattern_geom_out[t] = pattern_geom_series[signal_idx]
            all_geom_out[t] = all_stock_geom_series[signal_idx]
            snapshots[t + 1] = self._clone_active_buckets(active_buckets)

        final_invested = 0.0
        final_active = 0
        for bucket in active_buckets:
            final_invested += float(np.asarray(bucket["values"], dtype=np.float64).sum())
            final_active += int(np.asarray(bucket["idx"], dtype=np.int64).size)
        final_wealth = cash + final_invested
        wealth[end_idx - 1] = final_wealth
        exposure[end_idx - 1] = final_invested / final_wealth if final_wealth > 0.0 else np.nan
        selected_count[end_idx - 1] = 0.0
        active_count[end_idx - 1] = float(final_active)
        pattern_geom_out[end_idx - 1] = pattern_geom_series[end_idx - 1]
        all_geom_out[end_idx - 1] = all_stock_geom_series[end_idx - 1]

        out_index = pd.DatetimeIndex(self.dates[start_idx:end_idx])
        out = pd.DataFrame(
            {
                "wealth": wealth[start_idx:end_idx],
                "exposure": exposure[start_idx:end_idx],
                "selected_count": selected_count[start_idx:end_idx],
                "active_count": active_count[start_idx:end_idx],
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
        out.attrs["stop_loss_pct"] = stop_loss_value if stop_loss_value is not None else float("nan")
        out.attrs["take_profit_pct"] = (
            take_profit_value if take_profit_value is not None else float("nan")
        )
        out.attrs["min_marketcap"] = min_marketcap_value if min_marketcap_value is not None else float("nan")
        out.attrs["marketcap_top_pct"] = (
            marketcap_top_pct_value if marketcap_top_pct_value is not None else float("nan")
        )
        out.attrs["execution_lag_days"] = float(lag_days)
        out.attrs["execution_price_mode"] = str(execution_price_mode)
        out.attrs["max_weight_per_stock"] = float("nan")
        out.attrs["cohort_top_n"] = float(top_n) if top_n is not None else float("nan")
        out.attrs["top_n_type"] = top_n_type_value if top_n is not None else "none"
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
        self.stop_loss_pct = stop_loss_value
        self.take_profit_pct = take_profit_value
        self.min_marketcap = min_marketcap_value
        self.marketcap_top_pct = marketcap_top_pct_value
        self.execution_lag_days = lag_days
        self.execution_price_mode = str(execution_price_mode)
        self.max_weight_per_stock = float("nan")
        self.cohort_top_n = top_n
        self.top_n_type = top_n_type_value if top_n is not None else "none"
        self.run_years = years
        self.total_return = total_return
        self.cagr = cagr
        self.total_buy_fee_paid = total_buy_fee_paid
        self.total_sell_fee_paid = total_sell_fee_paid
        self._portfolio_snapshots = snapshots
        return self
