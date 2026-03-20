"""Shared plotting helpers for MetricStudio objects."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Iterable
import warnings

import numpy as np

if TYPE_CHECKING:
    from metricstudio.backtest import Backtest
    from metricstudio.simulate import Simulator
    from metricstudio.stats import StatsCollection

_PLOT_FONT_CONFIGURED = False
_PLOT_MODULES = None


def _plot_modules():
    """
    matplotlib import를 실제 plotting 시점까지 지연한다.
    """

    global _PLOT_MODULES
    if _PLOT_MODULES is None:
        import matplotlib.pyplot as plt
        from matplotlib import font_manager
        from matplotlib.ticker import MaxNLocator, StrMethodFormatter

        _PLOT_MODULES = (plt, font_manager, MaxNLocator, StrMethodFormatter)
    return _PLOT_MODULES


def _configure_plot_font() -> None:
    """
    한글 깨짐을 줄이기 위해 사용 가능한 CJK 폰트를 matplotlib에 적용한다.
    """

    global _PLOT_FONT_CONFIGURED
    if _PLOT_FONT_CONFIGURED:
        return

    plt, font_manager, _, _ = _plot_modules()

    font_files = [
        Path("/mnt/c/Windows/Fonts/malgun.ttf"),
        Path("/mnt/c/Windows/Fonts/malgunbd.ttf"),
        Path("/mnt/c/Windows/Fonts/NotoSansKR-VF.ttf"),
        Path("/mnt/c/Windows/Fonts/gulim.ttc"),
        Path("/System/Library/Fonts/AppleGothic.ttf"),
        Path("/Library/Fonts/AppleGothic.ttf"),
    ]
    for font_path in font_files:
        if not font_path.exists():
            continue
        try:
            font_manager.fontManager.addfont(str(font_path))
        except Exception:
            continue

    available = {f.name for f in font_manager.fontManager.ttflist}
    preferred = [
        "Malgun Gothic",
        "AppleGothic",
        "NanumGothic",
        "Noto Sans CJK KR",
        "Noto Sans KR",
        "Gulim",
        "Arial Unicode MS",
    ]
    preferred_available = [name for name in preferred if name in available]
    existing = list(plt.rcParams.get("font.sans-serif", []))
    merged: list[str] = []
    for name in [*preferred_available, *existing]:
        if name and name not in merged:
            merged.append(name)

    plt.rcParams["font.family"] = "sans-serif"
    plt.rcParams["font.sans-serif"] = merged
    plt.rcParams["axes.unicode_minus"] = False

    if not preferred_available:
        warnings.filterwarnings(
            "ignore",
            message=r"Glyph .* missing from font\(s\).*",
            category=UserWarning,
        )
        warnings.filterwarnings(
            "ignore",
            message=r"Glyph .* missing from current font\..*",
            category=UserWarning,
        )
    _PLOT_FONT_CONFIGURED = True


def _shade_regime_spans(
    ax,
    index,
    regime_mask,
    *,
    color: str = "silver",
    alpha: float = 0.18,
) -> None:
    """
    bool regime 마스크의 연속 구간을 x축 배경 음영으로 표시한다.
    """

    if regime_mask is None or len(index) == 0:
        return
    mask = np.asarray(regime_mask, dtype=np.bool_)
    if mask.ndim != 1 or mask.shape[0] != len(index):
        return

    padded = np.concatenate(([False], mask, [False]))
    starts = np.flatnonzero(padded[1:] & ~padded[:-1])
    ends = np.flatnonzero(padded[:-1] & ~padded[1:]) - 1
    for start_idx, end_idx in zip(starts, ends):
        left = index[start_idx]
        right = index[end_idx + 1] if (end_idx + 1) < len(index) else index[end_idx]
        ax.axvspan(left, right, color=color, alpha=alpha, linewidth=0.0, zorder=0)


def _plot_kospi_reference_curve(
    ax,
    index,
    kospi_curve,
) -> None:
    """
    시작값=1.0으로 정규화된 KOSPI 기준선을 wealth 축에 함께 그린다.
    """

    if kospi_curve is None:
        return

    arr = np.asarray(kospi_curve, dtype=np.float64)
    if arr.ndim != 1 or arr.shape[0] != len(index):
        return
    ax.plot(
        index,
        arr,
        linewidth=1.4,
        linestyle="--",
        alpha=0.9,
        color="#6C757D",
        label="KOSPI",
        zorder=1.0,
    )


def plot_stats_collection(
    stats_collection: StatsCollection,
    *,
    patterns: Iterable[str] | None = None,
    start=None,
    end=None,
    figsize=(16, 4),
    annualized: bool = False,
    cost: bool | None = None,
    rise_ylim=None,
    return_ylim=None,
    return_handles: bool = False,
    axes=None,
):
    """
    StatsCollection의 horizon 비교 4패널을 그린다.
    """

    from metricstudio.stats import (
        _annualize_returns,
        _apply_roundtrip_cost,
        _apply_y_ticks,
        _draw_hline_if_in_view,
        _normalize_ylim_percent,
        _share_return_y_axis,
    )

    _configure_plot_font()
    plt, _, _, _ = _plot_modules()
    if not stats_collection.stats_map:
        raise ValueError("StatsCollection이 비어 있습니다.")

    names = stats_collection._ordered_pattern_names(patterns)
    if not names:
        raise ValueError("플롯할 패턴이 선택되지 않았습니다.")
    cost_enabled = annualized if cost is None else bool(cost)
    if cost_enabled and not annualized:
        raise ValueError("cost=True는 annualized=True일 때만 사용할 수 있습니다.")

    color_map = stats_collection._pattern_colors(names)
    display_map = stats_collection._display_label_map(names)
    frames = []
    horizon_day_map = {
        label: int(days)
        for label, days in next(iter(stats_collection.stats_map.values())).horizons
    }
    for name in names:
        df = stats_collection.get(name).to_frame(start, end).reset_index()
        df["pattern"] = name
        frames.append(df)
    import pandas as pd

    combined = pd.concat(frames, ignore_index=True)
    exposure_denominator_map = stats_collection._exposure_denominator_map(start, end)
    combined["exposure_denominator"] = combined["period"].map(exposure_denominator_map).astype(float)
    exposure_denominator = combined["exposure_denominator"].to_numpy(dtype=float)
    pattern_count = combined["count"].to_numpy(dtype=float)
    valid_exposure = (
        np.isfinite(pattern_count)
        & np.isfinite(exposure_denominator)
        & (exposure_denominator > 0.0)
    )
    exposure_ratio = np.full(len(combined), np.nan, dtype=np.float64)
    exposure_ratio[valid_exposure] = (
        pattern_count[valid_exposure] / exposure_denominator[valid_exposure]
    )
    combined["exposure_ratio"] = exposure_ratio
    combined["horizon_days"] = combined["period"].map(horizon_day_map).astype(float)
    if cost_enabled:
        combined["arith_mean"] = _apply_roundtrip_cost(
            combined["arith_mean"].to_numpy(dtype=float),
        )
        combined["geom_mean"] = _apply_roundtrip_cost(
            combined["geom_mean"].to_numpy(dtype=float),
        )
    if annualized:
        combined["arith_mean"] = _annualize_returns(
            combined["arith_mean"].to_numpy(dtype=float),
            combined["horizon_days"].to_numpy(dtype=float),
            mode="arith",
        )
        combined["geom_mean"] = _annualize_returns(
            combined["geom_mean"].to_numpy(dtype=float),
            combined["horizon_days"].to_numpy(dtype=float),
            mode="geom",
        )

    periods = combined["period"].unique().tolist()
    x = np.arange(len(periods))
    period_index = {label: idx for idx, label in enumerate(periods)}

    created_axes = axes is None
    title_fontsize = 14
    label_fontsize = 10
    tick_fontsize = 10
    if created_axes:
        fig, axes = plt.subplots(1, 4, figsize=figsize, constrained_layout=True)
    else:
        axes = np.asarray(axes, dtype=object).reshape(-1)
        if axes.size != 4:
            raise ValueError("axes는 길이 4인 matplotlib Axes 컬렉션이어야 합니다.")
        fig = axes[0].figure
        if any(ax.figure is not fig for ax in axes):
            raise ValueError("axes는 모두 같은 figure에 속해야 합니다.")
        for ax in axes:
            ax.clear()

    for name in names:
        group = combined.loc[combined["pattern"] == name]
        if group.empty:
            continue
        color = color_map.get(name, None)
        xs = group["period"].map(period_index).to_numpy(dtype=float)
        axes[0].plot(xs, group["arith_mean"] * 100.0, marker="o", color=color, label=name)
        axes[1].plot(xs, group["geom_mean"] * 100.0, marker="o", linestyle="-", color=color, label=name)
        axes[2].plot(xs, group["rise_prob"] * 100.0, marker="o", color=color, label=name)
        axes[3].plot(xs, group["exposure_ratio"] * 100.0, marker="o", color=color, label=name)

    return_title_prefix = "Annualized " if annualized else ""
    return_ylabel = "Annualized Return (%)" if annualized else "Return (%)"
    for ax, title, ylabel, draw_zero in [
        (axes[0], f"{return_title_prefix}Arithmetic Mean", return_ylabel, True),
        (axes[1], f"{return_title_prefix}Geometric Mean", return_ylabel, True),
        (axes[2], "Rise Probability (%)", "Rise Probability (%)", False),
        (axes[3], "Pattern Exposure (%)", "Pattern Exposure (%)", False),
    ]:
        if draw_zero:
            ax.axhline(0.0, color="gray", linewidth=0.8, linestyle="--")
        ax.set_xticks(x)
        ax.set_xticklabels(periods, rotation=0)
        ax.set_title(title, fontsize=title_fontsize)
        ax.set_ylabel(ylabel, fontsize=label_fontsize)
        ax.tick_params(axis="both", labelsize=tick_fontsize)

    axes[2].set_ylabel("")
    axes[3].set_ylabel("")
    stats_collection._apply_legend_order(axes[0], names, display_map)

    arith_vals = combined["arith_mean"].to_numpy(dtype=float) * 100.0
    geom_vals = combined["geom_mean"].to_numpy(dtype=float) * 100.0
    finite_vals = np.concatenate(
        [arith_vals[np.isfinite(arith_vals)], geom_vals[np.isfinite(geom_vals)]]
    )
    return_ylim_pct = _normalize_ylim_percent(return_ylim)
    if return_ylim_pct is not None:
        axes[0].set_ylim(*return_ylim_pct)
        axes[1].set_ylim(*return_ylim_pct)
    elif finite_vals.size:
        ymin = float(finite_vals.min())
        ymax = float(finite_vals.max())
        span = ymax - ymin
        margin = max(1e-4, 0.05 * span)
        axes[0].set_ylim(ymin - margin, ymax + margin)
        axes[1].set_ylim(ymin - margin, ymax + margin)

    rise_ylim_pct = _normalize_ylim_percent(rise_ylim)
    if rise_ylim_pct is not None:
        axes[2].set_ylim(*rise_ylim_pct)
    _draw_hline_if_in_view(axes[2], 50.0, color="gray", linewidth=0.8, linestyle="--")

    axes[3].set_ylim(0.0, 100.0)

    _share_return_y_axis(axes)
    _apply_y_ticks(axes)

    if return_handles:
        return fig, axes
    return None

def plot_stats_history(
    stats_collection: StatsCollection,
    *,
    horizon: str | int = "1M",
    patterns: Iterable[str] | None = None,
    start=None,
    end=None,
    figsize=(12, 4),
    history_window: int = 240,
    min_count: int = 30,
    require_full_window: bool = True,
    rise_ylim=None,
    return_ylim=None,
    return_handles: bool = False,
):
    """
    StatsCollection의 rolling horizon 통계를 그린다.
    """

    from metricstudio.stats import (
        _apply_date_ticks,
        _apply_y_ticks,
        _as_percent,
        _draw_hline_if_in_view,
        _normalize_ylim_percent,
        _share_return_y_axis,
    )

    _configure_plot_font()
    plt, _, _, _ = _plot_modules()
    if not stats_collection.stats_map:
        raise ValueError("StatsCollection이 비어 있습니다.")

    names = stats_collection._ordered_pattern_names(patterns)
    if not names:
        raise ValueError("플롯할 패턴이 선택되지 않았습니다.")

    color_map = stats_collection._pattern_colors(names)
    display_map = stats_collection._display_label_map(names)
    fig, axes = plt.subplots(1, 3, figsize=figsize, constrained_layout=True, sharex=True)

    first_dates = None
    label = None
    arith_series = []
    geom_series = []
    for name in names:
        df = stats_collection.to_frame_history(
            horizon=horizon,
            start=start,
            end=end,
            history_window=history_window,
            min_count=min_count,
            require_full_window=require_full_window,
            pattern=name,
        )
        dates = df.index.to_numpy()
        arith = _as_percent(df["arith_mean"].to_numpy(dtype=float))
        geom = _as_percent(df["geom_mean"].to_numpy(dtype=float))
        rise = _as_percent(df["rise_prob"].to_numpy(dtype=float))
        current_label = str(df["horizon"].iloc[0]) if not df.empty else str(horizon)
        if first_dates is None:
            first_dates = dates
            label = current_label
        elif not np.array_equal(first_dates, dates):
            raise ValueError("plot_history에서는 모든 패턴이 동일한 날짜 인덱스를 가져야 합니다.")
        color = color_map.get(name, None)
        axes[0].plot(dates, arith, label=name, color=color)
        axes[1].plot(dates, geom, linestyle="-", label=name, color=color)
        axes[2].plot(dates, rise, label=name, color=color)
        arith_series.append(arith)
        geom_series.append(geom)

    axes[0].axhline(0.0, color="gray", linewidth=0.8, linestyle="--")
    axes[1].axhline(0.0, color="gray", linewidth=0.8, linestyle="--")
    axes[0].set_ylabel("Return (%)")
    axes[2].set_ylabel("")

    title_prefix = label if label is not None else "Horizon"
    axes[0].set_title(f"{title_prefix} Arithmetic Mean")
    axes[1].set_title(f"{title_prefix} Geometric Mean")
    axes[2].set_title(f"{title_prefix} Rise Probability")

    stats_collection._apply_legend_order(axes[0], names, display_map)

    return_ylim_pct = _normalize_ylim_percent(return_ylim)
    if return_ylim_pct is not None:
        axes[0].set_ylim(*return_ylim_pct)
        axes[1].set_ylim(*return_ylim_pct)
    else:
        finite_blocks = []
        for series in arith_series + geom_series:
            finite = series[np.isfinite(series)]
            if finite.size:
                finite_blocks.append(finite)
        if finite_blocks:
            combined = np.concatenate(finite_blocks)
            ymin = float(combined.min())
            ymax = float(combined.max())
            span = ymax - ymin
            margin = max(1e-4, 0.05 * span)
            axes[0].set_ylim(ymin - margin, ymax + margin)
            axes[1].set_ylim(ymin - margin, ymax + margin)
    rise_ylim_pct = _normalize_ylim_percent(rise_ylim)
    if rise_ylim_pct is not None:
        axes[2].set_ylim(*rise_ylim_pct)
    _draw_hline_if_in_view(axes[2], 50.0, color="gray", linewidth=0.8, linestyle="--")

    if first_dates is not None:
        _apply_date_ticks(axes, first_dates)
    _share_return_y_axis(axes)
    _apply_y_ticks(axes)

    if return_handles:
        return fig, axes
    return None


def plot_simulator(
    simulator: Simulator,
    *,
    figsize=(12, 5),
    show_kospi: bool = False,
    return_handles: bool = False,
    axes=None,
):
    """
    Simulator의 노출도/보유종목수/자산곡선 3패널을 그린다.
    """

    from metricstudio.simulate import TRADING_DAYS_PER_YEAR

    out = simulator.to_frame(copy=False)
    if out.empty:
        raise ValueError("Simulator에 플롯할 데이터가 없습니다.")

    meta = simulator.summary()
    selected_count_values = (
        out["selected_count"].to_numpy(dtype=float)
        if "selected_count" in out.columns
        else np.zeros(len(out), dtype=np.float64)
    )
    active_count_values = (
        out["active_count"].to_numpy(dtype=float)
        if "active_count" in out.columns
        else None
    )
    cagr = float(meta["cagr"])
    cagr_text = f"{cagr * 100.0:.2f}%" if np.isfinite(cagr) else "nan"
    max_drawdown = float(meta.get("max_drawdown", np.nan))
    max_drawdown_text = f"{max_drawdown * 100.0:.2f}%" if np.isfinite(max_drawdown) else "nan"
    wealth_vals = out["wealth"].to_numpy(dtype=float)
    daily_ret = wealth_vals[1:] / wealth_vals[:-1] - 1.0
    daily_ret = daily_ret[np.isfinite(daily_ret)]
    ann_vol = (
        float(np.std(daily_ret, ddof=1) * np.sqrt(float(TRADING_DAYS_PER_YEAR)))
        if daily_ret.size >= 2
        else float("nan")
    )
    ann_vol_text = f"{ann_vol * 100.0:.2f}%" if np.isfinite(ann_vol) else "nan"
    ir = cagr / ann_vol if np.isfinite(cagr) and np.isfinite(ann_vol) and ann_vol > 0.0 else float("nan")
    ir_text = f"{ir:.2f}" if np.isfinite(ir) else "nan"
    win_rate = float(meta.get("win_rate", meta.get("cohort_win_rate", np.nan)))
    payoff_ratio = float(meta.get("payoff_ratio", meta.get("cohort_payoff_ratio", np.nan)))
    active_day_ratio = float(meta.get("active_day_ratio", np.nan))
    annual_turnover = float(meta.get("annual_turnover", np.nan))
    mean_exposure = float(np.nanmean(out["exposure"].to_numpy(dtype=float)))
    mean_selected_count = (
        float(np.nanmean(selected_count_values))
        if "selected_count" in out.columns
        else float("nan")
    )
    mean_active_count = (
        float(np.nanmean(active_count_values))
        if active_count_values is not None
        else float("nan")
    )
    win_text = f"{win_rate * 100.0:.2f}%" if np.isfinite(win_rate) else "nan"
    payoff_text = f"{payoff_ratio:.2f}" if np.isfinite(payoff_ratio) else "nan"
    active_text = f"{active_day_ratio * 100.0:.2f}%" if np.isfinite(active_day_ratio) else "nan"
    turnover_text = f"{annual_turnover * 100.0:.2f}%" if np.isfinite(annual_turnover) else "nan"
    mean_exposure_text = f"{mean_exposure * 100.0:.2f}%" if np.isfinite(mean_exposure) else "nan"
    mean_selected_count_text = f"{mean_selected_count:.1f}" if np.isfinite(mean_selected_count) else "nan"
    mean_active_count_text = f"{mean_active_count:.1f}" if np.isfinite(mean_active_count) else "nan"

    _configure_plot_font()
    plt, _, _, _ = _plot_modules()
    created_axes = axes is None
    title_fontsize = 14
    label_fontsize = 10
    tick_fontsize = 10
    legend_fontsize = 10
    info_fontsize = 11
    if created_axes:
        fig, axes = plt.subplots(1, 3, figsize=figsize, constrained_layout=False, sharex=True)
        for ax in axes:
            ax.set_box_aspect(1.0)
    else:
        axes = np.asarray(axes, dtype=object).reshape(-1)
        if axes.size != 3:
            raise ValueError("axes는 길이 3인 matplotlib Axes 컬렉션이어야 합니다.")
        fig = axes[0].figure
        if any(ax.figure is not fig for ax in axes):
            raise ValueError("axes는 모두 같은 figure에 속해야 합니다.")
        for ax in axes:
            ax.clear()
    regime_mask = out.attrs.get("regime_active_mask")
    for ax in axes:
        _shade_regime_spans(ax, out.index, regime_mask)

    axes[0].plot(
        out.index,
        out["exposure"] * 100.0,
        color="#067BC2",
        linewidth=1.5,
        label="Daily exposure",
    )
    if np.isfinite(mean_exposure):
        axes[0].axhline(
            mean_exposure * 100.0,
            color="gray",
            linewidth=1.0,
            linestyle="--",
            label=f"Mean {mean_exposure * 100.0:.1f}%",
        )
    axes[0].set_title("Portfolio Exposure", fontsize=title_fontsize)
    axes[0].set_ylabel("Exposure (%)", fontsize=label_fontsize)
    axes[0].tick_params(axis="both", labelsize=tick_fontsize)
    axes[0].legend(loc="upper left", fontsize=legend_fontsize)
    axes[0].grid(alpha=0.25, linestyle="--")

    axes[1].plot(
        out.index,
        selected_count_values,
        color="#F37748",
        linewidth=1.8,
        label="New cohort count",
    )
    if active_count_values is not None:
        axes[1].plot(
            out.index,
            active_count_values,
            color="#067BC2",
            linewidth=1.6,
            alpha=0.9,
            label="Total active count",
        )
        axes[1].legend(loc="upper left", fontsize=legend_fontsize)
    axes[1].set_title("Portfolio count", fontsize=title_fontsize)
    axes[1].tick_params(axis="both", labelsize=tick_fontsize)
    axes[1].tick_params(axis="y", pad=2)
    axes[1].grid(alpha=0.25, linestyle="--")
    axes[1].text(
        0.98,
        0.98,
        "포트 평균 종목수: {active}\n코호트 평균 종목수: {selected}".format(
            active=mean_active_count_text,
            selected=mean_selected_count_text,
        ),
        transform=axes[1].transAxes,
        ha="right",
        va="top",
        fontsize=info_fontsize,
        bbox={"facecolor": "white", "alpha": 0.8, "edgecolor": "none"},
    )

    portfolio_label = str(meta.get("pattern", "Portfolio"))
    axes[2].plot(out.index, out["wealth"], color="#067BC2", linewidth=2.0, label=portfolio_label)
    kospi_curve = out.attrs.get("kospi_reference_curve")
    if kospi_curve is None:
        market_curves = out.attrs.get("market_reference_curves")
        if isinstance(market_curves, dict):
            kospi_curve = market_curves.get("KOSPI")
    if show_kospi:
        _plot_kospi_reference_curve(axes[2], out.index, kospi_curve)
    axes[2].set_yscale("log")
    axes[2].set_title("Wealth (Log Scale)", fontsize=title_fontsize)
    axes[2].tick_params(axis="both", labelsize=tick_fontsize)
    if not created_axes:
        axes[2].yaxis.tick_left()
        axes[2].yaxis.set_label_position("left")
        axes[2].tick_params(
            axis="y",
            labelright=False,
            right=False,
            labelleft=True,
            left=True,
            pad=4,
        )
    axes[2].grid(alpha=0.25, linestyle="--")
    axes[2].legend(loc="lower right", fontsize=legend_fontsize, frameon=True)
    axes[2].text(
        0.02,
        0.98,
        "CAGR: {cagr}\nMDD: {mdd}\n연변동성: {vol}\nIR: {ir}\n평균 노출도: {exposure}\n회전율(연환산): {turnover}\n승률(코호트): {win}\n손익비(코호트): {payoff}\n투자일 비중: {active}".format(
            cagr=cagr_text,
            mdd=max_drawdown_text,
            vol=ann_vol_text,
            ir=ir_text,
            exposure=mean_exposure_text,
            turnover=turnover_text,
            win=win_text,
            payoff=payoff_text,
            active=active_text,
        ),
        transform=axes[2].transAxes,
        ha="left",
        va="top",
        fontsize=info_fontsize,
        bbox={"facecolor": "white", "alpha": 0.8, "edgecolor": "none"},
    )

    if created_axes:
        fig.subplots_adjust(top=0.92)
    if return_handles:
        return fig, axes
    if created_axes:
        plt.show()
    return None


def plot_backtest(
    backtest: Backtest,
    *,
    patterns: Iterable[str] | None = None,
    start=None,
    end=None,
    figsize=(14.5, 8.0),
    annualized: bool = False,
    cost: bool | None = None,
    rise_ylim=None,
    return_ylim=None,
    show_kospi: bool = False,
    hspace: float = 0.2,
    wspace: float = 0.7,
    return_handles: bool = False,
):
    """
    마지막 analyze()/run() 결과를 한 figure에 결합해 그린다.
    """

    stats_collection = backtest._require_last_stats_collection()
    simulator = backtest._require_last_simulator()

    _configure_plot_font()
    plt, _, _, _ = _plot_modules()
    fig = plt.figure(figsize=figsize, constrained_layout=False)
    grid = fig.add_gridspec(
        2,
        12,
        height_ratios=[1.0, 1.15],
        hspace=float(hspace),
        wspace=float(wspace),
    )
    stats_axes = np.asarray(
        [fig.add_subplot(grid[0, i * 3 : (i + 1) * 3]) for i in range(4)],
        dtype=object,
    )
    simulator_axes = np.asarray(
        [fig.add_subplot(grid[1, i * 4 : (i + 1) * 4]) for i in range(3)],
        dtype=object,
    )

    plot_stats_collection(
        stats_collection,
        patterns=patterns,
        start=start,
        end=end,
        annualized=annualized,
        cost=cost,
        rise_ylim=rise_ylim,
        return_ylim=return_ylim,
        axes=stats_axes,
    )
    plot_simulator(
        simulator,
        show_kospi=show_kospi,
        axes=simulator_axes,
    )
    fig.subplots_adjust(
        left=0.045,
        right=0.99,
        top=0.96,
        bottom=0.08,
        hspace=float(hspace),
        wspace=float(wspace),
    )

    if return_handles:
        return fig, {"stats": stats_axes, "simulator": simulator_axes}
    return None


__all__ = [
    "plot_backtest",
    "plot_simulator",
    "plot_stats_collection",
    "plot_stats_history",
]
