from __future__ import annotations

import contextlib
import io
from dataclasses import dataclass
import sys

import numpy as np
import pandas as pd

from metricstudio.backtest import Backtest
from metricstudio.filter import Filter
from metricstudio.univ import Univ
from metricstudio import patterns as p


START = "2000-01-01"
END = "2026-03-20"
FILTER_GROUPS = {
    "d78910": [7, 8, 9, 10],
    "d8910": [8, 9, 10],
    "d910": [9, 10],
}


@dataclass(frozen=True)
class Variant:
    name: str
    bandwidth: float = 0.05
    high_threshold: float = 0.90
    mfi_threshold: float = 50.0
    amount_threshold: float = 2.0
    rs_window: int | None = None


VARIANTS = [
    Variant("base"),
    Variant("high93", high_threshold=0.93),
    Variant("amt15", amount_threshold=1.5),
    Variant("bb04", bandwidth=0.04),
    Variant("rs60", rs_window=60),
    Variant("high93_rs60", high_threshold=0.93, rs_window=60),
]
VALIDATION_CANDIDATES = [
    ("d78910", "base"),
    ("d78910", "bb04"),
    ("d8910", "base"),
    ("d8910", "bb04"),
    ("d8910", "high93"),
    ("d910", "base"),
    ("d910", "high93"),
    ("d910", "bb04"),
]


def _suppress_output():
    out = io.StringIO()
    err = io.StringIO()
    stack = contextlib.ExitStack()
    stack.enter_context(contextlib.redirect_stdout(out))
    stack.enter_context(contextlib.redirect_stderr(err))
    return stack


def _build_backtest() -> Backtest:
    return Backtest(
        START,
        END,
        benchmark=None,
        by="day",
        univ=Univ(market=["KOSPI", "KOSDAQ"]),
    )


def _build_pattern(
    name: str,
    *,
    bandwidth: float = 0.05,
    high_threshold: float = 0.90,
    mfi_threshold: float = 50.0,
    amount_threshold: float = 2.0,
    rs_window: int | None = None,
):
    bb = p.Bollinger(f"{name}_bb").on(
        trigger="breakout_up",
        breakout_cooldown_days=3,
        bandwidth_max=bandwidth,
    )
    uptrend = p.Trending(f"{name}_ma").on(trigger="ma_trend_up", window=200)
    mfi = p.MFI(f"{name}_mfi").on(trigger="above", threshold=mfi_threshold)
    high52w = p.High(f"{name}_high").on(window=240, threshold=high_threshold, stay_days=1)
    amount = p.AmountSurge(f"{name}_amt").on(window=20, threshold=amount_threshold)

    pattern = bb + uptrend + high52w + mfi + amount
    rank_rules = [
        ("stock", "marketcap.desc"),
        (amount, "ratio.desc"),
        (bb, "bandwidth.asc"),
        (high52w, "proximity.desc"),
        (uptrend, "ma_slope.desc"),
        (mfi, "value.desc"),
    ]
    if rs_window is not None:
        rs = p.RelativeStrength(f"{name}_rs").on(
            market="kospi",
            window=rs_window,
            threshold=0.0,
        )
        pattern = pattern + rs
        rank_rules.append((rs, "excess_return.desc"))

    return pattern.named(name).rank_by(*rank_rules).nmax(5)


def screen_variants() -> pd.DataFrame:
    rows: list[dict[str, float | str]] = []

    for filter_name, deciles in FILTER_GROUPS.items():
        print(f"[screen] {filter_name} analyze start", flush=True)
        bt = _build_backtest()
        patterns = [
            _build_pattern(
                f"{variant.name}_{filter_name}",
                bandwidth=variant.bandwidth,
                high_threshold=variant.high_threshold,
                mfi_threshold=variant.mfi_threshold,
                amount_threshold=variant.amount_threshold,
                rs_window=variant.rs_window,
            )
            for variant in VARIANTS
        ]
        with _suppress_output():
            stats = bt.analyze(*patterns, include_base=False, filter=Filter(market_cap=deciles))
        print(f"[screen] {filter_name} analyze done", flush=True)

        frame = stats.to_frame().reset_index()
        frame = frame[(frame["period"] == "1M") & (frame["scope"] != "empty")].copy()
        frame["variant"] = frame["pattern"].str.replace(
            r"_(d78910|d8910|d910)$",
            "",
            regex=True,
        )
        for _, row in frame.iterrows():
            rows.append(
                {
                    "filter": filter_name,
                    "variant": str(row["variant"]),
                    "count": float(row["count"]),
                    "rise_prob": float(row["rise_prob"]),
                    "arith_mean": float(row["arith_mean"]),
                    "geom_mean": float(row["geom_mean"]),
                }
            )
        print(f"[screen] {filter_name} rows={len(frame)}", flush=True)

    out = pd.DataFrame(rows)
    return out.sort_values(
        ["filter", "geom_mean", "rise_prob", "count"],
        ascending=[True, False, False, False],
        kind="stable",
    ).reset_index(drop=True)


def _annual_volatility(frame: pd.DataFrame) -> float:
    wealth = frame["wealth"].to_numpy(dtype=np.float64)
    if wealth.size < 3:
        return float("nan")
    returns = wealth[1:] / wealth[:-1] - 1.0
    returns = returns[np.isfinite(returns)]
    if returns.size < 2:
        return float("nan")
    return float(np.std(returns, ddof=1) * np.sqrt(240.0))


def validate_candidates() -> pd.DataFrame:
    rows: list[dict[str, float | str]] = []
    grouped: dict[str, list[str]] = {}
    for filter_name, variant_name in VALIDATION_CANDIDATES:
        grouped.setdefault(filter_name, []).append(variant_name)

    for filter_name, variant_names in grouped.items():
        print(f"[validate] {filter_name} analyze start", flush=True)
        bt = _build_backtest()
        patterns = []
        for variant_name in variant_names:
            variant = next(item for item in VARIANTS if item.name == variant_name)
            patterns.append(
                _build_pattern(
                    f"{variant.name}_{filter_name}",
                    bandwidth=variant.bandwidth,
                    high_threshold=variant.high_threshold,
                    mfi_threshold=variant.mfi_threshold,
                    amount_threshold=variant.amount_threshold,
                    rs_window=variant.rs_window,
                )
            )
        with _suppress_output():
            bt.analyze(
                *patterns,
                include_base=False,
                filter=Filter(market_cap=FILTER_GROUPS[filter_name]),
            )
        print(f"[validate] {filter_name} analyze done", flush=True)

        for variant_name in variant_names:
            pattern_name = f"{variant_name}_{filter_name}"
            print(f"[validate] {pattern_name} run start", flush=True)
            with _suppress_output():
                sim = bt.run(
                    pattern=pattern_name,
                    target_horizon=20,
                    trade_price_mode="당일종가",
                )
            summary = sim.summary()
            frame = sim.to_frame(copy=False)
            ann_vol = _annual_volatility(frame)
            rows.append(
                {
                    "filter": filter_name,
                    "variant": variant_name,
                    "cagr": float(summary["cagr"]),
                    "mdd": float(summary["max_drawdown"]),
                    "vol": ann_vol,
                    "ir": float(summary["cagr"]) / ann_vol
                    if np.isfinite(ann_vol) and ann_vol > 0.0
                    else float("nan"),
                    "stability": float(summary.get("wealth_stability", float("nan"))),
                    "exposure": float(np.nanmean(frame["exposure"].to_numpy(dtype=np.float64))),
                    "win": float(summary["cohort_win_rate"]),
                    "payoff": float(summary["cohort_payoff_ratio"]),
                }
            )
            print(f"[validate] {pattern_name} run done", flush=True)

    out = pd.DataFrame(rows)
    return out.sort_values(
        ["filter", "ir", "cagr", "stability"],
        ascending=[True, False, False, False],
        kind="stable",
    ).reset_index(drop=True)


if __name__ == "__main__":
    pd.set_option("display.width", 200)
    pd.set_option("display.max_columns", None)
    if "--validate" in sys.argv:
        print(validate_candidates().round(4).to_string(index=False))
    else:
        print(screen_variants().round(4).to_string(index=False))
