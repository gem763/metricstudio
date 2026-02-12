"""Statistical aggregation and plotting helpers for backtests."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, Iterable, List, Tuple
import warnings

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from matplotlib import font_manager
from matplotlib.ticker import MaxNLocator, MultipleLocator, StrMethodFormatter


Horizon = Tuple[str, int]
_PLOT_FONT_CONFIGURED = False


def _configure_plot_font() -> None:
    global _PLOT_FONT_CONFIGURED
    if _PLOT_FONT_CONFIGURED:
        return

    # WSL에서는 윈도우 폰트를 직접 등록해야 인식되는 경우가 많다.
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
    # Windows/macOS/WSL에서 바로 동작하도록 한글 폰트 우선순위를 둔다.
    preferred = [
        "Malgun Gothic",   # Windows
        "AppleGothic",     # macOS
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
    # 한글 폰트 적용 시 음수 부호가 깨지는 이슈 방지
    plt.rcParams["axes.unicode_minus"] = False

    # 어떤 CJK 폰트도 없으면 경고 폭주를 막기 위해 glyph warning을 억제한다.
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


def _as_percent(values: np.ndarray) -> np.ndarray:
    return values * 100.0


def _normalize_ylim_percent(ylim):
    if ylim is None:
        return None
    lo = float(ylim[0])
    hi = float(ylim[1])
    # 소수 수익률(예: 0.3) 입력도 허용하고, 퍼센트(예: 30) 입력도 허용한다.
    if max(abs(lo), abs(hi)) <= 2.0:
        return lo * 100.0, hi * 100.0
    return lo, hi


def _apply_y_ticks(axes):
    # Return 축은 정수 눈금을 유지한다.
    for ax in axes[:2]:
        ax.yaxis.set_major_locator(MaxNLocator(integer=True))
        ax.yaxis.set_major_formatter(StrMethodFormatter("{x:.0f}"))

    # Rise Probability 축은 자연수(정수) 눈금만 보이도록 고정한다.
    if len(axes) >= 3:
        rise_ax = axes[2]
        ymin, ymax = rise_ax.get_ylim()
        if np.isfinite(ymin) and np.isfinite(ymax):
            lo, hi = (ymin, ymax) if ymin <= ymax else (ymax, ymin)
            visible_integer_ticks = int(np.floor(hi) - np.ceil(lo) + 1)
            if visible_integer_ticks < 2:
                new_lo = float(np.floor(lo))
                new_hi = float(np.ceil(hi))
                if new_hi - new_lo < 1.0:
                    new_hi = new_lo + 1.0
                if ymin <= ymax:
                    rise_ax.set_ylim(new_lo, new_hi)
                else:
                    rise_ax.set_ylim(new_hi, new_lo)

        rise_ax.yaxis.set_major_locator(MultipleLocator(1.0))
        rise_ax.yaxis.set_major_formatter(StrMethodFormatter("{x:.0f}"))


def _share_return_y_axis(axes):
    axes[1].sharey(axes[0])
    axes[1].set_ylabel("")
    axes[1].tick_params(axis="y", left=False, labelleft=False)


def _apply_date_ticks(axes, dates):
    n_points = len(dates)
    if n_points <= 0:
        return
    dt = pd.to_datetime(dates)
    span_days = max(1, int((dt[-1] - dt[0]).days))

    if span_days <= 120:
        date_fmt = "%Y-%m-%d"
    elif span_days <= 10 * 365:
        date_fmt = "%Y-%m"
    else:
        date_fmt = "%Y"

    # 축 너비(픽셀)와 라벨 길이를 같이 고려해서 라벨 개수를 동적으로 제한한다.
    fig = axes[0].figure
    fig_width_px = float(fig.get_size_inches()[0] * fig.dpi)
    axis_width_px = fig_width_px / max(1, len(axes))
    sample_len = max(4, len(dt[0].strftime(date_fmt)))
    label_px = sample_len * 8.0 + 14.0
    max_ticks_by_width = int(axis_width_px // label_px)
    target_ticks = max(2, min(8, max_ticks_by_width))

    idx = np.linspace(0, n_points - 1, num=min(target_ticks, n_points), dtype=int)
    idx = np.unique(idx)
    tick_dates = dt[idx]
    tick_labels = tick_dates.strftime(date_fmt).tolist()

    # 포맷 때문에 같은 라벨이 반복될 수 있어 중복 라벨을 제거한다.
    uniq_dates = []
    uniq_labels = []
    for tick_date, tick_label in zip(tick_dates, tick_labels):
        if uniq_labels and uniq_labels[-1] == tick_label:
            continue
        uniq_dates.append(tick_date)
        uniq_labels.append(tick_label)
    if len(uniq_dates) >= 2:
        tick_dates = uniq_dates
        tick_labels = uniq_labels

    for ax in axes:
        ax.set_xticks(tick_dates)
        ax.set_xticklabels(tick_labels)
        ax.tick_params(axis="x", labelrotation=0)


def _draw_hline_if_in_view(ax, y: float, **kwargs) -> bool:
    ymin, ymax = ax.get_ylim()
    lo, hi = (ymin, ymax) if ymin <= ymax else (ymax, ymin)
    if lo <= y <= hi:
        ax.axhline(y, **kwargs)
        # Keep the current view so the reference line does not expand autoscale.
        ax.set_ylim(ymin, ymax)
        return True
    return False


def _rolling_sum_1d(values: np.ndarray, window: int) -> np.ndarray:
    if window <= 1:
        return values.astype(np.float64, copy=True)
    cumsum = np.cumsum(values, dtype=np.float64)
    out = cumsum.copy()
    if values.shape[0] >= window:
        out[window:] = cumsum[window:] - cumsum[:-window]
    return out


def _parse_lookback(spec: str):
    text = str(spec).strip().upper()
    if len(text) < 2:
        raise ValueError(f"lookback 형식이 올바르지 않습니다: {spec}")
    unit = text[-1]
    value_text = text[:-1]
    if not value_text.isdigit():
        raise ValueError(f"lookback 형식이 올바르지 않습니다: {spec}")
    value = int(value_text)
    if value <= 0:
        raise ValueError(f"lookback 값은 1 이상이어야 합니다: {spec}")
    if unit == "D":
        return pd.Timedelta(days=value)
    if unit == "W":
        return pd.Timedelta(weeks=value)
    if unit == "M":
        return pd.DateOffset(months=value)
    if unit == "Y":
        return pd.DateOffset(years=value)
    raise ValueError(f"지원하지 않는 lookback 단위입니다: '{spec}'. D/W/M/Y만 사용 가능합니다.")


def _lookback_start(asof: pd.Timestamp, lookback: str) -> pd.Timestamp:
    return asof - _parse_lookback(lookback)


@dataclass
class Stats:
    dates: np.ndarray
    horizons: List[Horizon]
    counts: np.ndarray
    sum_ret: np.ndarray
    sum_log: np.ndarray
    pos_counts: np.ndarray
    geom_invalid: np.ndarray
    occurrence_counts: np.ndarray
    aggregation_mode: str = "event"
    daily_arith: np.ndarray | None = None
    daily_geom: np.ndarray | None = None
    daily_rise: np.ndarray | None = None

    @classmethod
    def create(cls, dates: np.ndarray, horizons: Iterable[Horizon]) -> "Stats":
        horizon_list = list(horizons)
        length = len(dates)
        num_h = len(horizon_list)
        return cls(
            dates=dates.copy(),
            horizons=horizon_list,
            counts=np.zeros((num_h, length), dtype=np.int64),
            sum_ret=np.zeros((num_h, length), dtype=np.float64),
            sum_log=np.zeros((num_h, length), dtype=np.float64),
            pos_counts=np.zeros((num_h, length), dtype=np.int64),
            geom_invalid=np.zeros((num_h, length), dtype=np.bool_),
            occurrence_counts=np.zeros(length, dtype=np.int64),
            aggregation_mode="event",
            daily_arith=None,
            daily_geom=None,
            daily_rise=None,
        )

    @classmethod
    def create_daily(cls, dates: np.ndarray, horizons: Iterable[Horizon]) -> "Stats":
        horizon_list = list(horizons)
        length = len(dates)
        num_h = len(horizon_list)
        return cls(
            dates=dates.copy(),
            horizons=horizon_list,
            counts=np.zeros((num_h, length), dtype=np.int64),
            sum_ret=np.zeros((num_h, length), dtype=np.float64),
            sum_log=np.zeros((num_h, length), dtype=np.float64),
            pos_counts=np.zeros((num_h, length), dtype=np.int64),
            geom_invalid=np.zeros((num_h, length), dtype=np.bool_),
            occurrence_counts=np.zeros(length, dtype=np.int64),
            aggregation_mode="daily_mean",
            daily_arith=np.full((num_h, length), np.nan, dtype=np.float64),
            daily_geom=np.full((num_h, length), np.nan, dtype=np.float64),
            daily_rise=np.full((num_h, length), np.nan, dtype=np.float64),
        )

    def _slice_indices(self, start=None, end=None) -> Tuple[int, int]:
        dates = self.dates
        total = len(dates)
        if start is None:
            start_idx = 0
        else:
            start_ts = np.datetime64(pd.Timestamp(start).to_datetime64())
            start_idx = int(np.searchsorted(dates, start_ts, side="left"))
        if end is None:
            end_idx = total
        else:
            end_ts = np.datetime64(pd.Timestamp(end).to_datetime64())
            end_idx = int(np.searchsorted(dates, end_ts, side="right"))
        start_idx = max(0, min(start_idx, total))
        end_idx = max(start_idx, min(end_idx, total))
        return start_idx, end_idx

    def occurrence(
        self,
        start=None,
        end=None,
        ma_window: int | None = None,
    ) -> pd.DataFrame:
        start_idx, end_idx = self._slice_indices(start, end)
        dates = pd.to_datetime(self.dates[start_idx:end_idx])
        occ_full = self.occurrence_counts.astype(np.float64, copy=False)
        occ = occ_full[start_idx:end_idx]

        data = {
            "occurrence": occ,
        }
        if ma_window is not None:
            window = max(1, int(ma_window))
            ma_full = pd.Series(occ_full).rolling(window=window, min_periods=window).mean().to_numpy()
            ma = ma_full[start_idx:end_idx]
            data["occurrence_ma"] = ma

        out = pd.DataFrame(data, index=dates)
        out.index.name = "date"
        return out

    def to_frame(self, start=None, end=None) -> pd.DataFrame:
        start_idx, end_idx = self._slice_indices(start, end)
        rows = []
        if start_idx >= end_idx:
            scope_label = "empty"
        else:
            if start is None and end is None:
                scope_label = "overall"
            else:
                start_date = pd.Timestamp(self.dates[start_idx]).date()
                end_date = pd.Timestamp(self.dates[end_idx - 1]).date()
                scope_label = f"{start_date}~{end_date}"

        if self.aggregation_mode == "daily_mean":
            if self.daily_arith is None or self.daily_geom is None or self.daily_rise is None:
                raise ValueError("daily_mean 모드에서는 daily metric 배열이 필요합니다.")
            for h_idx, (label, _) in enumerate(self.horizons):
                day_arith = self.daily_arith[h_idx, start_idx:end_idx]
                day_geom = self.daily_geom[h_idx, start_idx:end_idx]
                day_rise = self.daily_rise[h_idx, start_idx:end_idx]

                valid_arith = np.isfinite(day_arith)
                valid_geom = np.isfinite(day_geom)
                valid_rise = np.isfinite(day_rise)

                cnt = float(valid_arith.sum())
                arith = float(np.nanmean(day_arith)) if valid_arith.any() else float("nan")
                geom = float(np.nanmean(day_geom)) if valid_geom.any() else float("nan")
                rise = float(np.nanmean(day_rise)) if valid_rise.any() else float("nan")

                rows.append(
                    {
                        "period": label,
                        "scope": scope_label,
                        "count": cnt,
                        "arith_mean": arith,
                        "geom_mean": geom,
                        "rise_prob": rise,
                    }
                )

            if not rows:
                return pd.DataFrame(
                    columns=["period", "scope", "count", "arith_mean", "geom_mean", "rise_prob"]
                ).set_index(["period", "scope"])
            return pd.DataFrame(rows).set_index(["period", "scope"])

        for h_idx, (label, _) in enumerate(self.horizons):
            cnt = float(self.counts[h_idx, start_idx:end_idx].sum())
            sum_ret = float(self.sum_ret[h_idx, start_idx:end_idx].sum())
            sum_log = float(self.sum_log[h_idx, start_idx:end_idx].sum())
            pos = float(self.pos_counts[h_idx, start_idx:end_idx].sum())
            invalid = bool(self.geom_invalid[h_idx, start_idx:end_idx].any())

            if cnt == 0:
                rows.append(
                    {
                        "period": label,
                        "scope": scope_label,
                        "count": 0.0,
                        "arith_mean": float("nan"),
                        "geom_mean": float("nan"),
                        "rise_prob": float("nan"),
                    }
                )
                continue

            arith = sum_ret / cnt
            if invalid:
                geom = float("nan")
            else:
                geom = float(np.exp(sum_log / cnt) - 1.0)
            rise = pos / cnt
            rows.append(
                {
                    "period": label,
                    "scope": scope_label,
                    "count": cnt,
                    "arith_mean": arith,
                    "geom_mean": geom,
                    "rise_prob": rise,
                }
            )

        if not rows:
            return pd.DataFrame(
                columns=["period", "scope", "count", "arith_mean", "geom_mean", "rise_prob"]
            ).set_index(["period", "scope"])
        return pd.DataFrame(rows).set_index(["period", "scope"])

    def to_frame_history(
        self,
        horizon: str | int = "1M",
        start=None,
        end=None,
        history_window: int = 252,
        min_count: int = 30,
        require_full_window: bool = True,
    ) -> pd.DataFrame:
        start_idx, end_idx = self._slice_indices(start, end)
        if start_idx >= end_idx:
            raise ValueError("지정한 구간에 데이터가 없습니다.")

        if isinstance(horizon, int):
            h_idx = horizon
            if h_idx < 0 or h_idx >= len(self.horizons):
                raise ValueError(f"horizon 인덱스가 올바르지 않습니다: {horizon}")
        else:
            labels = [label for label, _ in self.horizons]
            if horizon not in labels:
                raise ValueError(f"알 수 없는 horizon 입니다: {horizon}")
            h_idx = labels.index(horizon)

        window = max(1, int(history_window))

        if self.aggregation_mode == "daily_mean":
            if self.daily_arith is None or self.daily_geom is None or self.daily_rise is None:
                raise ValueError("daily_mean 모드에서는 daily metric 배열이 필요합니다.")

            day_arith = self.daily_arith[h_idx]
            day_geom = self.daily_geom[h_idx]
            day_rise = self.daily_rise[h_idx]

            arith_full = pd.Series(day_arith).rolling(window=window, min_periods=1).mean().to_numpy()
            geom_full = pd.Series(day_geom).rolling(window=window, min_periods=1).mean().to_numpy()
            rise_full = pd.Series(day_rise).rolling(window=window, min_periods=1).mean().to_numpy()
            cnt_full = (
                pd.Series(np.isfinite(day_arith).astype(np.float64))
                .rolling(window=window, min_periods=1)
                .sum()
                .to_numpy()
            )

            dates = self.dates[start_idx:end_idx]
            arith = arith_full[start_idx:end_idx]
            geom = geom_full[start_idx:end_idx]
            rise = rise_full[start_idx:end_idx]
            roll_counts = cnt_full[start_idx:end_idx]

            support = roll_counts >= max(1, int(min_count))
            if require_full_window:
                global_idx = np.arange(start_idx, end_idx)
                support &= global_idx >= (window - 1)

            out = pd.DataFrame(
                {
                    "horizon": self.horizons[h_idx][0],
                    "count": roll_counts,
                    "arith_mean": np.where(support, arith, np.nan),
                    "geom_mean": np.where(support, geom, np.nan),
                    "rise_prob": np.where(support, rise, np.nan),
                },
                index=pd.to_datetime(dates),
            )
            out.index.name = "date"
            return out

        counts = self.counts[h_idx].astype(np.float64)
        sum_ret = self.sum_ret[h_idx]
        sum_log = self.sum_log[h_idx]
        pos = self.pos_counts[h_idx].astype(np.float64)
        invalid = self.geom_invalid[h_idx]

        roll_counts = _rolling_sum_1d(counts, window)
        roll_sum_ret = _rolling_sum_1d(sum_ret, window)
        roll_sum_log = _rolling_sum_1d(sum_log, window)
        roll_pos = _rolling_sum_1d(pos, window)
        roll_invalid = _rolling_sum_1d(invalid.astype(np.float64), window) > 0.0

        dates = self.dates[start_idx:end_idx]
        roll_counts = roll_counts[start_idx:end_idx]
        roll_sum_ret = roll_sum_ret[start_idx:end_idx]
        roll_sum_log = roll_sum_log[start_idx:end_idx]
        roll_pos = roll_pos[start_idx:end_idx]
        roll_invalid = roll_invalid[start_idx:end_idx]

        arith = np.full_like(roll_sum_ret, np.nan, dtype=np.float64)
        geom = np.full_like(roll_sum_ret, np.nan, dtype=np.float64)
        rise = np.full_like(roll_sum_ret, np.nan, dtype=np.float64)

        valid = roll_counts > 0
        arith[valid] = roll_sum_ret[valid] / roll_counts[valid]
        rise[valid] = roll_pos[valid] / roll_counts[valid]

        valid_geom = valid & (~roll_invalid)
        geom[valid_geom] = np.exp(roll_sum_log[valid_geom] / roll_counts[valid_geom]) - 1.0

        support = roll_counts >= max(1, int(min_count))
        if require_full_window:
            global_idx = np.arange(start_idx, end_idx)
            support &= global_idx >= (window - 1)
        out = pd.DataFrame(
            {
                "horizon": self.horizons[h_idx][0],
                "count": roll_counts,
                "arith_mean": np.where(support, arith, np.nan),
                "geom_mean": np.where(support, geom, np.nan),
                "rise_prob": np.where(support, rise, np.nan),
            },
            index=pd.to_datetime(dates),
        )
        out.index.name = "date"
        return out


@dataclass
class StatsCollection:
    stats_map: Dict[str, Stats]
    benchmark_names: set[str] = field(default_factory=set)

    def _ordered_pattern_names(self, patterns: Iterable[str] | None = None) -> List[str]:
        if patterns is None:
            names = list(self.stats_map.keys())
        else:
            names = list(patterns)

        # Drop duplicates while preserving first appearance.
        deduped: List[str] = []
        seen: set[str] = set()
        for name in names:
            if name in seen:
                continue
            deduped.append(name)
            seen.add(name)

        benchmarks = [name for name in deduped if name in self.benchmark_names]
        non_benchmarks = [name for name in deduped if name not in self.benchmark_names]
        return [*benchmarks, *non_benchmarks]

    @staticmethod
    def _apply_legend_order(ax, names: Iterable[str]) -> None:
        handles, labels = ax.get_legend_handles_labels()
        handle_by_label = {label: handle for handle, label in zip(handles, labels)}
        ordered_labels = [name for name in names if name in handle_by_label]
        if not ordered_labels:
            return
        ordered_handles = [handle_by_label[label] for label in ordered_labels]
        ax.legend(ordered_handles, ordered_labels)

    def _pattern_colors(self, names: Iterable[str]) -> Dict[str, str]:
        palette = [
            "#D56062",
            "#067BC2",
            "#F37748",
            "#84BCDA",
            "#ECC30B",
        ]
        colors = iter(palette)
        mapping: Dict[str, str] = {}
        for name in names:
            if name in self.benchmark_names or name in {"market", "benchmark", "default"}:
                mapping[name] = "black"
                continue
            color = next(colors, None)
            if color is None:
                colors = iter(palette)
                color = next(colors)
            mapping[name] = color
        return mapping

    def patterns(self) -> List[str]:
        return list(self.stats_map.keys())

    def get(self, name: str) -> Stats:
        if name not in self.stats_map:
            raise KeyError(f"알 수 없는 패턴입니다: {name}")
        return self.stats_map[name]

    def to_frame(self, start=None, end=None, pattern: str | None = None) -> pd.DataFrame:
        if not self.stats_map:
            return pd.DataFrame(
                columns=["period", "scope", "count", "arith_mean", "geom_mean", "rise_prob"]
            ).set_index(["period", "scope"])

        if pattern is not None:
            return self.get(pattern).to_frame(start, end)

        frames = []
        keys = []
        for name, stats in self.stats_map.items():
            frames.append(stats.to_frame(start, end))
            keys.append(name)
        combined = pd.concat(frames, keys=keys, names=["pattern"])
        return combined

    def occurrence(
        self,
        start=None,
        end=None,
        ma_window: int | None = None,
        pattern: str | None = None,
    ) -> pd.DataFrame:
        cols = ["occurrence"] if ma_window is None else ["occurrence", "occurrence_ma"]
        if not self.stats_map:
            return pd.DataFrame(columns=cols)

        if pattern is not None:
            return self.get(pattern).occurrence(
                start=start,
                end=end,
                ma_window=ma_window,
            )

        frames = []
        keys = []
        for name, stats in self.stats_map.items():
            frames.append(
                stats.occurrence(
                    start=start,
                    end=end,
                    ma_window=ma_window,
                )
            )
            keys.append(name)
        return pd.concat(frames, keys=keys, names=["pattern"])

    def to_frame_history(
        self,
        horizon: str | int = "1M",
        start=None,
        end=None,
        history_window: int = 252,
        min_count: int = 30,
        require_full_window: bool = True,
        pattern: str | None = None,
    ) -> pd.DataFrame:
        if not self.stats_map:
            return pd.DataFrame(
                columns=["horizon", "count", "arith_mean", "geom_mean", "rise_prob"]
            )

        if pattern is not None:
            return self.get(pattern).to_frame_history(
                horizon=horizon,
                start=start,
                end=end,
                history_window=history_window,
                min_count=min_count,
                require_full_window=require_full_window,
            )

        frames = []
        keys = []
        for name, stats in self.stats_map.items():
            frames.append(
                stats.to_frame_history(
                    horizon=horizon,
                    start=start,
                    end=end,
                    history_window=history_window,
                    min_count=min_count,
                    require_full_window=require_full_window,
                )
            )
            keys.append(name)
        return pd.concat(frames, keys=keys, names=["pattern"])

    def plot(
        self,
        patterns: Iterable[str] | None = None,
        start=None,
        end=None,
        figsize=(12, 4),
        rise_ylim=None,
        return_ylim=None,
        return_handles: bool = False,
    ):
        _configure_plot_font()
        if not self.stats_map:
            raise ValueError("StatsCollection이 비어 있습니다.")

        names = self._ordered_pattern_names(patterns)
        if not names:
            raise ValueError("플롯할 패턴이 선택되지 않았습니다.")

        color_map = self._pattern_colors(names)
        frames = []
        for name in names:
            df = self.get(name).to_frame(start, end).reset_index()
            df["pattern"] = name
            frames.append(df)
        combined = pd.concat(frames, ignore_index=True)

        periods = combined["period"].unique().tolist()
        x = np.arange(len(periods))
        period_index = {label: idx for idx, label in enumerate(periods)}

        fig, axes = plt.subplots(1, 3, figsize=figsize, constrained_layout=True)

        for name in names:
            group = combined.loc[combined["pattern"] == name]
            if group.empty:
                continue
            color = color_map.get(name, None)
            xs = group["period"].map(period_index).to_numpy(dtype=float)
            axes[0].plot(xs, group["arith_mean"] * 100.0, marker="o", color=color, label=name)
            axes[1].plot(
                xs, group["geom_mean"] * 100.0, marker="o", linestyle="-", color=color, label=name
            )
            axes[2].plot(xs, group["rise_prob"] * 100.0, marker="o", color=color, label=name)

        for ax, title, ylabel, draw_zero in [
            (axes[0], "Arithmetic Mean", "Return (%)", True),
            (axes[1], "Geometric Mean", "Return (%)", True),
            (axes[2], "Rise Probability (%)", "Rise Probability (%)", False),
        ]:
            if draw_zero:
                ax.axhline(0.0, color="gray", linewidth=0.8, linestyle="--")
            ax.set_xticks(x)
            ax.set_xticklabels(periods, rotation=0)
            ax.set_title(title)

        axes[0].set_ylabel("Return (%)")
        axes[2].set_ylabel("")
        self._apply_legend_order(axes[0], names)

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

        _share_return_y_axis(axes)
        _apply_y_ticks(axes)

        if return_handles:
            return fig, axes
        return None

    def plot_compare(
        self,
        asof,
        short: str = "1Y",
        long: str = "3Y",
        patterns: Iterable[str] | None = None,
        figsize=(12, 4),
        rise_ylim=None,
        return_ylim=None,
        return_handles: bool = False,
    ):
        asof_ts = pd.Timestamp(asof)
        short_start = _lookback_start(asof_ts, short)
        long_start = _lookback_start(asof_ts, long)
        if long_start >= short_start:
            raise ValueError("`long` 기간은 `short` 기간보다 길어야 합니다.")

        short_result = self.plot(
            patterns=patterns,
            start=short_start,
            end=asof_ts,
            figsize=figsize,
            rise_ylim=rise_ylim,
            return_ylim=return_ylim,
            return_handles=True,
        )
        long_result = self.plot(
            patterns=patterns,
            start=long_start,
            end=asof_ts,
            figsize=figsize,
            rise_ylim=rise_ylim,
            return_ylim=return_ylim,
            return_handles=True,
        )

        fig_short, _ = short_result
        fig_long, _ = long_result
        fig_short.suptitle(f"Short ({short}): {short_start.date()} ~ {asof_ts.date()}")
        fig_long.suptitle(f"Long ({long}): {long_start.date()} ~ {asof_ts.date()}")

        if return_handles:
            return short_result, long_result
        return None

    def plot_occurrence(
        self,
        patterns: Iterable[str] | None = None,
        start=None,
        end=None,
        figsize=(3, 3),
        ma_window: int | None = 252,
        ylim=None,
        show_daily: bool = False,
        return_handles: bool = False,
    ):
        _configure_plot_font()
        if not self.stats_map:
            raise ValueError("StatsCollection이 비어 있습니다.")

        names = self._ordered_pattern_names(patterns)
        if not names:
            raise ValueError("플롯할 패턴이 선택되지 않았습니다.")

        color_map = self._pattern_colors(names)
        fig, ax = plt.subplots(1, 1, figsize=figsize, constrained_layout=True)

        first_dates = None
        for name in names:
            df = self.occurrence(
                start=start,
                end=end,
                ma_window=ma_window,
                pattern=name,
            )
            dates = df.index.to_numpy()
            if first_dates is None:
                first_dates = dates
            elif not np.array_equal(first_dates, dates):
                raise ValueError("plot_occurrence에서는 모든 패턴이 동일한 날짜 인덱스를 가져야 합니다.")

            color = color_map.get(name, None)
            if show_daily:
                ax.plot(
                    dates,
                    df["occurrence"].to_numpy(dtype=float),
                    color=color,
                    alpha=0.2,
                    linewidth=1.0,
                )
            line_vals = (
                df["occurrence_ma"].to_numpy(dtype=float)
                if ma_window is not None
                else df["occurrence"].to_numpy(dtype=float)
            )
            ax.plot(
                dates,
                line_vals,
                color=color,
                linewidth=2.0,
                label=name,
            )

        if ma_window is None:
            ax.set_title("Pattern Occurrence")
        else:
            ax.set_title(f"Pattern Occurrence (Rolling {int(ma_window)}D Mean)")
        ax.set_ylabel("Daily Occurrence Count")
        self._apply_legend_order(ax, names)
        if ylim is not None:
            ax.set_ylim(float(ylim[0]), float(ylim[1]))

        if first_dates is not None:
            _apply_date_ticks([ax], first_dates)
        ax.tick_params(axis="x", labelrotation=0)

        if return_handles:
            return fig, ax
        return None

    def plot_history(
        self,
        horizon: str | int = "1M",
        patterns: Iterable[str] | None = None,
        start=None,
        end=None,
        figsize=(12, 4),
        history_window: int = 252,
        min_count: int = 30,
        require_full_window: bool = True,
        rise_ylim=None,
        return_ylim=None,
        return_handles: bool = False,
    ):
        _configure_plot_font()
        if not self.stats_map:
            raise ValueError("StatsCollection이 비어 있습니다.")

        names = self._ordered_pattern_names(patterns)
        if not names:
            raise ValueError("플롯할 패턴이 선택되지 않았습니다.")

        color_map = self._pattern_colors(names)
        fig, axes = plt.subplots(1, 3, figsize=figsize, constrained_layout=True, sharex=True)

        first_dates = None
        label = None
        arith_series = []
        geom_series = []
        for name in names:
            df = self.to_frame_history(
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

        self._apply_legend_order(axes[0], names)

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
