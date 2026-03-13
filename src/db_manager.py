from __future__ import annotations

from pathlib import Path
import re
from typing import Iterable, Sequence

import pandas as pd


DEFAULT_MARKETS: tuple[str, ...] = ("KOSPI", "KOSDAQ", "KODEX")
DEFAULT_DEPT_EXCLUDES: tuple[str, ...] = ("SPAC", "관리종목", "투자주의환기종목", "외국기업")

_INDEX_NAME_MAP: dict[str, str] = {
    "kospi": "코스피",
    "kosdaq": "코스닥",
    "kospi200": "코스피200",
    "ks11": "코스피",
    "kq11": "코스닥",
    "ks200": "코스피200",
}


def _require_duckdb():
    try:
        import duckdb
    except ImportError as exc:
        raise ImportError(
            "duckdb가 필요합니다. metricstudio 환경에서 `python -m pip install duckdb`를 실행하세요."
        ) from exc
    return duckdb


def _normalize_ticker_series(values: Iterable[object]) -> pd.Series:
    s = pd.Series(list(values), dtype="object").astype(str).str.strip().str.upper()
    s = s.str.replace(r"^A(?=[0-9A-Z]{6}$)", "", regex=True)
    numeric_mask = s.str.fullmatch(r"\d+")
    s.loc[numeric_mask] = s.loc[numeric_mask].str.zfill(6)
    return s


def _normalize_market_names(values: Iterable[object]) -> list[str]:
    out: list[str] = []
    for value in values:
        text = str(value).strip().upper()
        if not text:
            continue
        out.append(text)
    return out


def _normalize_dept_names(values: Iterable[object]) -> list[str]:
    out: list[str] = []
    for value in values:
        text = str(value).strip()
        if not text:
            continue
        out.append(text)
    return out


def _quote_sql_text(text: str) -> str:
    return "'" + str(text).replace("'", "''") + "'"


def _normalize_column_names(columns: Sequence[str], base: Sequence[str]) -> list[str]:
    cleaned = [str(c).strip() for c in columns if str(c).strip()]
    if not cleaned:
        raise ValueError("columns가 비어 있습니다.")

    merged = [str(c) for c in base]
    for col in cleaned:
        if col not in merged:
            merged.append(col)

    # Column name을 SQL identifier로 안전하게 제한한다.
    for col in merged:
        if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", col) is None:
            raise ValueError(f"허용되지 않는 columns 값입니다: {col}")
    return merged


class DB:
    """
    DuckDB + parquet 기반 데이터 로더.

    - adjusted stock: db/adjusted-stock-*.parquet
    - index: db/index.parquet
    """

    def __init__(
        self,
        db_root_dir: str | Path | None = None,
        adjusted_pattern: str = "adjusted-stock-*.parquet",
        index_file: str = "index.parquet",
    ) -> None:
        root = Path(__file__).resolve().parents[1]
        self.project_root = root
        self.db_root_dir = Path(db_root_dir) if db_root_dir is not None else root / "db"
        self.adjusted_pattern = str(adjusted_pattern)
        self.index_file = str(index_file)

    def _adjusted_glob(self, adjusted_pattern: str | None = None) -> str:
        pattern = self.adjusted_pattern if adjusted_pattern is None else str(adjusted_pattern)
        paths = sorted(self.db_root_dir.glob(pattern))
        if not paths:
            raise FileNotFoundError(f"adjusted parquet 파일이 없습니다: {self.db_root_dir / pattern}")
        return str(self.db_root_dir / pattern)

    def _index_path(self, index_file: str | None = None) -> Path:
        file_name = self.index_file if index_file is None else str(index_file)
        path = self.db_root_dir / file_name
        if not path.exists():
            raise FileNotFoundError(f"index parquet 파일이 없습니다: {path}")
        return path

    @staticmethod
    def _drop_invalid_dates(df: pd.DataFrame) -> pd.DataFrame:
        out = df.copy()
        out["date"] = pd.to_datetime(out["date"], errors="coerce").dt.normalize()
        out = out[out["date"].notna()]
        return out

    def query_adjusted_stock_duckdb(
        self,
        query: str,
        adjusted_pattern: str | None = None,
    ) -> pd.DataFrame:
        """
        adjusted-stock parquet를 DuckDB view(adjusted_stock)로 등록한 뒤 SQL을 실행한다.
        """
        duckdb = _require_duckdb()
        parquet_glob = self._adjusted_glob(adjusted_pattern=adjusted_pattern)
        con = duckdb.connect(database=":memory:")
        try:
            con.execute(
                "CREATE VIEW adjusted_stock AS SELECT * FROM read_parquet(" + _quote_sql_text(parquet_glob) + ")"
            )
            return con.execute(str(query)).df()
        finally:
            con.close()

    def load_adjusted_stock_duckdb(
        self,
        adjusted_pattern: str | None = None,
        columns: Sequence[str] | None = None,
        start: str | pd.Timestamp | None = None,
        end: str | pd.Timestamp | None = None,
        tickers: Sequence[str] | None = None,
        market: Sequence[str] | None = DEFAULT_MARKETS,
        is_tradable: bool | None = True,
        dept_excludes: Sequence[str] = DEFAULT_DEPT_EXCLUDES,
        market_cap_quantile: str | None = None,
    ) -> pd.DataFrame:
        """
        DuckDB로 adjusted-stock parquet를 읽어 필터링한 뒤
        (date, ticker) MultiIndex DataFrame으로 반환한다.

        market_cap_quantile은 날짜별 단면에서 10분위(1Q~10Q)로 계산한다.
        분위 계산은 market/is_tradable/dept_excludes 등 선행 필터를 반영한 집합에서 수행한다.
        """
        select_cols = "*"
        if columns is not None:
            cleaned = _normalize_column_names(columns, base=["date", "ticker"])
            select_cols = ", ".join(cleaned)

        where_parts: list[str] = []
        if start is not None:
            start_s = pd.Timestamp(start).normalize().strftime("%Y-%m-%d")
            where_parts.append(f"date >= DATE '{start_s}'")
        if end is not None:
            end_s = pd.Timestamp(end).normalize().strftime("%Y-%m-%d")
            where_parts.append(f"date <= DATE '{end_s}'")

        if tickers is not None:
            tks = _normalize_ticker_series(tickers).tolist()
            if not tks:
                raise ValueError("tickers가 비어 있습니다.")
            quoted = ", ".join(_quote_sql_text(t) for t in tks)
            where_parts.append(f"ticker IN ({quoted})")

        if market is not None:
            markets = _normalize_market_names([market] if isinstance(market, str) else market)
            if not markets:
                raise ValueError("market이 비어 있습니다.")
            quoted = ", ".join(_quote_sql_text(m) for m in markets)
            where_parts.append(f"UPPER(market) IN ({quoted})")

        if is_tradable is True:
            where_parts.append("is_tradable IS TRUE")
        elif is_tradable is False:
            where_parts.append("is_tradable IS FALSE")

        if dept_excludes:
            depts = _normalize_dept_names(dept_excludes)
            if depts:
                quoted = ", ".join(_quote_sql_text(d) for d in depts)
                where_parts.append(f"(dept IS NULL OR dept NOT IN ({quoted}))")

        quantile_q: int | None = None
        if market_cap_quantile is not None:
            q_text = str(market_cap_quantile).strip().upper()
            m = re.fullmatch(r"(10|[1-9])Q", q_text)
            if m is None:
                raise ValueError("market_cap_quantile은 '1Q' ~ '10Q' 형식이어야 합니다.")
            quantile_q = int(m.group(1))

        base_where_sql = ""
        if where_parts:
            base_where_sql = "WHERE " + " AND ".join(where_parts)

        if quantile_q is None:
            sql = f"""
            SELECT {select_cols}
            FROM adjusted_stock
            {base_where_sql}
            ORDER BY date, ticker, name
            """
        else:
            quant_where_parts = list(where_parts)
            quant_where_parts.append("market_cap IS NOT NULL")
            quant_where_sql = "WHERE " + " AND ".join(quant_where_parts)
            sql = f"""
            WITH filtered AS (
                SELECT *
                FROM adjusted_stock
                {quant_where_sql}
            ),
            ranked AS (
                SELECT
                    *,
                    NTILE(10) OVER (
                        PARTITION BY date
                        ORDER BY market_cap ASC NULLS LAST
                    ) AS _market_cap_q
                FROM filtered
            )
            SELECT {select_cols}
            FROM ranked
            WHERE _market_cap_q = {quantile_q}
            ORDER BY date, ticker, name
            """

        out = self.query_adjusted_stock_duckdb(
            query=sql,
            adjusted_pattern=adjusted_pattern,
        )

        if not {"date", "ticker"}.issubset(out.columns):
            raise ValueError("DuckDB 결과에 date/ticker 컬럼이 없어 인덱스를 만들 수 없습니다.")

        out = self._drop_invalid_dates(out)
        out = out.set_index(["date", "ticker"]).sort_index()
        return out

    def load_index_duckdb(
        self,
        index_file: str | None = None,
        columns: Sequence[str] | None = None,
        start: str | pd.Timestamp | None = None,
        end: str | pd.Timestamp | None = None,
        names: Sequence[str] | None = None,
    ) -> pd.DataFrame:
        """
        DuckDB로 index parquet를 읽어 필터링한 뒤
        (date, name) MultiIndex DataFrame으로 반환한다.
        """
        duckdb = _require_duckdb()
        path = self._index_path(index_file=index_file)

        select_cols = "*"
        if columns is not None:
            cleaned = _normalize_column_names(columns, base=["date", "name"])
            select_cols = ", ".join(cleaned)

        where_parts: list[str] = []
        if start is not None:
            start_s = pd.Timestamp(start).normalize().strftime("%Y-%m-%d")
            where_parts.append(f"date >= DATE '{start_s}'")
        if end is not None:
            end_s = pd.Timestamp(end).normalize().strftime("%Y-%m-%d")
            where_parts.append(f"date <= DATE '{end_s}'")

        if names is not None:
            cleaned_names = [str(n).strip() for n in names if str(n).strip()]
            if not cleaned_names:
                raise ValueError("names가 비어 있습니다.")
            quoted = ", ".join(_quote_sql_text(n) for n in cleaned_names)
            where_parts.append(f"name IN ({quoted})")

        where_sql = ""
        if where_parts:
            where_sql = "WHERE " + " AND ".join(where_parts)

        sql = f"""
        SELECT {select_cols}
        FROM read_parquet({_quote_sql_text(str(path))})
        {where_sql}
        ORDER BY date, name
        """
        con = duckdb.connect(database=":memory:")
        try:
            out = con.execute(sql).df()
        finally:
            con.close()

        if not {"date", "name"}.issubset(out.columns):
            raise ValueError("DuckDB 결과에 date/name 컬럼이 없어 인덱스를 만들 수 없습니다.")

        out = self._drop_invalid_dates(out)
        out = out.set_index(["date", "name"]).sort_index()
        return out

    def load_stock(
        self,
        codes: Iterable[str] | str | None = None,
        field: str = "close",
        mapping_pkl: str | None = None,
        exclude_spac: bool | None = None,
        start: str | pd.Timestamp | None = None,
        end: str | pd.Timestamp | None = None,
        market: Sequence[str] | None = DEFAULT_MARKETS,
        is_tradable: bool | None = True,
        dept_excludes: Sequence[str] = DEFAULT_DEPT_EXCLUDES,
        market_cap_quantile: str | None = None,
    ) -> pd.DataFrame:
        """
        adjusted-stock long 포맷을 (date x ticker) wide 포맷으로 변환한다.
        """
        # mapping_pkl/exclude_spac는 구형 시그니처 호환용으로 유지한다.
        _ = (mapping_pkl, exclude_spac)

        field_name = str(field).strip()
        if not field_name:
            raise ValueError("field는 비어 있을 수 없습니다.")

        tickers: Sequence[str] | None
        if isinstance(codes, str):
            tickers = [codes]
        else:
            tickers = codes

        long_df = self.load_adjusted_stock_duckdb(
            columns=[field_name],
            start=start,
            end=end,
            tickers=tickers,
            market=market,
            is_tradable=is_tradable,
            dept_excludes=dept_excludes,
            market_cap_quantile=market_cap_quantile,
        )
        if field_name not in long_df.columns:
            raise ValueError(f"adjusted-stock 데이터에 '{field_name}' 컬럼이 없습니다.")

        out = pd.to_numeric(long_df[field_name], errors="coerce").unstack("ticker")
        out.index = pd.to_datetime(out.index, errors="coerce")
        out = out[out.index.notna()]
        out.columns = pd.Index([str(c) for c in out.columns], dtype="object")
        out = out.sort_index().sort_index(axis=1)
        return out

    def load_market(
        self,
        market: str,
        field: str | None = None,
        start: str | pd.Timestamp | None = None,
        end: str | pd.Timestamp | None = None,
    ) -> pd.DataFrame | pd.Series:
        """
        index.parquet에서 지정한 지수명을 읽어 (date index) 형태로 반환한다.
        """
        key = str(market).strip().lower()
        if not key:
            raise ValueError("market은 비어 있을 수 없습니다.")

        index_name = _INDEX_NAME_MAP.get(key, str(market).strip())
        df = self.load_index_duckdb(start=start, end=end, names=[index_name])

        if isinstance(df.index, pd.MultiIndex) and "name" in df.index.names:
            df = df.droplevel("name")
        df.index = pd.to_datetime(df.index, errors="coerce")
        df = df[df.index.notna()].sort_index()
        df.columns = [str(c).strip().lower() for c in df.columns]
        if "market_cap" in df.columns and "marketcap" not in df.columns:
            df = df.rename(columns={"market_cap": "marketcap"})

        if field is None:
            return df

        field_key = str(field).strip().lower()
        if field_key not in df.columns:
            raise ValueError(f"market 데이터에 '{field_key}' 컬럼이 없습니다.")
        out = pd.to_numeric(df[field_key], errors="coerce")
        out.name = field_key
        return out

    def load_code_name(self, mapping_pkl: str | None = None) -> pd.Series:
        """
        adjusted-stock에서 종목코드-종목명 매핑을 생성한다.
        """
        _ = mapping_pkl  # 구형 시그니처 호환용
        df = self.load_adjusted_stock_duckdb(columns=["name"])
        if df.empty:
            return pd.Series(dtype="object")

        out = (
            df.reset_index()[["date", "ticker", "name"]]
            .dropna(subset=["ticker", "name"])
            .sort_values(["date", "ticker"], kind="mergesort")
            .drop_duplicates(subset=["ticker"], keep="last")
            .set_index("ticker")["name"]
            .astype("object")
        )
        out.index = pd.Index([str(i) for i in out.index], dtype="object")
        return out


# Backward-compatible module-level wrappers

def query_adjusted_stock_duckdb(
    query: str,
    db_root_dir: str | Path | None = None,
    adjusted_pattern: str = "adjusted-stock-*.parquet",
) -> pd.DataFrame:
    return DB(db_root_dir=db_root_dir, adjusted_pattern=adjusted_pattern).query_adjusted_stock_duckdb(
        query=query,
        adjusted_pattern=adjusted_pattern,
    )


def load_adjusted_stock_duckdb(
    db_root_dir: str | Path | None = None,
    adjusted_pattern: str = "adjusted-stock-*.parquet",
    columns: Sequence[str] | None = None,
    start: str | pd.Timestamp | None = None,
    end: str | pd.Timestamp | None = None,
    tickers: Sequence[str] | None = None,
    market: Sequence[str] | None = DEFAULT_MARKETS,
    is_tradable: bool | None = True,
    dept_excludes: Sequence[str] = DEFAULT_DEPT_EXCLUDES,
    market_cap_quantile: str | None = None,
) -> pd.DataFrame:
    return DB(db_root_dir=db_root_dir, adjusted_pattern=adjusted_pattern).load_adjusted_stock_duckdb(
        adjusted_pattern=adjusted_pattern,
        columns=columns,
        start=start,
        end=end,
        tickers=tickers,
        market=market,
        is_tradable=is_tradable,
        dept_excludes=dept_excludes,
        market_cap_quantile=market_cap_quantile,
    )


def load_index_duckdb(
    db_root_dir: str | Path | None = None,
    index_file: str = "index.parquet",
    columns: Sequence[str] | None = None,
    start: str | pd.Timestamp | None = None,
    end: str | pd.Timestamp | None = None,
    names: Sequence[str] | None = None,
) -> pd.DataFrame:
    return DB(db_root_dir=db_root_dir, index_file=index_file).load_index_duckdb(
        index_file=index_file,
        columns=columns,
        start=start,
        end=end,
        names=names,
    )
