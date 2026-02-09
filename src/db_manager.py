from __future__ import annotations

from pathlib import Path
import re
from typing import Iterable

import FinanceDataReader as fdr
import pandas as pd
from tqdm.auto import tqdm


class DB:
    def __init__(
        self,
        static_dir: str | Path | None = None,
        data_dir: str | Path | None = None,
        db_dir: str | Path | None = None,
        marcap_dir: str | Path | None = None,
        db_root_dir: str | Path | None = None,
        stock_dir: str | Path | None = None,
        stock_data_dir: str | Path | None = None,
        market_dir: str | Path | None = None,
    ) -> None:
        root = Path(__file__).resolve().parents[1]
        self.project_root = root
        self.static_dir = Path(static_dir) if static_dir is not None else root / "static"
        self.db_root_dir = Path(db_root_dir) if db_root_dir is not None else root / "db"
        if stock_dir is None:
            stock_dir = db_dir
        self.stock_dir = Path(stock_dir) if stock_dir is not None else self.db_root_dir / "stock"
        if stock_data_dir is None:
            stock_data_dir = data_dir
        self.stock_data_dir = (
            Path(stock_data_dir) if stock_data_dir is not None else self.stock_dir / "data"
        )
        self.market_dir = Path(market_dir) if market_dir is not None else self.db_root_dir / "market"
        self.marcap_dir = Path(marcap_dir) if marcap_dir is not None else root / "marcap" / "data"
        self.legacy_stock_data_dir = root / "data"
        self.legacy_stock_field_dir = self.db_root_dir
        # backward compatibility aliases
        self.data_dir = self.stock_data_dir
        self.db_dir = self.stock_dir

    # =========================
    # SHARED HELPERS
    # =========================
    @staticmethod
    def _normalize_codes(codes: Iterable[object]) -> pd.Index:
        s = pd.Series(list(codes), dtype="object").astype(str).str.strip().str.upper()
        s = s.str.replace(r"^A(?=[0-9A-Z]{6}$)", "", regex=True)
        numeric_mask = s.str.fullmatch(r"\d+")
        s.loc[numeric_mask] = s.loc[numeric_mask].str.zfill(6)
        return pd.Index(s.to_numpy())

    def _adjclose_paths(self) -> list[Path]:
        paths = sorted(self.static_dir.glob("adjclose*.pkl"))
        if not paths:
            raise FileNotFoundError(f"adjclose 파일이 없습니다: {self.static_dir}")
        return paths

    def _code_parquet_paths(self) -> list[Path]:
        candidate_dirs = [self.stock_data_dir]
        if self.legacy_stock_data_dir != self.stock_data_dir:
            candidate_dirs.append(self.legacy_stock_data_dir)

        # 신규 구조(db/stock/data) 우선, 없으면 레거시(data)를 fallback으로 본다.
        for base_dir in candidate_dirs:
            if not base_dir.exists():
                continue
            all_paths = sorted(base_dir.glob("*.parquet"))
            paths = [
                p
                for p in all_paths
                if re.fullmatch(r"[0-9A-Z]{6}\.parquet", p.name) is not None
            ]
            if paths:
                return paths

        raise FileNotFoundError(
            "종목 parquet 파일이 없습니다. "
            f"확인 경로: {', '.join(str(p) for p in candidate_dirs)}"
        )

    def _field_path(self, field: str) -> Path:
        return self.stock_dir / f"{field}.parquet"

    @staticmethod
    def _market_symbol(market: str) -> str:
        key = str(market).strip().lower()
        if not key:
            raise ValueError("market은 비어 있을 수 없습니다.")
        symbol_map = {
            "kospi": "KS11",
            "kosdaq": "KQ11",
            "kospi200": "KS200",
        }
        return symbol_map.get(key, str(market).strip())

    def _market_file_path(self, market: str) -> Path:
        safe_name = re.sub(r"[^0-9a-zA-Z_-]+", "_", str(market).strip().lower())
        if not safe_name:
            raise ValueError("market 파일명을 생성할 수 없습니다.")
        return self.market_dir / f"{safe_name}.parquet"

    @staticmethod
    def _find_column(df: pd.DataFrame, candidates: list[str]) -> str | None:
        lower_to_original = {str(col).lower(): str(col) for col in df.columns}
        for name in candidates:
            key = name.lower()
            if key in lower_to_original:
                return lower_to_original[key]
        return None

    @classmethod
    def _normalize_market_frame(cls, raw_df: pd.DataFrame) -> pd.DataFrame:
        if not isinstance(raw_df, pd.DataFrame):
            raise TypeError("시장 데이터는 pandas DataFrame이어야 합니다.")

        target_cols = ["open", "high", "low", "close", "volume", "amount", "marketcap"]
        if raw_df.empty:
            return pd.DataFrame(columns=target_cols, index=pd.DatetimeIndex([], name="date"))

        df = raw_df.copy()
        df.index = pd.to_datetime(df.index, errors="coerce")
        df = df[df.index.notna()]
        if df.empty:
            return pd.DataFrame(columns=target_cols, index=pd.DatetimeIndex([], name="date"))

        aliases: dict[str, list[str]] = {
            "open": ["Open", "시가"],
            "high": ["High", "고가"],
            "low": ["Low", "저가"],
            "close": ["Close", "종가", "Adj Close", "AdjClose"],
            "volume": ["Volume", "거래량"],
            "amount": ["Amount", "거래대금", "거래대금(원)"],
            "marketcap": ["Marcap", "MarketCap", "시가총액"],
        }

        out = pd.DataFrame(index=df.index)
        out.index.name = "date"
        for target, candidates in aliases.items():
            src_col = cls._find_column(df, candidates)
            if src_col is None:
                out[target] = pd.NA
            else:
                out[target] = pd.to_numeric(df[src_col], errors="coerce")

        out = out[target_cols].sort_index()
        out = out[~out.index.duplicated(keep="last")]
        return out

    # load()가 우선적으로 읽는 db/stock/{field}.parquet(wide) 경로
    def _read_field_store(self, field: str) -> pd.DataFrame | None:
        primary_path = self._field_path(field)
        legacy_path = self.legacy_stock_field_dir / f"{field}.parquet"

        # 신규 구조(db/stock) 우선, 없으면 레거시 구조(db 루트)를 fallback으로 본다.
        field_path: Path | None = None
        if primary_path.exists():
            field_path = primary_path
        elif legacy_path.exists() and legacy_path != primary_path:
            field_path = legacy_path

        if field_path is None:
            return None

        df = pd.read_parquet(field_path)
        if not isinstance(df, pd.DataFrame):
            raise TypeError(f"{field_path}는 pandas DataFrame이어야 합니다.")
        if df.empty:
            return pd.DataFrame()

        # 과거 long 포맷(db/date,code,field)도 읽을 수 있게 호환 처리한다.
        if {"date", "code", field}.issubset(df.columns):
            idx = pd.MultiIndex.from_arrays(
                [
                    pd.to_datetime(df["date"], errors="coerce"),
                    self._normalize_codes(df["code"]),
                ],
                names=["date", "code"],
            )
            values = pd.to_numeric(df[field], errors="coerce")
            s = pd.Series(values.to_numpy(), index=idx, name=field)
            s = s[s.index.get_level_values("date").notna()].dropna()
            wide = s.unstack("code")
            wide.index = pd.to_datetime(wide.index, errors="coerce")
            wide = wide[wide.index.notna()]
            wide.columns = self._normalize_codes(wide.columns)
            wide = wide.sort_index().sort_index(axis=1)
            return wide

        if "date" in df.columns:
            df = df.set_index("date")

        df.index = pd.to_datetime(df.index, errors="coerce")
        df = df[df.index.notna()]
        df.columns = self._normalize_codes(df.columns)
        if df.columns.has_duplicates:
            df = df.T[~df.T.index.duplicated(keep="last")].T
        return df.sort_index().sort_index(axis=1)

    @staticmethod
    def _empty_price_series(name: str) -> pd.Series:
        empty_idx = pd.MultiIndex.from_arrays(
            [
                pd.DatetimeIndex([], name="date"),
                pd.Index([], name="code", dtype="object"),
            ]
        )
        return pd.Series([], index=empty_idx, name=name, dtype="float64")

    def _code_from_path(self, path: Path) -> str:
        raw = path.name.removesuffix(".parquet")
        return self._normalize_codes([raw])[0]

    @staticmethod
    def _series_from_frame(frame: pd.DataFrame, code: str, field: str) -> pd.Series:
        dates = pd.to_datetime(frame.index, errors="coerce")
        values = pd.to_numeric(frame[field], errors="coerce")
        valid = dates.notna() & values.notna()
        if not valid.any():
            return pd.Series(dtype="float64", name=field)

        idx = pd.MultiIndex.from_arrays(
            [dates[valid], pd.Index([code] * int(valid.sum()), dtype="object")],
            names=["date", "code"],
        )
        return pd.Series(values[valid].to_numpy(), index=idx, name=field)

    def _load_one_series(self, path: Path, field: str) -> pd.Series:
        code = self._code_from_path(path)
        df = pd.read_parquet(path, columns=[field])
        if not isinstance(df, pd.DataFrame):
            raise TypeError(f"{path}는 pandas DataFrame이어야 합니다.")
        if field not in df.columns:
            raise ValueError(f"{path}에 '{field}' 컬럼이 없습니다.")
        return self._series_from_frame(df, code=code, field=field)

    # load() fallback 및 build_stock()에서 공통으로 쓰는 db/stock/data/{code}.parquet 병합 로더
    def _load_field_series_from_paths(
        self,
        paths: list[Path],
        field: str,
    ) -> pd.Series:
        if not paths:
            return self._empty_price_series(field)

        series_list: list[pd.Series] = []
        for path in paths:
            s = self._load_one_series(path, field=field)
            if not s.empty:
                series_list.append(s)

        if not series_list:
            return self._empty_price_series(field)

        merged = pd.concat(series_list).sort_index()
        dup = merged.index[merged.index.duplicated()]
        if len(dup) > 0:
            raise ValueError("stock raw 데이터 로드 결과에 중복 (date, code) 인덱스가 있습니다.")
        return merged

    @staticmethod
    def _empty_output_frame() -> pd.DataFrame:
        return pd.DataFrame(
            columns=["open", "high", "low", "close", "volume", "amount", "marketcap", "shares"]
        )

    @staticmethod
    def _filter_bad_codes(
        df: pd.DataFrame,
        max_daily_ret: float = 2.0,
        min_price: float = 1.0,
    ) -> pd.DataFrame:
        if df.empty:
            return df
        # 비정상 급등락 또는 비정상 가격(예: 1원)을 포함한 종목 제거
        daily_ret = df.pct_change()
        bad_ret = daily_ret.abs() > max_daily_ret
        bad_price = df <= min_price
        bad_codes = bad_ret.any() | bad_price.any()
        if bad_codes.any():
            return df.loc[:, ~bad_codes]
        return df

    # collect_stock() 전체 수집에서만 사용하는 adjclose 통합 로더
    def _load_adjclose(self) -> pd.Series:
        series_list: list[pd.Series] = []
        for path in self._adjclose_paths():
            s = pd.read_pickle(path)
            if not isinstance(s, pd.Series):
                raise TypeError(f"{path}는 pandas Series여야 합니다.")
            if not isinstance(s.index, pd.MultiIndex) or s.index.nlevels != 2:
                raise ValueError(f"{path} 인덱스는 (date, code) MultiIndex여야 합니다.")

            dates = pd.to_datetime(s.index.get_level_values(0), errors="coerce")
            codes = self._normalize_codes(s.index.get_level_values(1))
            idx = pd.MultiIndex.from_arrays([dates, codes], names=["date", "code"])
            values = pd.to_numeric(s, errors="coerce")
            fixed = pd.Series(values.to_numpy(), index=idx, name="adjclose").dropna()
            fixed = fixed[fixed.index.get_level_values("date").notna()]
            series_list.append(fixed)

        out = pd.concat(series_list).sort_index()
        dup = out.index[out.index.duplicated()]
        if len(dup) > 0:
            raise ValueError("adjclose에 중복 (date, code) 인덱스가 있습니다.")
        return out

    # =========================
    # LOAD PATH (READ-ONLY)
    # =========================
    def load(
        self,
        codes: Iterable[str] | str | None = None,
        field: str = "close",
        mapping_pkl: str = "code_name.pkl",
        exclude_spac: bool = True,
    ) -> pd.DataFrame:
        """
        db/stock/{field}.parquet(wide) 또는 db/stock/data/{code}.parquet를 병합해
        (date x code) wide DataFrame을 반환한다.
        field가 가격계열(open/high/low/close)일 때는
        비정상 급등락/비정상 저가 종목을 제외한다.
        """
        if isinstance(codes, str):
            requested_codes = set(self._normalize_codes([codes]).to_list())
        elif codes is None:
            requested_codes: set[str] | None = None
        else:
            requested_codes = set(self._normalize_codes(codes).to_list())

        codes_to_exclude: set[str] = set()
        if exclude_spac:
            mapping_path = self.static_dir / mapping_pkl
            if not mapping_path.exists():
                raise FileNotFoundError(f"종목명 매핑 파일이 없습니다: {mapping_path}")
            code_name = pd.read_pickle(mapping_path)
            if not isinstance(code_name, pd.Series):
                raise TypeError(f"{mapping_path}는 pandas Series여야 합니다.")
            mask = code_name.astype(str).str.contains(r"스팩", regex=True, na=False)
            if mask.any():
                excluded = self._normalize_codes(code_name.index[mask])
                codes_to_exclude = set(excluded.to_list())

        wide = self._read_field_store(field)
        if wide is None:
            all_paths = self._code_parquet_paths()
            target_paths: list[Path] = []
            for path in all_paths:
                code = self._code_from_path(path)
                if requested_codes is not None and code not in requested_codes:
                    continue
                if code in codes_to_exclude:
                    continue
                target_paths.append(path)

            if not target_paths:
                return pd.DataFrame()

            merged = self._load_field_series_from_paths(
                target_paths,
                field=field,
            )
            if merged.empty:
                return pd.DataFrame()
            wide = merged.unstack("code")
            wide.index = pd.to_datetime(wide.index, errors="coerce")
            wide = wide[wide.index.notna()]
            wide.columns = self._normalize_codes(wide.columns)
            wide = wide.sort_index().sort_index(axis=1)

        if requested_codes is not None:
            wide = wide.loc[:, wide.columns.isin(requested_codes)]
        if codes_to_exclude:
            wide = wide.loc[:, ~wide.columns.isin(codes_to_exclude)]

        wide = wide.sort_index().sort_index(axis=1)
        if str(field).lower() in {"open", "high", "low", "close"}:
            wide = self._filter_bad_codes(wide)
        return wide

    # =========================
    # STOCK BUILD PATH (WRITE PIPELINE)
    # =========================
    # collect_stock(code=...) 단건 수집에서만 사용하는 adjclose 조회
    def _load_adjclose_code(self, code: str) -> pd.Series:
        code_norm = self._normalize_codes([code])[0]
        parts: list[pd.Series] = []

        for path in self._adjclose_paths():
            s = pd.read_pickle(path)
            if not isinstance(s, pd.Series):
                raise TypeError(f"{path}는 pandas Series여야 합니다.")
            if not isinstance(s.index, pd.MultiIndex) or s.index.nlevels != 2:
                raise ValueError(f"{path} 인덱스는 (date, code) MultiIndex여야 합니다.")

            for key in (code_norm, f"A{code_norm}"):
                try:
                    part_raw = s.xs(key, level=1)
                except KeyError:
                    continue

                dates = pd.to_datetime(part_raw.index, errors="coerce")
                values = pd.to_numeric(part_raw.to_numpy(), errors="coerce")
                part = pd.Series(values, index=pd.DatetimeIndex(dates, name="date"), name="adjclose")
                part = part[part.index.notna()].dropna()
                if not part.empty:
                    parts.append(part)

        if not parts:
            return pd.Series(dtype="float64", name="adjclose")

        out = pd.concat(parts).sort_index()
        out = out[~out.index.duplicated(keep="last")]
        return out

    @staticmethod
    def _required_marcap_columns() -> list[str]:
        return ["Date", "Code", "Open", "High", "Low", "Close", "Volume", "Amount", "Marcap", "Stocks"]

    def _marcap_paths(self, start: pd.Timestamp | None = None, end: pd.Timestamp | None = None) -> list[Path]:
        paths = sorted(self.marcap_dir.glob("marcap-*.parquet"))
        if not paths:
            raise FileNotFoundError(f"marcap parquet 파일이 없습니다: {self.marcap_dir}")
        if start is None or end is None:
            return paths

        out: list[Path] = []
        min_year = int(start.year)
        max_year = int(end.year)
        for p in paths:
            m = re.search(r"(\d{4})", p.stem)
            if m is None:
                continue
            y = int(m.group(1))
            if min_year <= y <= max_year:
                out.append(p)
        return out

    def _load_marcap_all(self, start: pd.Timestamp | None = None, end: pd.Timestamp | None = None) -> pd.DataFrame:
        paths = self._marcap_paths(start=start, end=end)

        frames: list[pd.DataFrame] = []
        for path in paths:
            df = pd.read_parquet(path, columns=self._required_marcap_columns())
            frames.append(df)

        out = pd.concat(frames, ignore_index=True)
        out["Date"] = pd.to_datetime(out["Date"], errors="coerce")
        out = out.dropna(subset=["Date"])
        out["Code"] = self._normalize_codes(out["Code"])

        for col in ["Open", "High", "Low", "Close", "Volume", "Amount", "Marcap", "Stocks"]:
            out[col] = pd.to_numeric(out[col], errors="coerce")

        out = out[out["Volume"] > 0]
        out = out.rename(columns={"Date": "date", "Code": "code"})
        return out

    def _load_marcap_code(
        self,
        code: str,
        start: pd.Timestamp | None = None,
        end: pd.Timestamp | None = None,
    ) -> pd.DataFrame:
        code_norm = self._normalize_codes([code])[0]
        paths = self._marcap_paths(start=start, end=end)

        frames: list[pd.DataFrame] = []
        for path in paths:
            filters = [("Code", "==", code_norm)]
            if start is not None:
                filters.append(("Date", ">=", pd.Timestamp(start)))
            if end is not None:
                filters.append(("Date", "<=", pd.Timestamp(end)))

            try:
                df = pd.read_parquet(path, columns=self._required_marcap_columns(), filters=filters)
            except Exception:
                df = pd.read_parquet(path, columns=self._required_marcap_columns())
                df["Code"] = self._normalize_codes(df["Code"])
                df["Date"] = pd.to_datetime(df["Date"], errors="coerce")
                if start is not None:
                    df = df[df["Date"] >= pd.Timestamp(start)]
                if end is not None:
                    df = df[df["Date"] <= pd.Timestamp(end)]
                df = df[df["Code"] == code_norm]

            if df.empty:
                continue
            frames.append(df)

        if not frames:
            return pd.DataFrame(
                columns=["date", "code", "Open", "High", "Low", "Close", "Volume", "Amount", "Marcap", "Stocks"]
            )

        out = pd.concat(frames, ignore_index=True)
        out["Date"] = pd.to_datetime(out["Date"], errors="coerce")
        out = out.dropna(subset=["Date"])
        out = out.rename(columns={"Date": "date", "Code": "code"})

        for col in ["Open", "High", "Low", "Close", "Volume", "Amount", "Marcap", "Stocks"]:
            out[col] = pd.to_numeric(out[col], errors="coerce")

        out = out[out["Volume"] > 0]
        return out

    @staticmethod
    def _build_adjusted_frame(adjclose: pd.Series, marcap_df: pd.DataFrame) -> pd.DataFrame:
        if marcap_df.empty:
            return DB._empty_output_frame()

        m = marcap_df.set_index("date").sort_index()
        joined = m.join(adjclose.rename("AdjClose"), how="inner")
        if joined.empty:
            return DB._empty_output_frame()

        valid = (
            joined["Close"].notna()
            & joined["AdjClose"].notna()
            & (joined["Close"] > 0)
            & (joined["AdjClose"] > 0)
        )
        joined = joined[valid]
        if joined.empty:
            return DB._empty_output_frame()

        factor = joined["AdjClose"] / joined["Close"]
        factor = factor.replace([pd.NA, float("inf"), float("-inf")], pd.NA).dropna()
        factor = factor[factor > 0]
        joined = joined.loc[factor.index]
        if joined.empty:
            return DB._empty_output_frame()

        out = pd.DataFrame(index=joined.index)
        out["open"] = joined["Open"] * factor
        out["high"] = joined["High"] * factor
        out["low"] = joined["Low"] * factor
        out["close"] = joined["AdjClose"]
        out["volume"] = joined["Volume"]
        out["amount"] = joined["Amount"]
        out["marketcap"] = joined["Marcap"]
        out["shares"] = joined["Stocks"]

        out["volume"] = pd.to_numeric(out["volume"], errors="coerce").round().astype("Int64")
        out["shares"] = pd.to_numeric(out["shares"], errors="coerce").round().astype("Int64")

        out = out.sort_index()
        out = out.replace([float("inf"), float("-inf")], pd.NA).dropna(how="all")
        return out

    def _save(self, code: str, frame: pd.DataFrame) -> None:
        self.stock_data_dir.mkdir(parents=True, exist_ok=True)
        path = self.stock_data_dir / f"{code}.parquet"
        frame.to_parquet(path, compression="zstd")

    # raw 소스(adjclose + marcap)에서 db/stock/data/{code}.parquet를 생성하는 단계
    def collect_stock(self, code: str | None = None) -> None:
        if code is not None:
            code_norm = self._normalize_codes([code])[0]
            adj_s = self._load_adjclose_code(code_norm)
            if adj_s.empty:
                raise ValueError(f"adjclose에 해당 종목코드가 없습니다: {code_norm}")

            marcap_df = self._load_marcap_code(
                code_norm,
                start=adj_s.index.min(),
                end=adj_s.index.max(),
            )
            frame = self._build_adjusted_frame(adj_s, marcap_df)
            if frame.empty:
                raise ValueError(f"겹치는 marcap/adjclose 데이터가 없습니다: {code_norm}")
            self._save(code_norm, frame)
            return None

        adjclose = self._load_adjclose()
        all_codes = adjclose.index.get_level_values("code")
        code_set = pd.Index(all_codes.unique())

        min_date = adjclose.index.get_level_values("date").min()
        max_date = adjclose.index.get_level_values("date").max()

        marcap_all = self._load_marcap_all(start=min_date, end=max_date)
        marcap_all = marcap_all[(marcap_all["date"] >= min_date) & (marcap_all["date"] <= max_date)]
        marcap_all = marcap_all[marcap_all["code"].isin(code_set)]
        marcap_by_code = marcap_all.groupby("code", sort=False)

        for code_norm in tqdm(code_set.tolist(), total=len(code_set), desc="collect_stock"):
            adj_s = adjclose.xs(code_norm, level="code").sort_index()
            try:
                m = marcap_by_code.get_group(code_norm)
            except KeyError:
                continue
            frame = self._build_adjusted_frame(adj_s, m)
            if frame.empty:
                continue
            self._save(code_norm, frame)

        return None

    # db/stock/data/{code}.parquet를 field별 db/stock/{field}.parquet로 병합하는 단계
    def build_stock(self) -> None:
        """
        db/stock/data/{code}.parquet 종목 파일들을 field별 단일 parquet로 병합한다.
        결과 파일: db/stock/{field}.parquet
        """
        code_paths = self._code_parquet_paths()
        fields_list = ["open", "high", "low", "close", "volume", "amount", "marketcap", "shares"]

        for field in tqdm(fields_list, total=len(fields_list), desc="build_fields"):
            merged = self._load_field_series_from_paths(
                code_paths,
                field=field,
            )
            if merged.empty:
                continue

            out_df = merged.unstack("code").sort_index()
            out_df.index = pd.to_datetime(out_df.index, errors="coerce")
            out_df = out_df[out_df.index.notna()]
            out_df.columns = self._normalize_codes(out_df.columns)
            out_df = out_df.sort_index().sort_index(axis=1)
            out_path = self._field_path(field)
            self.stock_dir.mkdir(parents=True, exist_ok=True)
            out_df.to_parquet(out_path, compression="zstd")

        return None

    # =========================
    # MARKET BUILD PATH (WRITE PIPELINE)
    # =========================
    def build_market(
        self,
        market: str,
        start: str | pd.Timestamp = "2000-01-01",
        end: str | pd.Timestamp | None = None,
    ) -> Path:
        market_key = str(market).strip().lower()
        if not market_key:
            raise ValueError("market은 비어 있을 수 없습니다.")

        symbol = self._market_symbol(market_key)
        start_ts = pd.Timestamp(start).normalize()
        end_ts = pd.Timestamp.today().normalize() if end is None else pd.Timestamp(end).normalize()
        if end_ts < start_ts:
            raise ValueError("end는 start보다 같거나 이후여야 합니다.")

        raw_df = fdr.DataReader(
            symbol,
            start_ts.strftime("%Y-%m-%d"),
            end_ts.strftime("%Y-%m-%d"),
        )
        if raw_df is None or raw_df.empty:
            raise ValueError(
                f"시장 데이터를 가져오지 못했습니다. market={market_key}, symbol={symbol}"
            )

        out_df = self._normalize_market_frame(raw_df)
        out_path = self._market_file_path(market_key)
        self.market_dir.mkdir(parents=True, exist_ok=True)
        out_df.to_parquet(out_path, compression="zstd")
        return out_path
