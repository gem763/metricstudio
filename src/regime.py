from __future__ import annotations

from types import SimpleNamespace

import numpy as np
import pandas as pd


TRADING_DAYS_PER_YEAR = 240

REGIME_TREND = "trend_friendly"
REGIME_CONTRARIAN = "contrarian_friendly"
REGIME_PANIC = "panic_rebound_risk"
REGIME_NEUTRAL = "neutral"
REGIME_QUIET_TAG = "quiet_tag"
REGIME_NARROW_TAG = "narrow_tag"

REGIME_QUIET = "quiet_squeeze_expansion"
REGIME_BROAD_BULL = "broad_bull_breakout"
REGIME_NARROW = "narrow_leadership"
REGIME_SIDEWAYS = "sideways_choppy"
REGIME_UNAVAILABLE = "unavailable"


def _normalize_regime_kind(kind: str) -> str:
    key = str(kind or "").strip().lower()
    alias_map = {
        "trend": REGIME_TREND,
        "trend_friendly": REGIME_TREND,
        "trend_follower": REGIME_TREND,
        "contrarian": REGIME_CONTRARIAN,
        "contrarian_friendly": REGIME_CONTRARIAN,
        "mean_reversion": REGIME_CONTRARIAN,
        "mean_reversion_friendly": REGIME_CONTRARIAN,
        "panic": REGIME_PANIC,
        "panic_rebound_risk": REGIME_PANIC,
        "neutral": REGIME_NEUTRAL,
        "quiet_tag": REGIME_QUIET_TAG,
        "quiettag": REGIME_QUIET_TAG,
        "narrow_tag": REGIME_NARROW_TAG,
        "narrowtag": REGIME_NARROW_TAG,
        "quiet": REGIME_QUIET,
        "quiet_squeeze_expansion": REGIME_QUIET,
        "quiet_squeeze": REGIME_QUIET,
        "broad_bull_breakout": REGIME_BROAD_BULL,
        "broad_bull": REGIME_BROAD_BULL,
        "broadbull": REGIME_BROAD_BULL,
        "narrow": REGIME_NARROW,
        "narrow_leadership": REGIME_NARROW,
        "sideways": REGIME_SIDEWAYS,
        "sideways_choppy": REGIME_SIDEWAYS,
        "other": REGIME_NEUTRAL,
    }
    if key not in alias_map:
        raise ValueError(
            "kind는 "
            "{'trend_friendly', 'contrarian_friendly', 'panic_rebound_risk', 'neutral', "
            "'quiet_tag', 'narrow_tag', 'quiet_squeeze_expansion', 'broad_bull_breakout', "
            "'narrow_leadership', 'sideways_choppy'} 중 하나여야 합니다."
        )
    return alias_map[key]


def regime_mask_from_frame(frame: pd.DataFrame, kind: str) -> np.ndarray:
    """
    build_regime_frame() 결과에서 kind에 대응하는 bool 마스크를 추출한다.
    """

    key = _normalize_regime_kind(kind)
    if key in frame.columns:
        values = frame[key]
        return values.fillna(False).to_numpy(dtype=np.bool_, copy=True)
    if "label" in frame.columns:
        return frame["label"].eq(key).to_numpy(dtype=np.bool_, copy=True)
    raise KeyError(f"regime frame에서 '{key}' 컬럼을 찾을 수 없습니다.")


def _rolling_last_percentile_rank(
    series: pd.Series,
    window: int = TRADING_DAYS_PER_YEAR,
) -> pd.Series:
    values = pd.to_numeric(series, errors="coerce").to_numpy(dtype=np.float64, copy=True)
    out = np.full(values.shape[0], np.nan, dtype=np.float64)
    if window <= 0:
        raise ValueError("window는 1 이상이어야 합니다.")
    if values.shape[0] < window:
        return pd.Series(out, index=series.index, dtype="float64")

    for i in range(window - 1, values.shape[0]):
        win = values[i - window + 1 : i + 1]
        if not np.all(np.isfinite(win)):
            continue
        out[i] = float(np.mean(win <= win[-1]))
    return pd.Series(out, index=series.index, dtype="float64")


def build_market_cap_bucket_masks(
    market_cap_df: pd.DataFrame,
) -> dict[str, pd.DataFrame]:
    values = market_cap_df.apply(pd.to_numeric, errors="coerce").to_numpy(dtype=np.float64, copy=True)
    num_dates, num_codes = values.shape
    large = np.zeros((num_dates, num_codes), dtype=np.bool_)
    mid = np.zeros((num_dates, num_codes), dtype=np.bool_)
    small = np.zeros((num_dates, num_codes), dtype=np.bool_)

    for row_idx in range(num_dates):
        row = values[row_idx]
        valid_idx = np.flatnonzero(np.isfinite(row) & (row > 0.0))
        if valid_idx.size == 0:
            continue

        order = valid_idx[np.argsort(-row[valid_idx], kind="mergesort")]
        ordered_caps = row[order]
        total_cap = float(np.sum(ordered_caps))
        if not np.isfinite(total_cap) or total_cap <= 0.0:
            continue

        cumulative_share = np.cumsum(ordered_caps) / total_cap
        large_end = int(np.searchsorted(cumulative_share, 0.50, side="left"))
        mid_end = int(np.searchsorted(cumulative_share, 0.75, side="left"))

        large[row_idx, order[: large_end + 1]] = True
        if mid_end >= large_end + 1:
            mid[row_idx, order[large_end + 1 : mid_end + 1]] = True
        if mid_end + 1 < order.size:
            small[row_idx, order[mid_end + 1 :]] = True

    return {
        "large": pd.DataFrame(large, index=market_cap_df.index, columns=market_cap_df.columns),
        "mid": pd.DataFrame(mid, index=market_cap_df.index, columns=market_cap_df.columns),
        "small": pd.DataFrame(small, index=market_cap_df.index, columns=market_cap_df.columns),
    }


def _ratio_with_mask(
    cond_df: pd.DataFrame,
    valid_df: pd.DataFrame,
    extra_mask_df: pd.DataFrame | None = None,
) -> pd.Series:
    valid = valid_df.copy()
    if extra_mask_df is not None:
        valid &= extra_mask_df
    numer = (cond_df & valid).sum(axis=1)
    denom = valid.sum(axis=1)
    out = numer / denom
    return out.where(denom > 0)


def build_regime_frame(
    market_close: pd.Series,
    close_df: pd.DataFrame,
    amount_df: pd.DataFrame,
    market_cap_df: pd.DataFrame,
    percentile_window: int = TRADING_DAYS_PER_YEAR,
) -> pd.DataFrame:
    market_close = pd.to_numeric(market_close, errors="coerce").where(lambda s: s > 0.0)
    close_df = close_df.apply(pd.to_numeric, errors="coerce").where(lambda df: df > 0.0)
    amount_df = amount_df.apply(pd.to_numeric, errors="coerce").where(lambda df: df > 0.0)
    market_cap_df = pd.to_numeric(market_cap_df.stack(), errors="coerce").unstack().reindex_like(close_df)
    market_cap_df = market_cap_df.where(market_cap_df > 0.0)

    sma20 = market_close.rolling(20, min_periods=20).mean()
    sma60 = market_close.rolling(60, min_periods=60).mean()
    sma240 = market_close.rolling(TRADING_DAYS_PER_YEAR, min_periods=TRADING_DAYS_PER_YEAR).mean()
    std20 = market_close.rolling(20, min_periods=20).std(ddof=0)
    upper20 = sma20 + 2.0 * std20
    lower20 = sma20 - 2.0 * std20
    bbw20 = (upper20 - lower20) / sma20
    logret = np.log(market_close / market_close.shift(1))
    rv20 = logret.rolling(20, min_periods=20).std(ddof=0)
    dd60 = market_close / market_close.rolling(60, min_periods=60).max() - 1.0

    bbw20_pct240 = _rolling_last_percentile_rank(bbw20, percentile_window)
    rv20_pct240 = _rolling_last_percentile_rank(rv20, percentile_window)

    trend_score = (
        (market_close > sma240).fillna(False).astype(np.int8)
        + (sma240 > sma240.shift(20)).fillna(False).astype(np.int8)
        + (market_close > sma60).fillna(False).astype(np.int8)
    ).astype(np.float64)
    trend_score = trend_score.where(sma240.notna() & sma60.notna() & sma240.shift(20).notna())

    stock_sma20 = close_df.rolling(20, min_periods=20).mean()
    stock_sma60 = close_df.rolling(60, min_periods=60).mean()
    valid20 = close_df.notna() & stock_sma20.notna()
    valid60 = close_df.notna() & stock_sma60.notna()
    above20 = close_df > stock_sma20
    above60 = close_df > stock_sma60

    pct_above20_all = _ratio_with_mask(above20, valid20)
    pct_above60_all = _ratio_with_mask(above60, valid60)
    pct_above20_delta5 = pct_above20_all - pct_above20_all.shift(5)

    ret1 = close_df / close_df.shift(1) - 1.0
    valid_amount = amount_df.notna() & ret1.notna()
    advancing_amount = amount_df.where(valid_amount & (ret1 > 0.0)).sum(axis=1)
    total_amount = amount_df.where(valid_amount).sum(axis=1)
    adv_amt_ratio = advancing_amount / total_amount
    adv_amt_ratio = adv_amt_ratio.where(total_amount > 0.0)
    aar5 = adv_amt_ratio.rolling(5, min_periods=5).mean()

    size_masks = build_market_cap_bucket_masks(market_cap_df)
    large_pct_above60 = _ratio_with_mask(above60, valid60, size_masks["large"])
    mid_pct_above60 = _ratio_with_mask(above60, valid60, size_masks["mid"])
    small_pct_above60 = _ratio_with_mask(above60, valid60, size_masks["small"])
    leadership_spread = large_pct_above60 - small_pct_above60

    out = pd.DataFrame(
        {
            "market_close": market_close,
            "sma20": sma20,
            "sma60": sma60,
            "sma240": sma240,
            "bbw20": bbw20,
            "bbw20_pct240": bbw20_pct240,
            "rv20": rv20,
            "rv20_pct240": rv20_pct240,
            "dd60": dd60,
            "trend_score": trend_score,
            "pct_above20_all": pct_above20_all,
            "pct_above60_all": pct_above60_all,
            "pct_above20_delta5": pct_above20_delta5,
            "adv_amt_ratio": adv_amt_ratio,
            "aar5": aar5,
            "large_pct_above60": large_pct_above60,
            "mid_pct_above60": mid_pct_above60,
            "small_pct_above60": small_pct_above60,
            "leadership_spread": leadership_spread,
        },
        index=market_close.index,
    )

    required_cols = [
        "rv20_pct240",
        "dd60",
        "pct_above20_all",
        "trend_score",
        "bbw20_pct240",
        "pct_above20_delta5",
        "pct_above60_all",
        "aar5",
        "large_pct_above60",
        "small_pct_above60",
        "leadership_spread",
    ]
    evaluable = out[required_cols].notna().all(axis=1)

    panic_mask = evaluable & (
        (out["rv20_pct240"] >= 0.85)
        | ((out["dd60"] <= -0.10) & (out["pct_above20_all"] < 0.35))
    )
    quiet_tag = evaluable & (
        (out["bbw20_pct240"] <= 0.20)
        & (out["rv20_pct240"] <= 0.40)
    )
    narrow_tag = evaluable & (
        (out["trend_score"] >= 2.0)
        & (out["large_pct_above60"] >= 0.60)
        & (out["small_pct_above60"] <= 0.45)
        & (out["leadership_spread"] >= 0.15)
    )

    trend_quiet_branch = evaluable & (~panic_mask) & (
        (out["trend_score"] >= 2.0)
        & (out["dd60"] > -0.08)
        & (out["rv20_pct240"] < 0.80)
        & (out["bbw20_pct240"] <= 0.20)
        & (out["rv20_pct240"] <= 0.40)
        & (out["pct_above20_delta5"] >= 0.05)
        & (out["pct_above20_all"] >= 0.45)
    )
    trend_broad_branch = evaluable & (~panic_mask) & (
        (out["trend_score"] >= 2.0)
        & (out["pct_above60_all"] >= 0.50)
        & (out["aar5"] >= 0.50)
        & (out["rv20_pct240"] < 0.80)
        & (out["dd60"] > -0.08)
    )
    trend_narrow_branch = evaluable & (~panic_mask) & narrow_tag & (
        (out["dd60"] > -0.08)
        & (out["rv20_pct240"] < 0.80)
        & (out["aar5"] >= 0.45)
    )
    trend_friendly = trend_quiet_branch | trend_broad_branch | trend_narrow_branch

    contrarian_friendly = evaluable & (~panic_mask) & (~trend_friendly) & (
        (
            (out["dd60"] <= -0.05)
            & (out["pct_above20_all"] <= 0.45)
            & (out["pct_above20_delta5"] >= -0.03)
        )
        |
        (
            (out["rv20_pct240"] >= 0.55)
            & (out["pct_above20_all"] >= 0.30)
            & (out["pct_above20_all"] <= 0.50)
            & (out["aar5"] >= 0.45)
        )
    )
    neutral_mask = evaluable & (~panic_mask) & (~trend_friendly) & (~contrarian_friendly)

    legacy_quiet_mask = evaluable & (~panic_mask) & (
        (out["trend_score"] >= 1.0)
        & (out["bbw20_pct240"] <= 0.20)
        & (out["rv20_pct240"] <= 0.40)
        & (out["pct_above20_delta5"] >= 0.05)
    )
    legacy_broad_mask = evaluable & (~panic_mask) & (~legacy_quiet_mask) & (
        (out["trend_score"] >= 2.0)
        & (out["pct_above60_all"] >= 0.55)
        & (out["aar5"] >= 0.55)
        & (out["rv20_pct240"] < 0.80)
        & (out["dd60"] > -0.08)
    )
    legacy_narrow_mask = evaluable & (~panic_mask) & (~legacy_quiet_mask) & (~legacy_broad_mask) & narrow_tag
    legacy_sideways_mask = evaluable & (
        (~panic_mask) & (~legacy_quiet_mask) & (~legacy_broad_mask) & (~legacy_narrow_mask)
    )

    labels = pd.Series(REGIME_UNAVAILABLE, index=out.index, dtype="object")
    labels.loc[neutral_mask] = REGIME_NEUTRAL
    labels.loc[contrarian_friendly] = REGIME_CONTRARIAN
    labels.loc[trend_friendly] = REGIME_TREND
    labels.loc[panic_mask] = REGIME_PANIC

    legacy_labels = pd.Series(REGIME_UNAVAILABLE, index=out.index, dtype="object")
    legacy_labels.loc[legacy_sideways_mask] = REGIME_SIDEWAYS
    legacy_labels.loc[legacy_narrow_mask] = REGIME_NARROW
    legacy_labels.loc[legacy_broad_mask] = REGIME_BROAD_BULL
    legacy_labels.loc[legacy_quiet_mask] = REGIME_QUIET
    legacy_labels.loc[panic_mask] = REGIME_PANIC

    out[REGIME_TREND] = trend_friendly
    out[REGIME_CONTRARIAN] = contrarian_friendly
    out[REGIME_PANIC] = panic_mask
    out[REGIME_NEUTRAL] = neutral_mask
    out[REGIME_QUIET_TAG] = quiet_tag
    out[REGIME_NARROW_TAG] = narrow_tag
    out[REGIME_QUIET] = legacy_quiet_mask
    out[REGIME_BROAD_BULL] = legacy_broad_mask
    out[REGIME_NARROW] = legacy_narrow_mask
    out[REGIME_SIDEWAYS] = legacy_sideways_mask
    out[REGIME_UNAVAILABLE] = ~evaluable
    out["label"] = labels
    out["legacy_label"] = legacy_labels
    return out


class Regime:
    def __init__(self, name: str | None = None):
        self.name = name or "regime"
        self.params: SimpleNamespace | None = None
        self._dates: np.ndarray | None = None
        self._mask_values: np.ndarray | None = None
        self._frame: pd.DataFrame | None = None

    def on(
        self,
        kind: str = REGIME_TREND,
        market: str = "kospi",
    ):
        market_name = str(market or "").strip().lower()
        if not market_name:
            raise ValueError("market은 비어 있을 수 없습니다.")
        self.params = SimpleNamespace(
            kind=_normalize_regime_kind(kind),
            market=market_name,
        )
        return self

    @property
    def kind(self) -> str:
        if self.params is None:
            raise ValueError("Regime은 사용 전에 on(...)으로 설정해야 합니다.")
        return str(self.params.kind)

    @property
    def market(self) -> str:
        if self.params is None:
            raise ValueError("Regime은 사용 전에 on(...)으로 설정해야 합니다.")
        return str(self.params.market)

    def cache_key(self) -> tuple[str, str]:
        return self.kind, self.market

    def _bind(self, dates: np.ndarray, mask_values: np.ndarray, frame: pd.DataFrame) -> None:
        self._dates = np.asarray(dates, dtype="datetime64[ns]")
        self._mask_values = np.array(mask_values, dtype=np.bool_, copy=True)
        self._frame = frame.copy()

    def mask(self, length: int | None = None) -> np.ndarray:
        if self._mask_values is None:
            raise ValueError("Regime mask가 준비되지 않았습니다. Backtest.analyze()/run()을 먼저 실행하세요.")
        if length is not None and int(length) != int(self._mask_values.shape[0]):
            raise ValueError("Regime mask 길이가 패턴 시계열 길이와 일치하지 않습니다.")
        return np.asarray(self._mask_values, dtype=np.bool_)

    def to_frame(self, copy: bool = True) -> pd.DataFrame:
        if self._frame is None:
            raise ValueError("Regime 데이터가 준비되지 않았습니다. Backtest.analyze()/run()을 먼저 실행하세요.")
        return self._frame.copy() if copy else self._frame


__all__ = [
    "Regime",
    "REGIME_TREND",
    "REGIME_CONTRARIAN",
    "REGIME_PANIC",
    "REGIME_NEUTRAL",
    "REGIME_QUIET_TAG",
    "REGIME_NARROW_TAG",
    "REGIME_QUIET",
    "REGIME_BROAD_BULL",
    "REGIME_NARROW",
    "REGIME_SIDEWAYS",
    "REGIME_UNAVAILABLE",
    "TRADING_DAYS_PER_YEAR",
    "build_market_cap_bucket_masks",
    "build_regime_frame",
    "regime_mask_from_frame",
]
