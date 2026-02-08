from __future__ import annotations

from pathlib import Path
import time

import FinanceDataReader as fdr
import pandas as pd
from tqdm.auto import tqdm


class Collector:
    def __init__(self) -> None:
        root = Path(__file__).resolve().parents[1]
        self.static_dir = root / "static"
        self.data_dir = root / "data"
        self.univ_path = self.static_dir / "code_name.pkl"

    @staticmethod
    def _normalize_code_index(index: pd.Index) -> pd.Index:
        s = pd.Series(index.astype(str), index=index, copy=False).str.strip().str.upper()
        # code_name.pkl은 종종 "A005930" 형태라 접두사 A만 제거한다.
        s = s.str.replace(r"^A(?=[0-9A-Z]{6}$)", "", regex=True)
        numeric_mask = s.str.fullmatch(r"\d+")
        s.loc[numeric_mask] = s.loc[numeric_mask].str.zfill(6)
        return pd.Index(s.to_numpy())

    @staticmethod
    def _pick_column(df: pd.DataFrame, candidates: list[str]) -> str | None:
        columns = set(df.columns.astype(str))
        for col in candidates:
            if col in columns:
                return col
        return None

    def _read_daily_krx(
        self,
        code: str,
        start: str,
        end: str,
    ) -> pd.DataFrame:
        errors: list[str] = []
        for source in ("KRX", "KRX-DELISTING"):
            for attempt in range(3):
                try:
                    df = fdr.DataReader(f"{source}:{code}", start, end)
                    if df is not None and not df.empty:
                        return df
                    errors.append(f"{source}:{code}: empty")
                    break
                except Exception as exc:
                    msg = str(exc)
                    lower_msg = msg.lower()
                    not_supported = "not supported" in lower_msg
                    retryable = (
                        "403" in lower_msg
                        or "forbidden" in lower_msg
                        or "expecting value" in lower_msg
                        or "connection" in lower_msg
                        or "timeout" in lower_msg
                    )
                    if retryable and (not not_supported) and attempt < 2:
                        time.sleep(0.7 * (attempt + 1))
                        continue
                    errors.append(f"{source}:{code}: {msg}")
                    break

        raise ValueError("; ".join(errors))

    def univ(self) -> pd.Series:
        """
        static/code_name.pkl(코드->종목명)에서 유니버스를 읽고, 종목명에 '스팩'이 포함된 종목을 제외한다.
        """
        if not self.univ_path.exists():
            raise FileNotFoundError(f"유니버스 파일이 없습니다: {self.univ_path}")

        obj = pd.read_pickle(self.univ_path)
        if not isinstance(obj, pd.Series):
            raise TypeError(f"{self.univ_path}는 pandas Series여야 합니다.")

        out = obj.copy()
        out.index = self._normalize_code_index(out.index)
        invalid = ~pd.Series(out.index.astype(str)).str.fullmatch(r"[0-9A-Z]{6}")
        if bool(invalid.any()):
            bad = pd.Index(out.index[invalid.to_numpy()]).astype(str).tolist()[:10]
            raise ValueError(f"유효하지 않은 종목코드가 있습니다: {bad}")
        out = out[~out.index.duplicated(keep="first")]
        out = out[~out.astype(str).str.contains("스팩", na=False)]
        out.name = "name"
        return out.sort_index()

    def _standardize_daily_frame(self, raw: pd.DataFrame) -> pd.DataFrame:
        """
        FDR 일봉 데이터를 표준 컬럼으로 정리한다.
        결과 컬럼:
        Open, High, Low, Close, Volume, Amount, MarCap, Shares
        """
        if raw is None or raw.empty:
            return pd.DataFrame(
                columns=["Open", "High", "Low", "Close", "Volume", "Amount", "MarCap", "Shares"]
            )

        df = raw.copy()
        df.index = pd.to_datetime(df.index, errors="coerce")
        df = df[~df.index.isna()].sort_index()

        out = pd.DataFrame(index=df.index)
        col_map = {
            "Open": ["Open", "시가"],
            "High": ["High", "고가"],
            "Low": ["Low", "저가"],
            "Close": ["Close", "종가"],
            "Volume": ["Volume", "거래량"],
            "Amount": ["Amount", "거래대금", "Turnover"],
            "MarCap": ["MarCap", "MarketCap", "시가총액"],
            "Shares": ["Shares", "Stocks", "상장주식수", "주식수"],
        }

        for target, candidates in col_map.items():
            src = self._pick_column(df, candidates)
            if src is None:
                out[target] = pd.NA
            else:
                out[target] = pd.to_numeric(df[src], errors="coerce")

        return out

    def _collect_one(
        self,
        item_code: str,
        start_ts: pd.Timestamp,
        end_ts: pd.Timestamp,
        start_str: str,
        end_str: str,
        overwrite: bool,
    ) -> str | None:
        pkl_path = self.data_dir / f"{item_code}.pkl.xz"
        if pkl_path.exists() and not overwrite:
            return "cached"

        raw = self._read_daily_krx(item_code, start_str, end_str)
        daily = self._standardize_daily_frame(raw)
        if daily.empty:
            return "조회 데이터가 비어 있습니다"

        daily = daily.loc[(daily.index >= start_ts) & (daily.index <= end_ts)]
        if daily.empty:
            return "기간 필터 후 데이터가 비어 있습니다"

        daily.to_pickle(pkl_path, compression="xz")
        return None

    @staticmethod
    def _is_retryable_error(msg: str) -> bool:
        lower = msg.lower()
        return (
            "403" in lower
            or "forbidden" in lower
            or "period is up to 2 years" in lower
            or "expecting value" in lower
            or "connection" in lower
            or "timeout" in lower
        )

    def get(
        self,
        start: str | pd.Timestamp = "2000-01-01",
        end: str | pd.Timestamp | None = None,
        code: str | None = None,
        univ_slice: slice | None = None,
        overwrite: bool = False,
        retry_failed: int = 2,
        retry_sleep: float = 1.0,
    ) -> None:
        """
        유니버스 전종목을 순회하며 FDR(KRX 소스) 일봉 데이터를 수집해 저장한다.

        저장:
        - pickle: data/{code}.pkl.xz (xz 압축)
        - 저장 과정에서 오류가 발생하면 즉시 예외를 발생시킨다.
        """
        start_ts = pd.Timestamp(start).normalize()
        end_ts = pd.Timestamp.today().normalize() if end is None else pd.Timestamp(end).normalize()
        if start_ts > end_ts:
            raise ValueError("start는 end보다 이전이어야 합니다.")
        if retry_failed < 0:
            raise ValueError("retry_failed는 0 이상이어야 합니다.")
        if retry_sleep < 0:
            raise ValueError("retry_sleep은 0 이상이어야 합니다.")

        self.data_dir.mkdir(parents=True, exist_ok=True)
        if code is None:
            universe = self.univ()
            if univ_slice is not None:
                universe = universe.iloc[univ_slice]
        else:
            one_code = self._normalize_code_index(pd.Index([code]))[0]
            universe = pd.Series({one_code: one_code}, name="name")

        start_str = start_ts.strftime("%Y-%m-%d")
        end_str = end_ts.strftime("%Y-%m-%d")
        item_codes = [item_code for item_code, _ in universe.items()]
        cached_count = 0

        def run_batch(codes: list[str], desc: str) -> tuple[list[tuple[str, str]], int]:
            batch_errors: list[tuple[str, str]] = []
            batch_cached = 0

            for item_code in tqdm(codes, total=len(codes), desc=desc):
                try:
                    err = self._collect_one(
                        item_code,
                        start_ts,
                        end_ts,
                        start_str,
                        end_str,
                        overwrite,
                    )
                    if err == "cached":
                        batch_cached += 1
                        continue
                    if err is not None:
                        raise ValueError(err)
                except Exception as exc:
                    batch_errors.append((item_code, str(exc)))
                    tqdm.write(f"[collect][skip] code={item_code} error={exc}")
            return batch_errors, batch_cached

        errors, initial_cached = run_batch(item_codes, "collect")
        cached_count += initial_cached
        error_map = {c: m for c, m in errors}

        for round_idx in range(retry_failed):
            retry_codes = [c for c, m in error_map.items() if self._is_retryable_error(m)]
            if not retry_codes:
                break
            tqdm.write(f"[collect][retry] round={round_idx + 1} targets={len(retry_codes)}")
            if retry_sleep > 0:
                time.sleep(retry_sleep)

            for c in retry_codes:
                error_map.pop(c, None)
            retry_errors, _ = run_batch(retry_codes, f"retry-{round_idx + 1}")
            for c, m in retry_errors:
                error_map[c] = m

        final_errors = list(error_map.items())
        success_count = len(item_codes) - len(final_errors) - cached_count
        tqdm.write(
            f"[collect] done success={success_count} cached={cached_count} failed={len(final_errors)}"
        )

        return None
