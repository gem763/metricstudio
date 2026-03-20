"""Simulation runtime for backtest runs."""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd
from metricstudio._progress import progress as _progress
from metricstudio.plot import plot_simulator

TRADING_DAYS_PER_YEAR = 240

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
    stop_loss_pct: float | None = field(default=None, init=False)
    take_profit_pct: float | None = field(default=None, init=False)
    execution_lag_days: int | None = field(default=None, init=False)
    execution_price_mode: str | None = field(default=None, init=False)
    max_weight_per_stock: float | None = field(default=None, init=False)
    allow_reentry: bool | None = field(default=None, init=False)
    min_cohort_size: int | None = field(default=None, init=False)
    max_cohort_size: int | None = field(default=None, init=False)
    run_years: float | None = field(default=None, init=False)
    total_return: float | None = field(default=None, init=False)
    cagr: float | None = field(default=None, init=False)
    max_drawdown: float | None = field(default=None, init=False)
    cohort_win_rate: float | None = field(default=None, init=False)
    cohort_payoff_ratio: float | None = field(default=None, init=False)
    active_day_ratio: float | None = field(default=None, init=False)
    mean_turnover: float | None = field(default=None, init=False)
    annual_turnover: float | None = field(default=None, init=False)
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
                    "signal_entry_idx": int(bucket.get("signal_entry_idx", bucket["entry_idx"])),
                    "cohort_id": int(bucket["cohort_id"]),
                    "horizon_days": int(bucket.get("horizon_days", 0)),
                    "stop_loss_pct": float(bucket["stop_loss_pct"])
                    if bucket.get("stop_loss_pct") is not None
                    else None,
                    "take_profit_pct": float(bucket["take_profit_pct"])
                    if bucket.get("take_profit_pct") is not None
                    else None,
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
    def _portfolio_value(
        cash: float,
        buckets: list[dict[str, np.ndarray | int]],
    ) -> float:
        """
        현금과 활성 코호트 평가액을 합쳐 현재 포트폴리오 가치를 계산한다.
        """

        total = float(cash)
        for bucket in buckets:
            total += float(np.asarray(bucket["values"], dtype=np.float64).sum())
        return total

    @staticmethod
    def _build_bucket_exit_mask(
        bucket: dict[str, np.ndarray | int],
        observation_idx: int,
        *,
        pattern_exit_mask: np.ndarray | None,
        pattern_dynamic_exit_index: np.ndarray | None,
    ) -> np.ndarray:
        """
        개별 코호트 버킷에 대해 다음 청산 대상 종목 mask를 계산한다.
        """

        vals_t = np.asarray(bucket["values"], dtype=np.float64)
        entry_vals = np.asarray(bucket["entry_values"], dtype=np.float64)
        idx = np.asarray(bucket["idx"], dtype=np.int64)
        hit = np.zeros(vals_t.shape, dtype=np.bool_)
        valid = np.isfinite(vals_t) & np.isfinite(entry_vals) & (entry_vals > 0.0)
        bucket_stop_loss_value = bucket.get("stop_loss_pct")
        bucket_take_profit_value = bucket.get("take_profit_pct")
        if bucket_stop_loss_value is not None:
            hit |= valid & (vals_t <= entry_vals * (1.0 - float(bucket_stop_loss_value)))
        if bucket_take_profit_value is not None:
            hit |= valid & (vals_t >= entry_vals * (1.0 + float(bucket_take_profit_value)))
        if pattern_exit_mask is not None:
            hit |= np.asarray(pattern_exit_mask[observation_idx, idx], dtype=np.bool_)
        if pattern_dynamic_exit_index is not None:
            signal_entry_idx = int(bucket["signal_entry_idx"])
            exit_idx = np.asarray(
                pattern_dynamic_exit_index[signal_entry_idx, idx],
                dtype=np.int32,
            )
            hit |= exit_idx == observation_idx
        return hit

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
            "stop_loss_pct": float(self.stop_loss_pct) if self.stop_loss_pct is not None else float("nan"),
            "take_profit_pct": float(self.take_profit_pct)
            if self.take_profit_pct is not None
            else float("nan"),
            "execution_lag_days": float(self.execution_lag_days)
            if self.execution_lag_days is not None
            else float("nan"),
            "execution_price_mode": str(self.execution_price_mode)
            if self.execution_price_mode is not None
            else "none",
            "max_weight_per_stock": float(self.max_weight_per_stock),
            "allow_reentry": bool(self.allow_reentry) if self.allow_reentry is not None else True,
            "min_cohort_size": float(self.min_cohort_size)
            if self.min_cohort_size is not None
            else float("nan"),
            "max_cohort_size": float(self.max_cohort_size)
            if self.max_cohort_size is not None
            else float("nan"),
            "buy_fee": float(self.buy_fee),
            "sell_fee": float(self.sell_fee),
            "run_years": float(self.run_years),
            "total_return": float(self.total_return),
            "cagr": float(self.cagr),
            "max_drawdown": float(self.max_drawdown)
            if self.max_drawdown is not None
            else float("nan"),
            "win_rate": float(self.cohort_win_rate)
            if self.cohort_win_rate is not None
            else float("nan"),
            "payoff_ratio": float(self.cohort_payoff_ratio)
            if self.cohort_payoff_ratio is not None
            else float("nan"),
            "cohort_win_rate": float(self.cohort_win_rate)
            if self.cohort_win_rate is not None
            else float("nan"),
            "cohort_payoff_ratio": float(self.cohort_payoff_ratio)
            if self.cohort_payoff_ratio is not None
            else float("nan"),
            "active_day_ratio": float(self.active_day_ratio)
            if self.active_day_ratio is not None
            else float("nan"),
            "mean_turnover": float(self.mean_turnover)
            if self.mean_turnover is not None
            else float("nan"),
            "annual_turnover": float(self.annual_turnover)
            if self.annual_turnover is not None
            else float("nan"),
            "closed_cohort_count": float(self.data.attrs.get("closed_cohort_count", np.nan)),
            "total_buy_fee_paid": float(self.total_buy_fee_paid),
            "total_sell_fee_paid": float(self.total_sell_fee_paid),
            "total_fee_paid": float(self.total_fee_paid),
        }

    def plot(
        self,
        figsize=(12, 5),
        show_kospi: bool = False,
        return_handles: bool = False,
        axes=None,
    ):
        """
        노출도/보유종목수/자산곡선을 3개 패널로 시각화한다.
        """
        return plot_simulator(
            self,
            figsize=figsize,
            show_kospi=show_kospi,
            return_handles=return_handles,
            axes=axes,
        )

    def run(
        self,
        *,
        start_idx: int,
        end_idx: int,
        pattern: str,
        target_horizon: str,
        target_horizon_days: int,
        pattern_mask: np.ndarray,
        pattern_policy_id_matrix: np.ndarray | None,
        policy_horizon_days: np.ndarray | None,
        policy_stop_loss_pct: np.ndarray | None,
        policy_take_profit_pct: np.ndarray | None,
        policy_cohort_scale: np.ndarray | None,
        pattern_exit_mask: np.ndarray | None,
        pattern_dynamic_exit_index: np.ndarray | None,
        stop_loss_pct: float | None = None,
        take_profit_pct: float | None = None,
        execution_lag_days: int = 1,
        execution_price_mode: str = "next_vwap",
        allow_reentry: bool = True,
        min_cohort_size: int = 1,
        max_cohort_size: int | None = None,
    ) -> Simulator:
        """
        패턴 신호를 코호트 포트폴리오로 시뮬레이션한다.

        - 기본 코호트 크기: 전체자산의 1/horizon
        - 선택된 신규 코호트는 기본적으로 100% 크기로 진입
        - branch별 `policy_cohort_scale`이 있으면 그 비율을 추가 적용
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

        stop_loss_value = self._normalize_stop_loss_pct(stop_loss_pct)
        take_profit_value = self._normalize_take_profit_pct(take_profit_pct)
        allow_reentry_value = bool(allow_reentry)
        min_cohort_size_value = int(min_cohort_size)
        if min_cohort_size_value <= 0:
            raise ValueError("min_cohort_size는 1 이상의 정수여야 합니다.")
        max_cohort_size_value = None if max_cohort_size is None else int(max_cohort_size)
        if max_cohort_size_value is not None and max_cohort_size_value <= 0:
            raise ValueError("max_cohort_size는 1 이상의 정수 또는 None이어야 합니다.")
        buy_fee_value = float(self.buy_fee)
        sell_fee_value = float(self.sell_fee)
        if pattern_policy_id_matrix is None or pattern_policy_id_matrix.shape != pattern_mask.shape:
            pattern_policy_id_matrix = np.zeros(pattern_mask.shape, dtype=np.int16)
            pattern_policy_id_matrix[pattern_mask] = 1
        else:
            pattern_policy_id_matrix = np.asarray(pattern_policy_id_matrix, dtype=np.int16)

        if policy_horizon_days is None or len(policy_horizon_days) == 0:
            policy_horizon_days = np.asarray([horizon_days, horizon_days], dtype=np.int32)
        else:
            policy_horizon_days = np.asarray(policy_horizon_days, dtype=np.int32)
        if policy_horizon_days[0] <= 0:
            policy_horizon_days[0] = int(horizon_days)

        if policy_stop_loss_pct is None or len(policy_stop_loss_pct) < len(policy_horizon_days):
            policy_stop_loss_pct = np.full(len(policy_horizon_days), np.nan, dtype=np.float64)
        else:
            policy_stop_loss_pct = np.asarray(policy_stop_loss_pct, dtype=np.float64)

        if policy_take_profit_pct is None or len(policy_take_profit_pct) < len(policy_horizon_days):
            policy_take_profit_pct = np.full(len(policy_horizon_days), np.nan, dtype=np.float64)
        else:
            policy_take_profit_pct = np.asarray(policy_take_profit_pct, dtype=np.float64)
        if policy_cohort_scale is None or len(policy_cohort_scale) < len(policy_horizon_days):
            policy_cohort_scale = np.ones(len(policy_horizon_days), dtype=np.float64)
        else:
            policy_cohort_scale = np.asarray(policy_cohort_scale, dtype=np.float64)
        invalid_scale = (~np.isfinite(policy_cohort_scale)) | (policy_cohort_scale <= 0.0) | (policy_cohort_scale > 1.0)
        if np.any(invalid_scale):
            raise ValueError("policy_cohort_scale 값은 모두 0보다 크고 1 이하여야 합니다.")

        if stop_loss_value is not None and not np.isfinite(policy_stop_loss_pct[0]):
            policy_stop_loss_pct[0] = float(stop_loss_value)
        if take_profit_value is not None and not np.isfinite(policy_take_profit_pct[0]):
            policy_take_profit_pct[0] = float(take_profit_value)
        has_policy_stop_loss = bool(np.any(np.isfinite(policy_stop_loss_pct) & (policy_stop_loss_pct > 0.0)))
        has_policy_take_profit = bool(
            np.any(np.isfinite(policy_take_profit_pct) & (policy_take_profit_pct > 0.0))
        )

        wealth = np.full(len(self.dates), np.nan, dtype=np.float64)
        exposure = np.full(len(self.dates), np.nan, dtype=np.float64)
        selected_count = np.full(len(self.dates), np.nan, dtype=np.float64)
        active_count = np.full(len(self.dates), np.nan, dtype=np.float64)
        turnover = np.full(len(self.dates), np.nan, dtype=np.float64)

        wealth[start_idx] = 1.0
        turnover[start_idx] = 0.0
        cash = 1.0
        active_buckets: list[dict[str, np.ndarray | int]] = []
        snapshots: dict[int, list[dict[str, np.ndarray | int]]] = {start_idx: []}
        total_buy_fee_paid = 0.0
        total_sell_fee_paid = 0.0
        next_cohort_id = 1
        cohort_entry_net: dict[int, float] = {}
        cohort_exit_net: dict[int, float] = {}
        closed_cohort_returns: list[float] = []
        has_pattern_exit = pattern_exit_mask is not None and np.any(pattern_exit_mask)
        has_dynamic_pattern_exit = (
            pattern_dynamic_exit_index is not None
            and np.any(np.asarray(pattern_dynamic_exit_index) >= 0)
        )

        iterator = _progress(
            range(start_idx, end_idx - 1),
            desc=f"{pattern} | run",
        )
        for t in iterator:
            signal_idx = t if lag_days == 1 else (t + 1)
            signal_mask = pattern_mask[signal_idx]
            selected = np.where(signal_mask)[0]
            selected_policy_ids = (
                np.asarray(pattern_policy_id_matrix[signal_idx, selected], dtype=np.int16)
                if selected.size > 0
                else np.zeros(0, dtype=np.int16)
            )
            if (not allow_reentry_value) and selected.size > 0 and active_buckets:
                active_idx_parts: list[np.ndarray] = []
                for bucket in active_buckets:
                    idx_bucket = np.asarray(bucket["idx"], dtype=np.int64)
                    if idx_bucket.size > 0:
                        active_idx_parts.append(idx_bucket)
                if active_idx_parts:
                    held_idx = np.unique(np.concatenate(active_idx_parts))
                    if held_idx.size > 0:
                        keep_selected = ~np.isin(selected, held_idx)
                        selected = selected[keep_selected]
                        selected_policy_ids = selected_policy_ids[keep_selected]
            if max_cohort_size_value is not None and selected.size > max_cohort_size_value:
                selected = selected[:max_cohort_size_value]
                selected_policy_ids = selected_policy_ids[:max_cohort_size_value]
            if selected.size < min_cohort_size_value:
                selected = selected[:0]
                selected_policy_ids = selected_policy_ids[:0]
            actual_selected = 0

            # lag=1: t 기준 판정 -> t+1 체결, lag=0: t+1 기준 판정 -> t+1 체결
            if (
                has_policy_stop_loss
                or has_policy_take_profit
                or has_pattern_exit
                or has_dynamic_pattern_exit
            ) and lag_days == 1:
                for bucket in active_buckets:
                    bucket["exit_next_mask"] = self._build_bucket_exit_mask(
                        bucket,
                        t,
                        pattern_exit_mask=pattern_exit_mask if has_pattern_exit else None,
                        pattern_dynamic_exit_index=(
                            pattern_dynamic_exit_index if has_dynamic_pattern_exit else None
                        ),
                    )

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

            if (
                has_policy_stop_loss
                or has_policy_take_profit
                or has_pattern_exit
                or has_dynamic_pattern_exit
            ) and lag_days == 0:
                for bucket in active_buckets:
                    bucket["exit_next_mask"] = self._build_bucket_exit_mask(
                        bucket,
                        t + 1,
                        pattern_exit_mask=pattern_exit_mask if has_pattern_exit else None,
                        pattern_dynamic_exit_index=(
                            pattern_dynamic_exit_index if has_dynamic_pattern_exit else None
                        ),
                    )

            trade_base_wealth = self._portfolio_value(cash, active_buckets)
            day_buy_notional = 0.0
            day_sell_notional = 0.0

            # 2) 보유기간(horizon) 만료 버킷 청산
            next_active: list[dict[str, np.ndarray | int]] = []
            for bucket in active_buckets:
                age = int(bucket["age"])
                bucket_horizon_days = int(bucket.get("horizon_days", horizon_days))
                idx = np.asarray(bucket["idx"], dtype=np.int64)
                vals = np.asarray(bucket["values"], dtype=np.float64)
                entry_vals = np.asarray(bucket["entry_values"], dtype=np.float64)
                exit_mask = np.asarray(
                    bucket.get("exit_next_mask", np.zeros(idx.size, dtype=np.bool_)),
                    dtype=np.bool_,
                )
                if exit_mask.shape[0] != idx.size:
                    exit_mask = np.zeros(idx.size, dtype=np.bool_)

                if age >= bucket_horizon_days:
                    gross_sell_value = float(vals.sum())
                    day_sell_notional += gross_sell_value
                    sell_fee_paid = gross_sell_value * sell_fee_value
                    total_sell_fee_paid += sell_fee_paid
                    net_sell_value = gross_sell_value - sell_fee_paid
                    cash += net_sell_value
                    cohort_id = int(bucket["cohort_id"])
                    cohort_exit_net[cohort_id] = float(
                        cohort_exit_net.get(cohort_id, 0.0) + net_sell_value
                    )
                    entry_net = float(cohort_entry_net.get(cohort_id, np.nan))
                    if np.isfinite(entry_net) and entry_net > 0.0:
                        closed_cohort_returns.append(cohort_exit_net[cohort_id] / entry_net - 1.0)
                    cohort_entry_net.pop(cohort_id, None)
                    cohort_exit_net.pop(cohort_id, None)
                    continue

                if np.any(exit_mask):
                    cohort_id = int(bucket["cohort_id"])
                    sell_value = float(vals[exit_mask].sum())
                    if sell_value > 0.0:
                        day_sell_notional += sell_value
                        sell_fee_paid = sell_value * sell_fee_value
                        total_sell_fee_paid += sell_fee_paid
                        net_sell_value = sell_value - sell_fee_paid
                        cash += net_sell_value
                        cohort_exit_net[cohort_id] = float(
                            cohort_exit_net.get(cohort_id, 0.0) + net_sell_value
                        )

                    keep = ~exit_mask
                    if np.any(keep):
                        bucket["idx"] = idx[keep]
                        bucket["values"] = vals[keep]
                        bucket["entry_values"] = entry_vals[keep]
                        bucket.pop("exit_next_mask", None)
                        next_active.append(bucket)
                    else:
                        entry_net = float(cohort_entry_net.get(cohort_id, np.nan))
                        if np.isfinite(entry_net) and entry_net > 0.0:
                            closed_cohort_returns.append(
                                cohort_exit_net[cohort_id] / entry_net - 1.0
                            )
                        cohort_entry_net.pop(cohort_id, None)
                        cohort_exit_net.pop(cohort_id, None)
                    continue

                bucket.pop("exit_next_mask", None)
                next_active.append(bucket)
            active_buckets = next_active

            # 3) 신규 코호트 진입
            if selected.size > 0:
                curr_wealth = self._portfolio_value(cash, active_buckets)

                group_orders: list[tuple[np.ndarray, int, float | None, float | None, float]] = []
                target_total = 0.0
                for policy_id in np.unique(selected_policy_ids):
                    group_selected = selected[selected_policy_ids == policy_id]
                    if group_selected.size == 0:
                        continue

                    policy_idx = int(policy_id)
                    group_horizon_days = int(horizon_days)
                    if 0 <= policy_idx < len(policy_horizon_days) and int(policy_horizon_days[policy_idx]) > 0:
                        group_horizon_days = int(policy_horizon_days[policy_idx])
                    group_stop_loss_value = None
                    if 0 <= policy_idx < len(policy_stop_loss_pct):
                        stop_val = float(policy_stop_loss_pct[policy_idx])
                        if np.isfinite(stop_val) and stop_val > 0.0:
                            group_stop_loss_value = stop_val
                    group_take_profit_value = None
                    if 0 <= policy_idx < len(policy_take_profit_pct):
                        take_val = float(policy_take_profit_pct[policy_idx])
                        if np.isfinite(take_val) and take_val > 0.0:
                            group_take_profit_value = take_val
                    group_cohort_scale = 1.0
                    if 0 <= policy_idx < len(policy_cohort_scale):
                        group_cohort_scale = float(policy_cohort_scale[policy_idx])

                    target_gross = (
                        curr_wealth
                        * (1.0 / float(group_horizon_days))
                        * group_cohort_scale
                    )
                    if target_gross <= 0.0:
                        continue
                    group_orders.append(
                        (
                            group_selected.astype(np.int64, copy=False),
                            group_horizon_days,
                            group_stop_loss_value,
                            group_take_profit_value,
                            float(target_gross),
                        )
                    )
                    target_total += float(target_gross)

                spend_scale = 0.0
                if target_total > 0.0 and cash > 0.0:
                    spend_scale = min(1.0, float(cash) / float(target_total))

                for (
                    buy_idx,
                    group_horizon_days,
                    group_stop_loss_value,
                    group_take_profit_value,
                    target_gross,
                ) in group_orders:
                    invest_amount = float(target_gross) * float(spend_scale)
                    if invest_amount <= 0.0:
                        continue

                    net_budget = invest_amount / (1.0 + buy_fee_value)
                    per_stock_value = net_budget / float(buy_idx.size)
                    if per_stock_value <= 0.0:
                        continue

                    buy_values = np.full(buy_idx.size, per_stock_value, dtype=np.float64)
                    invested_net = float(buy_values.sum())
                    day_buy_notional += invested_net
                    buy_fee_paid = invested_net * buy_fee_value
                    gross_spend = invested_net + buy_fee_paid
                    total_buy_fee_paid += buy_fee_paid
                    cash -= gross_spend
                    cohort_id = int(next_cohort_id)
                    active_buckets.append(
                        {
                            "idx": buy_idx,
                            "values": buy_values,
                            "entry_values": buy_values.copy(),
                            "age": 0,
                            "entry_idx": t + 1,
                            "signal_entry_idx": signal_idx,
                            "cohort_id": cohort_id,
                            "horizon_days": int(group_horizon_days),
                            "stop_loss_pct": group_stop_loss_value,
                            "take_profit_pct": group_take_profit_value,
                        }
                    )
                    cohort_entry_net[cohort_id] = invested_net
                    cohort_exit_net[cohort_id] = 0.0
                    next_cohort_id += 1
                    actual_selected += int(buy_idx.size)

            # 4) 다음 날짜 기준 자산/메타 시계열 기록
            invested_value = 0.0
            total_active = 0
            for bucket in active_buckets:
                invested_value += float(np.asarray(bucket["values"], dtype=np.float64).sum())
                total_active += int(np.asarray(bucket["idx"], dtype=np.int64).size)
            next_wealth = cash + invested_value
            wealth[t + 1] = next_wealth
            turnover[t + 1] = (
                (day_buy_notional + day_sell_notional) / trade_base_wealth
                if np.isfinite(trade_base_wealth) and trade_base_wealth > 0.0
                else np.nan
            )
            exposure[t] = invested_value / next_wealth if next_wealth > 0.0 else np.nan
            selected_count[t] = float(actual_selected)
            active_count[t] = float(total_active)
            snapshots[t + 1] = self._clone_active_buckets(active_buckets)

        final_invested = 0.0
        final_active = 0
        for bucket in active_buckets:
            final_invested += float(np.asarray(bucket["values"], dtype=np.float64).sum())
            final_active += int(np.asarray(bucket["idx"], dtype=np.int64).size)
        final_wealth = cash + final_invested
        wealth[end_idx - 1] = final_wealth
        if not np.isfinite(turnover[end_idx - 1]):
            turnover[end_idx - 1] = 0.0
        exposure[end_idx - 1] = final_invested / final_wealth if final_wealth > 0.0 else np.nan
        selected_count[end_idx - 1] = 0.0
        active_count[end_idx - 1] = float(final_active)

        out_index = pd.DatetimeIndex(self.dates[start_idx:end_idx])
        out = pd.DataFrame(
            {
                "wealth": wealth[start_idx:end_idx],
                "exposure": exposure[start_idx:end_idx],
                "selected_count": selected_count[start_idx:end_idx],
                "active_count": active_count[start_idx:end_idx],
            },
            index=out_index,
        )
        out.index.name = "date"

        exposure_prev = out["exposure"].to_numpy(dtype=np.float64)[:-1]
        active_mask = np.isfinite(exposure_prev) & (exposure_prev > 1e-12)
        cohort_ret = np.asarray(closed_cohort_returns, dtype=np.float64)
        cohort_ret = cohort_ret[np.isfinite(cohort_ret)]
        win_rate_value = float("nan")
        payoff_ratio_value = float("nan")
        if cohort_ret.size > 0:
            winners = cohort_ret[cohort_ret > 0.0]
            losers = cohort_ret[cohort_ret < 0.0]
            win_rate_value = float(np.mean(cohort_ret > 0.0))
            if winners.size > 0 and losers.size > 0:
                payoff_ratio_value = float(winners.mean() / abs(losers.mean()))
        active_day_ratio_value = float(np.mean(active_mask)) if active_mask.size > 0 else float("nan")
        turnover_window = turnover[start_idx:end_idx]
        mean_turnover_value = (
            float(np.nanmean(turnover_window))
            if np.any(np.isfinite(turnover_window))
            else float("nan")
        )
        annual_turnover_value = (
            float(mean_turnover_value * float(TRADING_DAYS_PER_YEAR))
            if np.isfinite(mean_turnover_value)
            else float("nan")
        )

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

        max_drawdown = float("nan")
        wealth_values = out["wealth"].to_numpy(dtype=np.float64)
        wealth_valid = np.isfinite(wealth_values) & (wealth_values > 0.0)
        if np.any(wealth_valid):
            running_peak = np.maximum.accumulate(wealth_values[wealth_valid])
            drawdown = wealth_values[wealth_valid] / running_peak - 1.0
            max_drawdown = float(np.min(drawdown))

        out.attrs["cagr"] = cagr
        out.attrs["max_drawdown"] = max_drawdown
        out.attrs["total_return"] = total_return
        out.attrs["run_years"] = years
        out.attrs["win_rate"] = win_rate_value
        out.attrs["payoff_ratio"] = payoff_ratio_value
        out.attrs["cohort_win_rate"] = win_rate_value
        out.attrs["cohort_payoff_ratio"] = payoff_ratio_value
        out.attrs["active_day_ratio"] = active_day_ratio_value
        out.attrs["mean_turnover"] = mean_turnover_value
        out.attrs["annual_turnover"] = annual_turnover_value
        out.attrs["closed_cohort_count"] = float(cohort_ret.size)
        out.attrs["pattern"] = pattern
        out.attrs["target_horizon"] = target_horizon
        out.attrs["target_horizon_days"] = horizon_days
        out.attrs["stop_loss_pct"] = stop_loss_value if stop_loss_value is not None else float("nan")
        out.attrs["take_profit_pct"] = (
            take_profit_value if take_profit_value is not None else float("nan")
        )
        out.attrs["execution_lag_days"] = float(lag_days)
        out.attrs["execution_price_mode"] = str(execution_price_mode)
        out.attrs["max_weight_per_stock"] = float("nan")
        out.attrs["allow_reentry"] = bool(allow_reentry_value)
        out.attrs["min_cohort_size"] = float(min_cohort_size_value)
        out.attrs["max_cohort_size"] = (
            float(max_cohort_size_value)
            if max_cohort_size_value is not None
            else float("nan")
        )
        out.attrs["buy_fee"] = buy_fee_value
        out.attrs["sell_fee"] = sell_fee_value
        out.attrs["total_buy_fee_paid"] = total_buy_fee_paid
        out.attrs["total_sell_fee_paid"] = total_sell_fee_paid
        out.attrs["total_fee_paid"] = total_buy_fee_paid + total_sell_fee_paid

        self.data = out
        self.pattern = pattern
        self.target_horizon = target_horizon
        self.target_horizon_days = horizon_days
        self.stop_loss_pct = stop_loss_value
        self.take_profit_pct = take_profit_value
        self.execution_lag_days = lag_days
        self.execution_price_mode = str(execution_price_mode)
        self.max_weight_per_stock = float("nan")
        self.allow_reentry = bool(allow_reentry_value)
        self.min_cohort_size = int(min_cohort_size_value)
        self.max_cohort_size = (
            int(max_cohort_size_value)
            if max_cohort_size_value is not None
            else None
        )
        self.run_years = years
        self.total_return = total_return
        self.cagr = cagr
        self.max_drawdown = max_drawdown
        self.cohort_win_rate = win_rate_value
        self.cohort_payoff_ratio = payoff_ratio_value
        self.active_day_ratio = active_day_ratio_value
        self.mean_turnover = mean_turnover_value
        self.annual_turnover = annual_turnover_value
        self.total_buy_fee_paid = total_buy_fee_paid
        self.total_sell_fee_paid = total_sell_fee_paid
        self._portfolio_snapshots = snapshots
        return self
