"""
DuckDB + parquet 기반 데이터 로더와 백테스트용 캐시 계층.

- adjusted stock: `db/adjusted-stock-*.parquet`
- index: `db/index.parquet`
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re
from typing import TYPE_CHECKING, Iterable, Sequence

import duckdb
import numpy as np
import pandas as pd


if TYPE_CHECKING:
    from metricstudio.univ import Univ


DEFAULT_MARKETS: tuple[str, ...] = ("KOSPI", "KOSDAQ", "KONEX")
DEFAULT_DEPT_EXCLUDES: tuple[str, ...] = ("SPAC", "관리종목", "투자주의환기종목", "외국기업")
DB_MODE_DUCKDB = 0

_INDEX_NAME_MAP: dict[str, str] = {
    "kospi": "코스피",
    "kosdaq": "코스닥",
    "kospi200": "코스피200",
    "ks11": "코스피",
    "kq11": "코스닥",
    "ks200": "코스피200",
}
_DEFAULT_DATA_LOADER: "DataLoader | None" = None


def _configure_duckdb_connection(con) -> None:
    """
    DuckDB 연결의 불필요한 진행률 출력을 끈다.
    """

    con.execute("SET enable_progress_bar=false")
    con.execute("SET enable_progress_bar_print=false")


def _normalize_ticker_series(values: Iterable[object]) -> pd.Series:
    """
    종목코드 입력을 6자리 ticker 시리즈로 정규화한다.
    """

    s = pd.Series(list(values), dtype="object").astype(str).str.strip().str.upper()
    s = s.str.replace(r"^A(?=[0-9A-Z]{6}$)", "", regex=True)
    numeric_mask = s.str.fullmatch(r"\d+")
    s.loc[numeric_mask] = s.loc[numeric_mask].str.zfill(6)
    return s


def _normalize_market_names(values: Iterable[object]) -> list[str]:
    """
    시장명 입력을 공백 제거 + 대문자 기준으로 정규화한다.
    """

    out: list[str] = []
    for value in values:
        text = str(value).strip().upper()
        if text:
            out.append(text)
    return out


def _normalize_dept_names(values: Iterable[object]) -> list[str]:
    """
    부서/상태명 입력을 공백 제거 기준으로 정규화한다.
    """

    out: list[str] = []
    for value in values:
        text = str(value).strip()
        if text:
            out.append(text)
    return out


def _quote_sql_text(text: str) -> str:
    """
    DuckDB SQL 문자열 리터럴에 안전하게 넣을 수 있도록 이스케이프한다.
    """

    return "'" + str(text).replace("'", "''") + "'"


def _reits_name_exclusion_sql(name_col: str = "name") -> str:
    """
    리츠/유사명칭 종목을 제외하는 SQL 조건식을 만든다.
    """

    col = str(name_col).strip() or "name"
    return (
        f"({col} IS NULL OR {col} NOT LIKE '%리츠%' "
        f"OR {col} LIKE '%메리츠%' OR {col} LIKE '%블리츠%')"
    )


def _normalize_column_names(columns: Sequence[str], base: Sequence[str]) -> list[str]:
    """
    조회할 컬럼 목록을 정리하고 SQL identifier 제약을 검증한다.
    """

    cleaned = [str(c).strip() for c in columns if str(c).strip()]
    if not cleaned:
        raise ValueError("columns가 비어 있습니다.")

    merged = [str(c) for c in base]
    for col in cleaned:
        if col not in merged:
            merged.append(col)

    for col in merged:
        if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", col) is None:
            raise ValueError(f"허용되지 않는 columns 값입니다: {col}")
    return merged


@dataclass
class StockTable:
    """
    백테스트에 쓰는 정렬된 가격 테이블(날짜 x 종목) 컨테이너.
    """

    dates: np.ndarray
    prices: np.ndarray
    codes: list[str]
    code_names: dict[str, str]


class DataLoader:
    """
    DuckDB + parquet 로더와 백테스트용 wide-table 캐시를 함께 관리한다.
    """

    def __init__(
        self,
        db_root_dir: str | Path | None = None,
        adjusted_pattern: str = "adjusted-stock-*.parquet",
        index_file: str = "index.parquet",
    ) -> None:
        root = Path(__file__).resolve().parents[1]
        self.db_root_dir = Path(db_root_dir) if db_root_dir is not None else root / "db"
        self.adjusted_pattern = str(adjusted_pattern)
        self.index_file = str(index_file)
        self._stock_table_cache: dict[tuple[tuple[str, ...] | None, bool | None, tuple[str, ...], bool], StockTable] = {}
        self._market_table_cache: dict[str, pd.DataFrame] = {}
        self._code_name_series: pd.Series | None = None
        self._stock_field_table_cache: dict[
            tuple[tuple[str, ...] | None, bool | None, tuple[str, ...], bool],
            dict[str, pd.DataFrame],
        ] = {}

    @staticmethod
    def _resolve_univ(univ: "Univ | None") -> "Univ":
        """
        None 입력을 기본 Univ 설정으로 치환한다.
        """

        from metricstudio.univ import Univ

        return univ if isinstance(univ, Univ) else Univ()

    @staticmethod
    def _normalize_wide_stock_frame(df: pd.DataFrame) -> pd.DataFrame:
        """
        adjusted-stock long -> wide 변환 결과를 백테스트용 형태로 정리한다.
        """

        out = df.copy()
        out.index = pd.to_datetime(out.index, errors="coerce")
        out = out[out.index.notna()]
        out.columns = pd.Index([str(c) for c in out.columns], dtype="object")
        return out.sort_index().sort_index(axis=1)

    @staticmethod
    def _normalize_market_frame(df: pd.DataFrame) -> pd.DataFrame:
        """
        index.parquet 조회 결과를 (date index, 소문자 컬럼) 형태로 정리한다.
        """

        out = df.copy()
        if isinstance(out.index, pd.MultiIndex) and "name" in out.index.names:
            out = out.droplevel("name")
        out.index = pd.to_datetime(out.index, errors="coerce")
        out = out[out.index.notna()].sort_index()
        out.columns = [str(c).strip().lower() for c in out.columns]
        if "market_cap" in out.columns and "marketcap" not in out.columns:
            out = out.rename(columns={"market_cap": "marketcap"})
        return out

    def _adjusted_glob(self, adjusted_pattern: str | None = None) -> str:
        """
        adjusted-stock parquet glob 문자열을 검증 후 반환한다.
        """

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

        parquet_glob = self._adjusted_glob(adjusted_pattern=adjusted_pattern)
        with duckdb.connect(database=":memory:") as con:
            _configure_duckdb_connection(con)
            con.execute(
                "CREATE VIEW adjusted_stock AS SELECT * FROM read_parquet(" + _quote_sql_text(parquet_glob) + ")"
            )
            return con.execute(str(query)).df()

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
        exclude_reits: bool = True,
    ) -> pd.DataFrame:
        """
        DuckDB로 adjusted-stock parquet를 읽어 필터링한 뒤
        (date, ticker) MultiIndex DataFrame으로 반환한다.
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
        if bool(exclude_reits):
            where_parts.append(_reits_name_exclusion_sql("name"))

        base_where_sql = ""
        if where_parts:
            base_where_sql = "WHERE " + " AND ".join(where_parts)

        sql = f"""
        SELECT {select_cols}
        FROM adjusted_stock
        {base_where_sql}
        ORDER BY date, ticker, name
        """

        out = self.query_adjusted_stock_duckdb(
            query=sql,
            adjusted_pattern=adjusted_pattern,
        )

        if not {"date", "ticker"}.issubset(out.columns):
            raise ValueError("DuckDB 결과에 date/ticker 컬럼이 없어 인덱스를 만들 수 없습니다.")

        out = self._drop_invalid_dates(out)
        return out.set_index(["date", "ticker"]).sort_index()

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
        with duckdb.connect(database=":memory:") as con:
            _configure_duckdb_connection(con)
            out = con.execute(sql).df()

        if not {"date", "name"}.issubset(out.columns):
            raise ValueError("DuckDB 결과에 date/name 컬럼이 없어 인덱스를 만들 수 없습니다.")

        out = self._drop_invalid_dates(out)
        return out.set_index(["date", "name"]).sort_index()

    def load_stock(
        self,
        codes: Iterable[str] | str | None = None,
        field: str = "close",
        start: str | pd.Timestamp | None = None,
        end: str | pd.Timestamp | None = None,
        market: Sequence[str] | None = DEFAULT_MARKETS,
        is_tradable: bool | None = True,
        dept_excludes: Sequence[str] = DEFAULT_DEPT_EXCLUDES,
        exclude_reits: bool = True,
    ) -> pd.DataFrame:
        """
        adjusted-stock long 포맷을 (date x ticker) wide 포맷으로 변환한다.
        """

        field_name = str(field).strip()
        if not field_name:
            raise ValueError("field는 비어 있을 수 없습니다.")

        tickers = [codes] if isinstance(codes, str) else codes
        long_df = self.load_adjusted_stock_duckdb(
            columns=[field_name],
            start=start,
            end=end,
            tickers=tickers,
            market=market,
            is_tradable=is_tradable,
            dept_excludes=dept_excludes,
            exclude_reits=exclude_reits,
        )
        if field_name not in long_df.columns:
            raise ValueError(f"adjusted-stock 데이터에 '{field_name}' 컬럼이 없습니다.")

        out = pd.to_numeric(long_df[field_name], errors="coerce").unstack("ticker")
        return self._normalize_wide_stock_frame(out)

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
        df = self._normalize_market_frame(
            self.load_index_duckdb(start=start, end=end, names=[index_name])
        )

        if field is None:
            return df

        field_key = str(field).strip().lower()
        if field_key not in df.columns:
            raise ValueError(f"market 데이터에 '{field_key}' 컬럼이 없습니다.")
        out = pd.to_numeric(df[field_key], errors="coerce")
        out.name = field_key
        return out

    def load_code_name(self) -> pd.Series:
        """
        adjusted-stock에서 종목코드-종목명 매핑을 생성한다.
        """

        sql = """
        SELECT
            ticker,
            arg_max(name, date) AS name
        FROM adjusted_stock
        WHERE name IS NOT NULL
        GROUP BY ticker
        ORDER BY ticker
        """
        df = self.query_adjusted_stock_duckdb(sql)
        if df.empty:
            return pd.Series(dtype="object")

        out = df.dropna(subset=["ticker", "name"]).set_index("ticker")["name"].astype("object")
        out.index = pd.Index([str(i) for i in out.index], dtype="object")
        return out

    def load_stock_field_table(self, field: str, univ: "Univ | None" = None) -> pd.DataFrame:
        """
        단일 필드의 wide 주가 테이블을 반환한다.
        """

        return self.load_stock_field_tables([field], univ)[str(field).strip().lower()]

    def load_stock_field_tables(
        self,
        fields: list[str],
        univ: "Univ | None" = None,
    ) -> dict[str, pd.DataFrame]:
        """
        여러 stock 필드를 한 번에 로드해 필드명 -> wide 테이블로 반환한다.
        """

        resolved_univ = self._resolve_univ(univ)
        cache_key = resolved_univ.cache_key()
        cache = self._stock_field_table_cache.setdefault(cache_key, {})
        keys: list[str] = []
        for field in fields:
            key = str(field).strip().lower()
            if key and key not in keys:
                keys.append(key)

        if not keys:
            return {}

        field_map = {"marketcap": "market_cap"}
        missing_keys = [key for key in keys if key not in cache]
        if missing_keys:
            source_fields = [field_map.get(key, key) for key in missing_keys]
            long_df = self.load_adjusted_stock_duckdb(
                columns=source_fields,
                market=resolved_univ.market,
                is_tradable=resolved_univ.is_tradable,
                dept_excludes=resolved_univ.dept_excludes,
                exclude_reits=resolved_univ.exclude_reits,
            )
            for key in missing_keys:
                source_field = field_map.get(key, key)
                if source_field not in long_df.columns:
                    raise ValueError(f"adjusted-stock 데이터에 '{source_field}' 컬럼이 없습니다.")
                wide = pd.to_numeric(long_df[source_field], errors="coerce").unstack("ticker")
                cache[key] = self._normalize_wide_stock_frame(wide)

        return {key: cache[key] for key in keys}

    def load_stock_table(self, univ: "Univ | None" = None) -> StockTable:
        """
        종가 테이블을 로드해 numpy 기반 StockTable로 반환한다.
        """

        resolved_univ = self._resolve_univ(univ)
        cache_key = resolved_univ.cache_key()
        if cache_key in self._stock_table_cache:
            return self._stock_table_cache[cache_key]

        close_wide = self.load_stock(
            field="close",
            market=resolved_univ.market,
            is_tradable=resolved_univ.is_tradable,
            dept_excludes=resolved_univ.dept_excludes,
            exclude_reits=resolved_univ.exclude_reits,
        )
        table = StockTable(
            dates=close_wide.index.to_numpy(dtype="datetime64[ns]"),
            prices=close_wide.to_numpy(dtype=np.float64, copy=True),
            codes=[str(c) for c in close_wide.columns],
            code_names={},
        )
        self._stock_table_cache[cache_key] = table
        return table

    def load_market_table(self, market: str) -> pd.DataFrame:
        """
        시장 보조지표 테이블을 로드해 전역 캐시에 보관한다.
        """

        key = str(market).strip().lower()
        if not key:
            raise ValueError("market은 비어 있을 수 없습니다.")
        if key in self._market_table_cache:
            return self._market_table_cache[key]

        df = self.load_market(key)
        if not isinstance(df, pd.DataFrame):
            raise TypeError("load_market_table은 DataFrame 결과만 지원합니다.")
        self._market_table_cache[key] = df
        return df

    def load_code_name_series(self) -> pd.Series:
        """
        종목코드-종목명 매핑 시리즈를 로드한다.
        """

        if self._code_name_series is not None:
            return self._code_name_series

        table = self.load_stock_table()
        if table.code_names:
            out = pd.Series(table.code_names, dtype="object")
        else:
            out = self.load_code_name()
        self._code_name_series = out
        return out


def get_default_data_loader() -> DataLoader:
    """
    기본 DataLoader 싱글턴을 반환한다.
    """

    global _DEFAULT_DATA_LOADER
    if _DEFAULT_DATA_LOADER is None:
        _DEFAULT_DATA_LOADER = DataLoader()
    return _DEFAULT_DATA_LOADER


__all__ = [
    "DB_MODE_DUCKDB",
    "DEFAULT_DEPT_EXCLUDES",
    "DEFAULT_MARKETS",
    "DataLoader",
    "StockTable",
    "get_default_data_loader",
]
