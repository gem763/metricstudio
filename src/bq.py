from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass
from datetime import date
from types import SimpleNamespace
from typing import Any, Sequence

import numpy as np
import pandas as pd
from google.cloud import bigquery

DEFAULT_PROJECT = "openclaw-gcp-proj"
DEFAULT_STOCK_DAILY_TABLE = "kr_stock.daily"
DEFAULT_INDEX_DAILY_TABLE = "kr_index.daily"
DEFAULT_EVENT_TABLE = "kr_events.adj_factor"
# amount/market_cap은 가격·거래량(또는 주식수) 관계로 이미 반영된 값으로 보고 기본 조정 대상에서 제외한다.
ADJ_MULTIPLY_FIELDS = ("open", "high", "low", "close")
ADJ_DIVIDE_FIELDS = ("volume", "shares_outstanding")
NUMERIC_CANDIDATE_FIELDS = (
    "open",
    "high",
    "low",
    "close",
    "volume",
    "amount",
    "shares_outstanding",
    "market_cap",
    "rank",
)

_CACHE_STATE_NAME = "_metricstudio_bq_cache_state"
_cache_state = sys.modules.get(_CACHE_STATE_NAME)
if _cache_state is None:
    _cache_state = SimpleNamespace(
        daily_query_cache={},
        events_query_cache={},
        bounds_cache={},
        daily_batch_query_cache={},
        events_batch_query_cache={},
        bounds_batch_cache={},
        processed_data_cache={},
        bq_client_cache={},
    )
    sys.modules[_CACHE_STATE_NAME] = _cache_state

_DAILY_QUERY_CACHE: dict[
    tuple[
        str,
        str,
        str,
        str,
        date | None,
        date | None,
        tuple[str, ...] | None,
        tuple[str, ...] | None,
        tuple[str, ...] | None,
    ],
    pd.DataFrame,
] = _cache_state.daily_query_cache
_EVENTS_QUERY_CACHE: dict[
    tuple[str, str, str, str, date | None, date | None],
    pd.DataFrame,
] = _cache_state.events_query_cache
_BOUNDS_CACHE: dict[tuple[str, str, str, str], tuple[date, date]] = _cache_state.bounds_cache
_DAILY_BATCH_QUERY_CACHE: dict[
    tuple[
        str,
        str,
        str,
        tuple[str, ...] | None,
        date | None,
        date | None,
        tuple[str, ...] | None,
        tuple[str, ...] | None,
        tuple[str, ...] | None,
    ],
    pd.DataFrame,
] = _cache_state.daily_batch_query_cache
_EVENTS_BATCH_QUERY_CACHE: dict[
    tuple[str, str, str, tuple[str, ...] | None, date | None, date | None],
    pd.DataFrame,
] = _cache_state.events_batch_query_cache
_BOUNDS_BATCH_CACHE: dict[tuple[str, str, str, tuple[str, ...] | None], tuple[date, date]] = _cache_state.bounds_batch_cache
_PROCESSED_DATA_CACHE: dict[tuple[Any, ...], pd.DataFrame] = _cache_state.processed_data_cache
_BQ_CLIENT_CACHE: dict[str, Any] = _cache_state.bq_client_cache
_TABLE_COLUMNS_CACHE: dict[tuple[str, str], tuple[str, ...]] = {}


@dataclass(frozen=True)
class Inputs:
    ticker: str
    start: date
    end: date
    daily_table: str
    event_table: str
    output: str | None


def _to_date(v: str | date | pd.Timestamp | None) -> date | None:
    if v is None:
        return None
    if isinstance(v, date) and not isinstance(v, pd.Timestamp):
        return v
    ts = pd.Timestamp(v)
    if pd.isna(ts):
        return None
    return ts.date()


def normalize_ticker(ticker: str) -> str:
    t = str(ticker).strip().upper()
    if t.startswith("A") and len(t) == 7 and t[1:].isdigit():
        t = t[1:]
    return t.zfill(6)


def parse_args(argv: Sequence[str] | None = None) -> Inputs:
    ap = argparse.ArgumentParser(
        description="BigQuery(kr_stock.daily, kr_events.adj_factor) 기반 수정주가 산출"
    )
    ap.add_argument("--ticker", required=True, help="6-digit ticker, e.g. 005930")
    ap.add_argument("--start", required=True, help="YYYY-MM-DD")
    ap.add_argument("--end", required=True, help="YYYY-MM-DD")
    ap.add_argument("--daily-table", default=DEFAULT_STOCK_DAILY_TABLE, help="dataset.table")
    ap.add_argument("--event-table", default=DEFAULT_EVENT_TABLE, help="dataset.table")
    ap.add_argument("--output", default=None, help="CSV output path (default: stdout)")

    ns = ap.parse_args(argv)
    ticker = normalize_ticker(ns.ticker)
    start = _to_date(ns.start)
    end = _to_date(ns.end)
    if start is None or end is None:
        raise SystemExit("start/end는 YYYY-MM-DD 형식이어야 합니다.")
    if start > end:
        raise SystemExit("start must be <= end")

    return Inputs(
        ticker=ticker,
        start=start,
        end=end,
        daily_table=str(ns.daily_table).strip(),
        event_table=str(ns.event_table).strip(),
        output=str(ns.output).strip() if ns.output else None,
    )


def bq_client(project: str):
    proj = str(project).strip()
    cached = _BQ_CLIENT_CACHE.get(proj)
    if cached is not None:
        return cached
    client = bigquery.Client(project=proj)
    _BQ_CLIENT_CACHE[proj] = client
    return client


def _table_fqn(project: str, table: str) -> str:
    cleaned = str(table).strip().strip("`")
    if cleaned.count(".") != 1:
        raise ValueError(f"table 형식이 잘못되었습니다: {table} (expected: dataset.table)")
    return f"`{project}.{cleaned}`"


def _split_table(table: str) -> tuple[str, str]:
    cleaned = str(table).strip().strip("`")
    if cleaned.count(".") != 1:
        raise ValueError(f"table 형식이 잘못되었습니다: {table} (expected: dataset.table)")
    dataset, table_name = cleaned.split(".", 1)
    return dataset, table_name


def _get_table_columns(
    client,
    project: str,
    table: str,
    use_cache: bool = True,
) -> tuple[str, ...]:
    cache_key = (str(project), str(table))
    if use_cache and cache_key in _TABLE_COLUMNS_CACHE:
        return _TABLE_COLUMNS_CACHE[cache_key]

    dataset, table_name = _split_table(table)
    q = f"""
    SELECT column_name
    FROM `{project}.{dataset}.INFORMATION_SCHEMA.COLUMNS`
    WHERE table_name = @table_name
    """
    job = client.query(
        q,
        job_config=bigquery.QueryJobConfig(
            query_parameters=[bigquery.ScalarQueryParameter("table_name", "STRING", table_name)]
        ),
    )
    cols = tuple(str(r["column_name"]) for r in job.result())
    _TABLE_COLUMNS_CACHE[cache_key] = cols
    return cols


def _normalize_flds(flds: Sequence[str] | None) -> tuple[str, ...] | None:
    if flds is None:
        return None
    cleaned: list[str] = []
    removed_created_at = False
    for f in flds:
        s = str(f).strip()
        if not s:
            continue
        if s == "created_at":
            removed_created_at = True
            continue
        cleaned.append(s)
    if not cleaned:
        if removed_created_at:
            raise ValueError("created_at은 조회 대상에서 제외됩니다. 다른 필드를 지정하세요.")
        raise ValueError("flds가 비어 있습니다.")
    return tuple(dict.fromkeys(cleaned))


def _normalize_where_clauses(clauses: Sequence[str] | None) -> tuple[str, ...] | None:
    if clauses is None:
        return None
    cleaned: list[str] = []
    for c in clauses:
        s = str(c).strip()
        if not s:
            continue
        cleaned.append(s)
    if not cleaned:
        return None
    return tuple(dict.fromkeys(cleaned))


def _normalize_markets(markets: Sequence[str] | None) -> tuple[str, ...] | None:
    if markets is None:
        return None
    out: list[str] = []
    for m in markets:
        s = str(m).strip().upper()
        if not s:
            continue
        out.append(s)
    if not out:
        raise ValueError("market이 비어 있습니다.")
    return tuple(dict.fromkeys(out))


def _normalize_symbols(
    symbols: Sequence[str] | None,
    normalize_symbol_fn,
) -> tuple[str, ...] | None:
    if symbols is None:
        return None
    out: list[str] = []
    for s in symbols:
        v = normalize_symbol_fn(s)
        if v:
            out.append(v)
    return tuple(dict.fromkeys(out))


def _set_date_index(df: pd.DataFrame, date_col: str = "date") -> pd.DataFrame:
    out = df.copy()
    if date_col in out.columns:
        dt = pd.to_datetime(out[date_col], errors="coerce")
        out = out[dt.notna()].copy()
        out[date_col] = dt[dt.notna()].dt.normalize()
        out = out.sort_values(date_col).set_index(date_col, drop=True)
    else:
        if not isinstance(out.index, pd.DatetimeIndex):
            raise ValueError(f"날짜 인덱스를 만들 수 없습니다. '{date_col}' 컬럼이 필요합니다.")
        out.index = pd.to_datetime(out.index, errors="coerce")
        out = out[out.index.notna()].copy()
        out.index = out.index.normalize()
        out = out.sort_index()
    out.index.name = "date"
    return out


def fetch_daily(
    client,
    project: str,
    table: str,
    symbol: str,
    start: date | None = None,
    end: date | None = None,
    flds: Sequence[str] | None = None,
    extra_where: Sequence[str] | None = None,
    market_filter: Sequence[str] | None = None,
    exclude_created_at_in_query: bool = False,
    symbol_col: str = "ticker",
    normalize_symbol_fn=None,
    use_cache: bool = True,
) -> pd.DataFrame:
    selected_flds = _normalize_flds(flds)
    extra_where_key = _normalize_where_clauses(extra_where)
    markets_key = _normalize_markets(market_filter)
    if normalize_symbol_fn is None:
        normalize_symbol_fn = lambda x: str(x).strip()  # noqa: E731
    symbol_norm = normalize_symbol_fn(symbol)
    symbol_col_key = str(symbol_col).strip()
    cache_key = (
        str(project),
        str(table),
        symbol_col_key,
        symbol_norm,
        start,
        end,
        selected_flds,
        extra_where_key,
        markets_key,
        ("exclude_created_at_in_query",) if exclude_created_at_in_query else None,
    )
    if use_cache and cache_key in _DAILY_QUERY_CACHE:
        return _DAILY_QUERY_CACHE[cache_key].copy()

    if selected_flds is None:
        if exclude_created_at_in_query:
            cols = _get_table_columns(client, project=project, table=table, use_cache=use_cache)
            if "created_at" in cols:
                select_expr = "* EXCEPT(created_at)"
            else:
                select_expr = "*"
        else:
            select_expr = "*"
    else:
        select_expr = ",\n      ".join(f"`{c}`" for c in selected_flds)

    where_clauses = [f"`{symbol_col_key}` = @symbol"]
    query_parameters: list[Any] = [bigquery.ScalarQueryParameter("symbol", "STRING", symbol_norm)]
    if start is not None:
        where_clauses.append("date >= @start")
        query_parameters.append(bigquery.ScalarQueryParameter("start", "DATE", start))
    if end is not None:
        where_clauses.append("date <= @end")
        query_parameters.append(bigquery.ScalarQueryParameter("end", "DATE", end))
    if markets_key is not None:
        where_clauses.append("UPPER(`market`) IN UNNEST(@markets)")
        query_parameters.append(bigquery.ArrayQueryParameter("markets", "STRING", list(markets_key)))
    if extra_where_key is not None:
        where_clauses.extend(list(extra_where_key))

    where_sql = " AND\n      ".join(where_clauses)
    q = f"""
    SELECT
      {select_expr}
    FROM {_table_fqn(project, table)}
    WHERE {where_sql}
    ORDER BY date
    """
    job = client.query(
        q,
        job_config=bigquery.QueryJobConfig(query_parameters=query_parameters),
    )
    df = job.result().to_dataframe()
    if df.empty:
        raise ValueError(f"{table} 조회 결과가 없습니다. ticker/range를 확인하세요.")

    if "date" in df.columns:
        df["date"] = pd.to_datetime(df["date"], errors="coerce").dt.normalize()
    if symbol_col_key in df.columns:
        df[symbol_col_key] = df[symbol_col_key].astype(str).map(normalize_symbol_fn)
    if "created_at" in df.columns:
        df = df.drop(columns=["created_at"])

    for col in NUMERIC_CANDIDATE_FIELDS:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")

    if "date" in df.columns:
        df = df.sort_values("date").drop_duplicates(subset=["date"], keep="last").reset_index(drop=True)
    else:
        df = df.reset_index(drop=True)

    if selected_flds is not None:
        missing = [c for c in selected_flds if c not in df.columns]
        if missing:
            raise ValueError(f"요청한 필드를 daily 테이블에서 찾지 못했습니다: {missing}")
        df = df.loc[:, list(selected_flds)]
    _DAILY_QUERY_CACHE[cache_key] = df.copy()
    return df


def fetch_daily_date_bounds(
    client,
    project: str,
    table: str,
    symbol: str,
    symbol_col: str = "ticker",
    normalize_symbol_fn=None,
    use_cache: bool = True,
) -> tuple[date, date]:
    if normalize_symbol_fn is None:
        normalize_symbol_fn = lambda x: str(x).strip()  # noqa: E731
    symbol_norm = normalize_symbol_fn(symbol)
    symbol_col_key = str(symbol_col).strip()
    cache_key = (str(project), str(table), symbol_col_key, symbol_norm)
    if use_cache and cache_key in _BOUNDS_CACHE:
        return _BOUNDS_CACHE[cache_key]

    q = f"""
    SELECT
      MIN(date) AS min_date,
      MAX(date) AS max_date
    FROM {_table_fqn(project, table)}
    WHERE `{symbol_col_key}` = @symbol
    """
    job = client.query(
        q,
        job_config=bigquery.QueryJobConfig(
            query_parameters=[
                bigquery.ScalarQueryParameter("symbol", "STRING", symbol_norm),
            ]
        ),
    )
    df = job.result().to_dataframe()
    if df.empty:
        raise ValueError("daily 테이블에서 날짜 경계를 조회하지 못했습니다.")

    min_date = _to_date(df.loc[0, "min_date"])
    max_date = _to_date(df.loc[0, "max_date"])
    if min_date is None or max_date is None:
        raise ValueError("해당 ticker의 daily 데이터가 없습니다.")
    _BOUNDS_CACHE[cache_key] = (min_date, max_date)
    return min_date, max_date


def fetch_daily_batch(
    client,
    project: str,
    table: str,
    start: date | None = None,
    end: date | None = None,
    symbols: Sequence[str] | None = None,
    flds: Sequence[str] | None = None,
    extra_where: Sequence[str] | None = None,
    market_filter: Sequence[str] | None = None,
    symbol_col: str = "ticker",
    normalize_symbol_fn=None,
    use_cache: bool = True,
) -> pd.DataFrame:
    if normalize_symbol_fn is None:
        normalize_symbol_fn = lambda x: str(x).strip()  # noqa: E731
    selected_flds = _normalize_flds(flds)
    extra_where_key = _normalize_where_clauses(extra_where)
    markets_key = _normalize_markets(market_filter)
    symbols_key = _normalize_symbols(symbols, normalize_symbol_fn=normalize_symbol_fn)
    symbol_col_key = str(symbol_col).strip()
    cache_key = (
        str(project),
        str(table),
        symbol_col_key,
        symbols_key,
        start,
        end,
        selected_flds,
        extra_where_key,
        markets_key,
    )
    if use_cache and cache_key in _DAILY_BATCH_QUERY_CACHE:
        return _DAILY_BATCH_QUERY_CACHE[cache_key].copy()

    if symbols_key is not None and len(symbols_key) == 0:
        return pd.DataFrame(columns=list(selected_flds) if selected_flds is not None else None)

    if selected_flds is None:
        select_expr = "*"
    else:
        select_expr = ",\n      ".join(f"`{c}`" for c in selected_flds)

    where_clauses: list[str] = []
    query_parameters: list[Any] = []
    if start is not None:
        where_clauses.append("date >= @start")
        query_parameters.append(bigquery.ScalarQueryParameter("start", "DATE", start))
    if end is not None:
        where_clauses.append("date <= @end")
        query_parameters.append(bigquery.ScalarQueryParameter("end", "DATE", end))
    if symbols_key is not None:
        where_clauses.append(f"`{symbol_col_key}` IN UNNEST(@symbols)")
        query_parameters.append(bigquery.ArrayQueryParameter("symbols", "STRING", list(symbols_key)))
    if markets_key is not None:
        where_clauses.append("UPPER(`market`) IN UNNEST(@markets)")
        query_parameters.append(bigquery.ArrayQueryParameter("markets", "STRING", list(markets_key)))
    if extra_where_key is not None:
        where_clauses.extend(list(extra_where_key))
    where_sql = ""
    if where_clauses:
        where_sql = "WHERE " + " AND ".join(where_clauses)

    q = f"""
    SELECT
      {select_expr}
    FROM {_table_fqn(project, table)}
    {where_sql}
    ORDER BY date, `{symbol_col_key}`
    """
    job = client.query(
        q,
        job_config=bigquery.QueryJobConfig(query_parameters=query_parameters),
    )
    df = job.result().to_dataframe()
    if df.empty:
        raise ValueError(f"{table} 조회 결과가 없습니다. ticker/range를 확인하세요.")

    if "date" in df.columns:
        df["date"] = pd.to_datetime(df["date"], errors="coerce").dt.normalize()
    if symbol_col_key in df.columns:
        df[symbol_col_key] = df[symbol_col_key].astype(str).map(normalize_symbol_fn)
    if "created_at" in df.columns:
        df = df.drop(columns=["created_at"])

    for col in NUMERIC_CANDIDATE_FIELDS:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")

    if "date" in df.columns:
        dedup_cols = ["date"]
        if symbol_col_key in df.columns:
            dedup_cols.append(symbol_col_key)
        df = df.sort_values(dedup_cols).drop_duplicates(subset=dedup_cols, keep="last").reset_index(drop=True)
    else:
        df = df.reset_index(drop=True)

    if selected_flds is not None:
        missing = [c for c in selected_flds if c not in df.columns]
        if missing:
            raise ValueError(f"요청한 필드를 daily 테이블에서 찾지 못했습니다: {missing}")
        df = df.loc[:, list(selected_flds)]
    _DAILY_BATCH_QUERY_CACHE[cache_key] = df.copy()
    return df


def fetch_daily_date_bounds_batch(
    client,
    project: str,
    table: str,
    symbols: Sequence[str] | None = None,
    symbol_col: str = "ticker",
    normalize_symbol_fn=None,
    use_cache: bool = True,
) -> tuple[date, date]:
    if normalize_symbol_fn is None:
        normalize_symbol_fn = lambda x: str(x).strip()  # noqa: E731
    symbols_key = _normalize_symbols(symbols, normalize_symbol_fn=normalize_symbol_fn)
    symbol_col_key = str(symbol_col).strip()
    cache_key = (str(project), str(table), symbol_col_key, symbols_key)
    if use_cache and cache_key in _BOUNDS_BATCH_CACHE:
        return _BOUNDS_BATCH_CACHE[cache_key]

    if symbols_key is not None and len(symbols_key) == 0:
        raise ValueError("symbols가 비어 있습니다.")

    filter_symbol_sql = ""
    query_parameters = []
    if symbols_key is not None:
        filter_symbol_sql = f" WHERE `{symbol_col_key}` IN UNNEST(@symbols)"
        query_parameters.append(bigquery.ArrayQueryParameter("symbols", "STRING", list(symbols_key)))

    q = f"""
    SELECT
      MIN(date) AS min_date,
      MAX(date) AS max_date
    FROM {_table_fqn(project, table)}
    {filter_symbol_sql}
    """
    job = client.query(
        q,
        job_config=bigquery.QueryJobConfig(query_parameters=query_parameters),
    )
    df = job.result().to_dataframe()
    if df.empty:
        raise ValueError("daily 테이블에서 날짜 경계를 조회하지 못했습니다.")

    min_date = _to_date(df.loc[0, "min_date"])
    max_date = _to_date(df.loc[0, "max_date"])
    if min_date is None or max_date is None:
        raise ValueError("조건에 해당하는 daily 데이터가 없습니다.")
    _BOUNDS_BATCH_CACHE[cache_key] = (min_date, max_date)
    return min_date, max_date


def fetch_events_batch(
    client,
    project: str,
    table: str,
    start: date | None = None,
    end: date | None = None,
    symbols: Sequence[str] | None = None,
    symbol_col: str = "ticker",
    normalize_symbol_fn=None,
    use_cache: bool = True,
) -> pd.DataFrame:
    if normalize_symbol_fn is None:
        normalize_symbol_fn = lambda x: str(x).strip()  # noqa: E731
    symbols_key = _normalize_symbols(symbols, normalize_symbol_fn=normalize_symbol_fn)
    symbol_col_key = str(symbol_col).strip()
    cache_key = (str(project), str(table), symbol_col_key, symbols_key, start, end)
    if use_cache and cache_key in _EVENTS_BATCH_QUERY_CACHE:
        return _EVENTS_BATCH_QUERY_CACHE[cache_key].copy()

    if symbols_key is not None and len(symbols_key) == 0:
        out = pd.DataFrame(columns=["date", "ticker", "ratio"])
        _EVENTS_BATCH_QUERY_CACHE[cache_key] = out.copy()
        return out

    where_clauses: list[str] = []
    query_parameters: list[Any] = []
    if start is not None:
        where_clauses.append("date >= @start")
        query_parameters.append(bigquery.ScalarQueryParameter("start", "DATE", start))
    if end is not None:
        where_clauses.append("date <= @end")
        query_parameters.append(bigquery.ScalarQueryParameter("end", "DATE", end))
    if symbols_key is not None:
        where_clauses.append(f"`{symbol_col_key}` IN UNNEST(@symbols)")
        query_parameters.append(bigquery.ArrayQueryParameter("symbols", "STRING", list(symbols_key)))
    where_sql = ""
    if where_clauses:
        where_sql = "WHERE " + " AND ".join(where_clauses)

    q = f"""
    SELECT
      date,
      `{symbol_col_key}` AS ticker,
      ratio
    FROM {_table_fqn(project, table)}
    {where_sql}
    ORDER BY date, `{symbol_col_key}`
    """
    job = client.query(
        q,
        job_config=bigquery.QueryJobConfig(query_parameters=query_parameters),
    )
    df = job.result().to_dataframe()
    if df.empty:
        out = pd.DataFrame(columns=["date", "ticker", "ratio"])
        _EVENTS_BATCH_QUERY_CACHE[cache_key] = out.copy()
        return out

    df["date"] = pd.to_datetime(df["date"], errors="coerce").dt.normalize()
    df["ticker"] = df["ticker"].astype(str).map(normalize_symbol_fn)
    df["ratio"] = pd.to_numeric(df["ratio"], errors="coerce")
    df = df[df["ratio"].notna()].copy()
    df = df[df["ratio"] != 1.0].copy()
    out = df.sort_values(["date", "ticker"]).reset_index(drop=True)
    _EVENTS_BATCH_QUERY_CACHE[cache_key] = out.copy()
    return out


def fetch_events(
    client,
    project: str,
    table: str,
    symbol: str,
    start: date | None = None,
    end: date | None = None,
    symbol_col: str = "ticker",
    normalize_symbol_fn=None,
    use_cache: bool = True,
) -> pd.DataFrame:
    if normalize_symbol_fn is None:
        normalize_symbol_fn = lambda x: str(x).strip()  # noqa: E731
    symbol_norm = normalize_symbol_fn(symbol)
    symbol_col_key = str(symbol_col).strip()
    cache_key = (str(project), str(table), symbol_col_key, symbol_norm, start, end)
    if use_cache and cache_key in _EVENTS_QUERY_CACHE:
        return _EVENTS_QUERY_CACHE[cache_key].copy()

    where_clauses = [f"`{symbol_col_key}` = @symbol"]
    query_parameters: list[Any] = [bigquery.ScalarQueryParameter("symbol", "STRING", symbol_norm)]
    if start is not None:
        where_clauses.append("date >= @start")
        query_parameters.append(bigquery.ScalarQueryParameter("start", "DATE", start))
    if end is not None:
        where_clauses.append("date <= @end")
        query_parameters.append(bigquery.ScalarQueryParameter("end", "DATE", end))
    where_sql = " AND\n      ".join(where_clauses)
    q = f"""
    SELECT
      date,
      `{symbol_col_key}` AS ticker,
      ratio
    FROM {_table_fqn(project, table)}
    WHERE {where_sql}
    ORDER BY date
    """
    job = client.query(
        q,
        job_config=bigquery.QueryJobConfig(query_parameters=query_parameters),
    )
    df = job.result().to_dataframe()
    if df.empty:
        out = pd.DataFrame(columns=["date", "ticker", "ratio"])
        _EVENTS_QUERY_CACHE[cache_key] = out.copy()
        return out

    df["date"] = pd.to_datetime(df["date"], errors="coerce").dt.normalize()
    df["ticker"] = df["ticker"].astype(str).map(normalize_symbol_fn)
    df["ratio"] = pd.to_numeric(df["ratio"], errors="coerce")
    df = df[df["ratio"].notna()].copy()
    df = df[df["ratio"] != 1.0].copy()
    out = df.sort_values("date").reset_index(drop=True)
    _EVENTS_QUERY_CACHE[cache_key] = out.copy()
    return out


def apply_adjustment(
    daily: pd.DataFrame,
    events: pd.DataFrame,
    symbol_col: str = "ticker",
    multiply_fields: Sequence[str] = ADJ_MULTIPLY_FIELDS,
    divide_fields: Sequence[str] = ADJ_DIVIDE_FIELDS,
) -> pd.DataFrame:
    if daily.empty:
        raise ValueError("daily 데이터가 비어 있습니다.")
    out = daily.copy()
    if "date" not in out.columns:
        idx_name = out.index.name if out.index.name is not None else "index"
        out = out.reset_index().rename(columns={idx_name: "date"})
    out["date"] = pd.to_datetime(out["date"], errors="coerce").dt.normalize()
    out = out[out["date"].notna()].copy()
    symbol_col_key = str(symbol_col).strip()
    added_symbol_col = False
    if symbol_col_key not in out.columns:
        out[symbol_col_key] = "__SINGLE__"
        added_symbol_col = True
    out[symbol_col_key] = out[symbol_col_key].astype(str).str.strip()
    out = out.sort_values([symbol_col_key, "date"]).reset_index(drop=True)

    event_df = events.copy()
    if not event_df.empty and "date" not in event_df.columns:
        idx_name = event_df.index.name if event_df.index.name is not None else "index"
        event_df = event_df.reset_index().rename(columns={idx_name: "date"})
    if "date" in event_df.columns:
        event_df["date"] = pd.to_datetime(event_df["date"], errors="coerce").dt.normalize()
        event_df = event_df[event_df["date"].notna()].copy()
    if not event_df.empty:
        if symbol_col_key not in event_df.columns:
            if "ticker" in event_df.columns:
                event_df[symbol_col_key] = event_df["ticker"]
            else:
                event_df[symbol_col_key] = "__SINGLE__"
        event_df[symbol_col_key] = event_df[symbol_col_key].astype(str).str.strip()

    event_map: pd.Series
    if not event_df.empty:
        event_map = (
            event_df.groupby([symbol_col_key, "date"], sort=False)["ratio"].prod().rename("_ratio")
        )
        out = out.merge(
            event_map,
            on=[symbol_col_key, "date"],
            how="left",
        )
    else:
        out["_ratio"] = np.nan
    out["_ratio"] = pd.to_numeric(out["_ratio"], errors="coerce").fillna(1.0)

    rev = out.iloc[::-1].copy()
    rev["_cumprod"] = rev.groupby(symbol_col_key, sort=False)["_ratio"].cumprod()
    rev["_mult"] = rev.groupby(symbol_col_key, sort=False)["_cumprod"].shift(1, fill_value=1.0)
    out["_mult"] = rev["_mult"].iloc[::-1].to_numpy(dtype=float)

    for col in multiply_fields:
        if col in out.columns:
            out[col] = pd.to_numeric(out[col], errors="coerce") * out["_mult"]
    for col in divide_fields:
        if col in out.columns:
            base = pd.to_numeric(out[col], errors="coerce")
            out[col] = base / out["_mult"].replace(0, np.nan)

    out = out.drop(columns=["_ratio", "_cumprod", "_mult"], errors="ignore")
    if added_symbol_col:
        out = out.drop(columns=[symbol_col_key], errors="ignore")
    out = out.sort_values(["date", symbol_col_key] if symbol_col_key in out.columns else ["date"]).reset_index(
        drop=True
    )

    return out


class BigQueryData:
    def __init__(
        self,
        ticker: str,
        start: str | date | None = None,
        end: str | date | None = None,
        flds: Sequence[str] | None = None,
        daily_table: str = DEFAULT_STOCK_DAILY_TABLE,
        symbol_col: str = "ticker",
        exclude_created_at_in_query: bool = False,
        client=None,
    ) -> None:
        self.ticker = self._normalize_symbol(ticker)
        self._start_input = _to_date(start)
        self._end_input = _to_date(end)
        if self._start_input is not None and self._end_input is not None and self._start_input > self._end_input:
            raise ValueError("start must be <= end")
        self.start: date | None = self._start_input
        self.end: date | None = self._end_input

        self.project = DEFAULT_PROJECT
        self.daily_table = str(daily_table).strip()
        self.symbol_col = str(symbol_col).strip()
        self._exclude_created_at_in_query = bool(exclude_created_at_in_query)
        self._flds = _normalize_flds(flds)
        self._client = client

        self._daily_cache: dict[tuple[str, ...] | None, pd.DataFrame] = {}

    def _normalize_symbol(self, value: str) -> str:
        return str(value).strip()

    @property
    def client(self):
        if self._client is None:
            self._client = bq_client(self.project)
        return self._client

    def _resolve_date_bounds(self, refresh: bool = False) -> tuple[date, date]:
        should_recompute = refresh and (self._start_input is None or self._end_input is None)
        if should_recompute:
            self.start = self._start_input
            self.end = self._end_input

        if self.start is None or self.end is None:
            min_date, max_date = fetch_daily_date_bounds(
                self.client,
                project=self.project,
                table=self.daily_table,
                symbol=self.ticker,
                symbol_col=self.symbol_col,
                normalize_symbol_fn=self._normalize_symbol,
                use_cache=not refresh,
            )
            if self.start is None:
                self.start = min_date
            if self.end is None:
                self.end = max_date

        if self.start is None or self.end is None:
            raise ValueError("조회 기간을 확정할 수 없습니다.")
        if self.start > self.end:
            raise ValueError("start must be <= end")
        return self.start, self.end

    def get_data(
        self,
        refresh: bool = False,
        copy: bool = True,
    ) -> pd.DataFrame:
        if refresh:
            self._daily_cache.clear()
            if self._start_input is None or self._end_input is None:
                self.start = self._start_input
                self.end = self._end_input
        field_key = self._flds
        query_flds = field_key
        if field_key is not None and "date" not in field_key:
            query_flds = tuple(["date", *field_key])
        query_start = self.start
        query_end = self.end
        if query_start is not None and query_end is not None and query_start > query_end:
            raise ValueError("start must be <= end")
        processed_key = (
            "daily_single",
            self.project,
            self.daily_table,
            self.symbol_col,
            self.ticker,
            query_start,
            query_end,
            field_key,
        )
        if not refresh and field_key not in self._daily_cache and processed_key in _PROCESSED_DATA_CACHE:
            self._daily_cache[field_key] = _PROCESSED_DATA_CACHE[processed_key]

        if refresh or field_key not in self._daily_cache:
            data = fetch_daily(
                self.client,
                project=self.project,
                table=self.daily_table,
                symbol=self.ticker,
                symbol_col=self.symbol_col,
                normalize_symbol_fn=self._normalize_symbol,
                start=query_start,
                end=query_end,
                flds=query_flds,
                exclude_created_at_in_query=self._exclude_created_at_in_query,
                use_cache=not refresh,
            )
            data = _set_date_index(data, date_col="date")
            if self.start is None and len(data.index) > 0:
                self.start = data.index.min().date()
            if self.end is None and len(data.index) > 0:
                self.end = data.index.max().date()
            if field_key is not None:
                selected_cols = [c for c in field_key if c != "date" and c in data.columns]
                data = data.loc[:, selected_cols]
            self._daily_cache[field_key] = data
            _PROCESSED_DATA_CACHE[processed_key] = data
        return self._daily_cache[field_key].copy() if copy else self._daily_cache[field_key]


class BigQueryStockData(BigQueryData):
    STOCK_DAILY_EXTRA_WHERE = (
        "is_tradable IS TRUE",
        "(dept IS NULL OR dept NOT IN ('SPAC', '관리종목', '투자주의환기종목', '외국기업'))",
    )
    DEFAULT_OUTPUT_FIELDS = ("ticker", "name")

    def __init__(
        self,
        ticker: str | None = None,
        tickers: Sequence[str] | None = None,
        market: Sequence[str] | None = None,
        start: str | date | None = None,
        end: str | date | None = None,
        flds: Sequence[str] | None = None,
        daily_table: str = DEFAULT_STOCK_DAILY_TABLE,
        event_table: str = DEFAULT_EVENT_TABLE,
        symbol_col: str = "ticker",
        client=None,
    ) -> None:
        if ticker is not None and tickers is not None:
            raise ValueError("ticker와 tickers는 동시에 지정할 수 없습니다.")
        if ticker is not None:
            symbols_raw: Sequence[str] | None = [ticker]
        else:
            symbols_raw = tickers

        normalized_symbols = _normalize_symbols(symbols_raw, normalize_symbol_fn=normalize_ticker)
        if normalized_symbols is not None and len(normalized_symbols) == 0:
            raise ValueError("tickers가 비어 있습니다.")

        representative = normalized_symbols[0] if normalized_symbols is not None else "__ALL__"
        super().__init__(
            ticker=representative,
            start=start,
            end=end,
            flds=flds,
            daily_table=daily_table,
            symbol_col=symbol_col,
            exclude_created_at_in_query=False,
            client=client,
        )
        self.tickers: tuple[str, ...] | None = normalized_symbols
        self._markets: tuple[str, ...] | None = _normalize_markets(market)
        self.event_table = str(event_table).strip()
        self._event_cache: pd.DataFrame | None = None
        self._adjusted_cache: dict[tuple[str, ...] | None, pd.DataFrame] = {}

    def _normalize_symbol(self, value: str) -> str:
        return normalize_ticker(value)

    def _resolve_event_symbols(self, refresh: bool = False) -> tuple[str, ...] | None:
        if self.tickers is not None:
            return self.tickers
        if self._markets is None:
            return None
        daily = self.get_data(refresh=refresh, copy=False)
        if self.symbol_col not in daily.columns:
            return None
        symbols = (
            daily[self.symbol_col]
            .dropna()
            .astype(str)
            .map(self._normalize_symbol)
            .tolist()
        )
        return tuple(dict.fromkeys(symbols))

    def _resolve_date_bounds(self, refresh: bool = False) -> tuple[date, date]:
        should_recompute = refresh and (self._start_input is None or self._end_input is None)
        if should_recompute:
            self.start = self._start_input
            self.end = self._end_input

        if self.start is None or self.end is None:
            min_date, max_date = fetch_daily_date_bounds_batch(
                self.client,
                project=self.project,
                table=self.daily_table,
                symbols=self.tickers,
                symbol_col=self.symbol_col,
                normalize_symbol_fn=self._normalize_symbol,
                use_cache=not refresh,
            )
            if self.start is None:
                self.start = min_date
            if self.end is None:
                self.end = max_date

        if self.start is None or self.end is None:
            raise ValueError("조회 기간을 확정할 수 없습니다.")
        if self.start > self.end:
            raise ValueError("start must be <= end")
        return self.start, self.end

    def get_data(
        self,
        refresh: bool = False,
        copy: bool = True,
    ) -> pd.DataFrame:
        if refresh:
            self._daily_cache.clear()
            self._adjusted_cache.clear()
            if self._start_input is None or self._end_input is None:
                self.start = self._start_input
                self.end = self._end_input

        field_key = self._flds
        query_flds = field_key
        must_have = ["date", self.symbol_col]
        default_output_fields = list(self.DEFAULT_OUTPUT_FIELDS)
        if query_flds is None:
            query_flds_with_keys = None
        else:
            query_flds_with_keys = tuple(dict.fromkeys([*query_flds, *must_have, *default_output_fields]))

        query_start = self.start
        query_end = self.end
        if query_start is not None and query_end is not None and query_start > query_end:
            raise ValueError("start must be <= end")
        processed_key = (
            "daily_batch",
            self.project,
            self.daily_table,
            self.symbol_col,
            self.tickers,
            query_start,
            query_end,
            field_key,
            self.STOCK_DAILY_EXTRA_WHERE,
            self._markets,
        )
        if not refresh and field_key not in self._daily_cache and processed_key in _PROCESSED_DATA_CACHE:
            self._daily_cache[field_key] = _PROCESSED_DATA_CACHE[processed_key]

        if refresh or field_key not in self._daily_cache:
            data = fetch_daily_batch(
                self.client,
                project=self.project,
                table=self.daily_table,
                symbols=self.tickers,
                symbol_col=self.symbol_col,
                normalize_symbol_fn=self._normalize_symbol,
                start=query_start,
                end=query_end,
                flds=query_flds_with_keys,
                extra_where=self.STOCK_DAILY_EXTRA_WHERE,
                market_filter=self._markets,
                use_cache=not refresh,
            )
            data = _set_date_index(data, date_col="date")
            if self.start is None and len(data.index) > 0:
                self.start = data.index.min().date()
            if self.end is None and len(data.index) > 0:
                self.end = data.index.max().date()
            if field_key is not None:
                selected_cols = [c for c in default_output_fields if c in data.columns]
                selected_cols.extend(
                    [c for c in field_key if c not in ("date", *default_output_fields) and c in data.columns]
                )
                data = data.loc[:, selected_cols]
            self._daily_cache[field_key] = data
            _PROCESSED_DATA_CACHE[processed_key] = data
        return self._daily_cache[field_key].copy() if copy else self._daily_cache[field_key]

    def get_adjust_factors(self, refresh: bool = False) -> pd.DataFrame:
        if refresh:
            self._event_cache = None
            self._adjusted_cache.clear()
            if self._start_input is None or self._end_input is None:
                self.start = self._start_input
                self.end = self._end_input
        if self.start is not None and self.end is not None and self.start > self.end:
            raise ValueError("start must be <= end")
        if refresh or self._event_cache is None:
            event_symbols = self._resolve_event_symbols(refresh=refresh)
            events = fetch_events_batch(
                self.client,
                project=self.project,
                table=self.event_table,
                symbols=event_symbols,
                symbol_col=self.symbol_col,
                normalize_symbol_fn=self._normalize_symbol,
                start=self.start,
                end=self.end,
                use_cache=not refresh,
            )
            self._event_cache = _set_date_index(events, date_col="date")
        return self._event_cache.copy()

    def get_adj_data(
        self,
        refresh: bool = False,
    ) -> pd.DataFrame:
        field_key = self._flds
        default_output_fields = list(self.DEFAULT_OUTPUT_FIELDS)
        if refresh or field_key not in self._adjusted_cache:
            calc_fields = field_key
            if calc_fields is not None:
                calc_fields = tuple(dict.fromkeys([*calc_fields, self.symbol_col, *default_output_fields]))

            if calc_fields == field_key:
                daily = self.get_data(refresh=refresh, copy=False)
            else:
                query_start = self.start
                query_end = self.end
                if query_start is not None and query_end is not None and query_start > query_end:
                    raise ValueError("start must be <= end")
                calc_query_fields = tuple(dict.fromkeys([*calc_fields, "date", self.symbol_col]))
                daily = fetch_daily_batch(
                    self.client,
                    project=self.project,
                    table=self.daily_table,
                    symbols=self.tickers,
                    symbol_col=self.symbol_col,
                    normalize_symbol_fn=self._normalize_symbol,
                    start=query_start,
                    end=query_end,
                    flds=calc_query_fields,
                    extra_where=self.STOCK_DAILY_EXTRA_WHERE,
                    market_filter=self._markets,
                    use_cache=not refresh,
                )
                daily = _set_date_index(daily, date_col="date")
                if self.start is None and len(daily.index) > 0:
                    self.start = daily.index.min().date()
                if self.end is None and len(daily.index) > 0:
                    self.end = daily.index.max().date()
            events = self.get_adjust_factors(refresh=refresh)
            adjusted = apply_adjustment(daily=daily, events=events, symbol_col=self.symbol_col)
            adjusted = _set_date_index(adjusted, date_col="date")

            if field_key is not None:
                selected_cols = [c for c in default_output_fields if c in adjusted.columns]
                selected_cols.extend(
                    [c for c in field_key if c not in ("date", *default_output_fields) and c in adjusted.columns]
                )
                adjusted = adjusted.loc[:, selected_cols]
            self._adjusted_cache[field_key] = adjusted
        return self._adjusted_cache[field_key].copy()

    def _fetch_fdr_frame(self, ticker: str, source: str = "YAHOO") -> pd.DataFrame:
        target_ticker = self._normalize_symbol(ticker)
        try:
            import FinanceDataReader as fdr
        except Exception as exc:
            raise ImportError(
                "FinanceDataReader가 필요합니다. "
                "metricstudio 환경에서 `python -m pip install finance-datareader`를 실행하세요."
            ) from exc

        source_key = str(source).strip().upper() or "YAHOO"
        start_arg = self.start.isoformat() if self.start is not None else None
        end_arg = self.end.isoformat() if self.end is not None else None

        def _read_fdr(symbol: str, src: str | None = None):
            if start_arg is None and end_arg is None:
                if src is None:
                    return fdr.DataReader(symbol)
                return fdr.DataReader(symbol, data_source=src)
            if start_arg is not None and end_arg is None:
                if src is None:
                    return fdr.DataReader(symbol, start_arg)
                return fdr.DataReader(symbol, start_arg, data_source=src)
            if start_arg is None and end_arg is not None:
                if src is None:
                    return fdr.DataReader(symbol, None, end_arg)
                return fdr.DataReader(symbol, None, end_arg, src)
            if src is None:
                return fdr.DataReader(symbol, start_arg, end_arg)
            return fdr.DataReader(symbol, start_arg, end_arg, src)

        def _guess_yahoo_symbol(tk: str) -> str:
            suffix = "KS"
            try:
                probe = fetch_daily_batch(
                    self.client,
                    project=self.project,
                    table=self.daily_table,
                    symbols=[tk],
                    symbol_col=self.symbol_col,
                    normalize_symbol_fn=self._normalize_symbol,
                    start=self.start,
                    end=self.end,
                    flds=["date", self.symbol_col, "market"],
                    extra_where=self.STOCK_DAILY_EXTRA_WHERE,
                    market_filter=self._markets,
                    use_cache=True,
                )
                if "market" in probe.columns:
                    m = probe["market"].dropna()
                    if not m.empty:
                        mk = str(m.iloc[0]).strip().lower()
                        if "kosdaq" in mk or "kq" in mk:
                            suffix = "KQ"
                        elif "kospi" in mk or "ks" in mk:
                            suffix = "KS"
            except Exception:
                pass
            return f"{tk}.{suffix}"

        if source_key == "YAHOO":
            yahoo_symbol = _guess_yahoo_symbol(target_ticker)
            candidates = [
                f"YAHOO:{yahoo_symbol}",
                f"YAHOO:{target_ticker}",
            ]
            last_exc: Exception | None = None
            frame = None
            for sym in candidates:
                try:
                    frame = _read_fdr(sym)
                    if frame is not None and not frame.empty:
                        break
                except Exception as exc:
                    last_exc = exc
                    frame = None
            if frame is None:
                raise ValueError(f"Yahoo 데이터를 가져오지 못했습니다: {target_ticker}") from last_exc
        else:
            symbol_with_source = f"{source_key}:{target_ticker}"
            try:
                frame = _read_fdr(symbol_with_source)
            except Exception:
                try:
                    frame = _read_fdr(target_ticker, source_key)
                except TypeError:
                    frame = _read_fdr(target_ticker)

        if frame is None or frame.empty:
            raise ValueError(f"FinanceDataReader 조회 결과가 없습니다. source={source_key}")
        if not isinstance(frame, pd.DataFrame):
            raise TypeError("FinanceDataReader 결과는 DataFrame이어야 합니다.")
        return frame

    def plot_with_fdr(
        self,
        ticker: str | None = None,
        source: str = "YAHOO",
        figsize: tuple[int, int] = (12, 5),
        refresh: bool = False,
        ax=None,
    ):
        try:
            import matplotlib.pyplot as plt
        except Exception as exc:
            raise ImportError(
                "matplotlib이 필요합니다. "
                "metricstudio 환경에서 `python -m pip install matplotlib`를 실행하세요."
            ) from exc

        if ticker is not None:
            target_ticker = self._normalize_symbol(ticker)
        elif self.tickers is not None and len(self.tickers) == 1:
            target_ticker = self.tickers[0]
        else:
            raise ValueError("다중/전종목 객체에서는 plot_with_fdr(ticker='005930')처럼 ticker를 지정하세요.")

        single = BigQueryStockData(
            ticker=target_ticker,
            market=self._markets,
            start=self.start,
            end=self.end,
            flds=["close"],
            daily_table=self.daily_table,
            event_table=self.event_table,
            symbol_col=self.symbol_col,
            client=self.client,
        )
        adjusted = single.get_adj_data(refresh=refresh)
        bq_series = pd.to_numeric(adjusted["close"], errors="coerce").dropna()

        fdr_frame = self._fetch_fdr_frame(ticker=target_ticker, source=source)
        fdr_frame.index = pd.to_datetime(fdr_frame.index, errors="coerce")
        fdr_frame = fdr_frame[fdr_frame.index.notna()]
        source_key = str(source).strip().upper() or "YAHOO"
        if source_key == "YAHOO":
            close_candidates = ["Adj Close", "AdjClose"]
        else:
            close_candidates = ["Adj Close", "AdjClose", "Close", "종가"]
        close_col = next((c for c in close_candidates if c in fdr_frame.columns), None)
        if close_col is None:
            raise ValueError(
                f"FinanceDataReader 결과에 비교용 가격 컬럼이 없습니다. columns={list(fdr_frame.columns)}"
            )
        fdr_series = pd.to_numeric(fdr_frame[close_col], errors="coerce").dropna()

        if ax is None:
            _, ax = plt.subplots(figsize=figsize)
        bq_series.plot(ax=ax, linewidth=2, label="BigQuery adjusted close")
        fdr_series.plot(ax=ax, linewidth=1.8, alpha=0.85, label=f"FDR {close_col} ({source})")
        ax.set_title(f"{target_ticker} adjusted close comparison")
        ax.set_xlabel("date")
        ax.set_ylabel("price")
        ax.grid(True, alpha=0.3)
        ax.legend()
        return ax


class BigQueryIndexData(BigQueryData):
    def __init__(
        self,
        ticker: str,
        start: str | date | None = None,
        end: str | date | None = None,
        flds: Sequence[str] | None = None,
        daily_table: str = DEFAULT_INDEX_DAILY_TABLE,
        symbol_col: str = "name",
        client=None,
    ) -> None:
        super().__init__(
            ticker=ticker,
            start=start,
            end=end,
            flds=flds,
            daily_table=daily_table,
            symbol_col=symbol_col,
            exclude_created_at_in_query=True,
            client=client,
        )

    def get_data(
        self,
        refresh: bool = False,
        copy: bool = False,
    ) -> pd.DataFrame:
        return super().get_data(refresh=refresh, copy=copy)

def main(argv: Sequence[str] | None = None) -> None:
    inp = parse_args(argv)
    loader = BigQueryStockData(
        ticker=inp.ticker,
        start=inp.start,
        end=inp.end,
        daily_table=inp.daily_table,
        event_table=inp.event_table,
    )
    adjusted = loader.get_adj_data()
    print(
        f"ticker={inp.ticker} rows={len(adjusted)} range={inp.start.isoformat()}..{inp.end.isoformat()}",
        file=sys.stderr,
    )

    if inp.output:
        adjusted.to_csv(inp.output, index=True, index_label="date")
    else:
        adjusted.to_csv(sys.stdout, index=True, index_label="date")


if __name__ == "__main__":
    main()
