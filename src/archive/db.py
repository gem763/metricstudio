from __future__ import annotations

from pathlib import Path
from typing import Iterable, List
import re

import pandas as pd


class DB:
    def __init__(self, static_dir: str | Path | None = None) -> None:
        if static_dir is None:
            self.static_dir = Path(__file__).resolve().parents[1] / "static"
        else:
            self.static_dir = Path(static_dir)

    def _read_excel_frame(self, path: Path) -> tuple[pd.DataFrame, pd.Series]:
        df = pd.read_excel(path, header=[0, 1]).copy()
        if df.empty:
            raise ValueError(f"엑셀 파일이 비어 있습니다: {path}")

        date_col = df.columns[0]
        dates = pd.to_datetime(df[date_col], errors="coerce")
        if dates.isna().all():
            raise ValueError(f"날짜 컬럼을 인식하지 못했습니다: {path}")

        price_df = df.drop(columns=[date_col])
        codes = [c[0] for c in price_df.columns]
        names = [c[1] for c in price_df.columns]

        code_name = pd.Series(names, index=codes, name="name")

        price_df.columns = codes
        price_df.index = dates

        return price_df, code_name

    def _frame_to_series(self, price_df: pd.DataFrame) -> pd.Series:
        long_df = (
            price_df.reset_index(names="date")
            .melt(id_vars="date", var_name="code", value_name="adjclose")
        )
        return long_df.set_index(["date", "code"])["adjclose"].dropna()

    def _split_columns(self, columns: List[str], parts: int) -> List[List[str]]:
        if parts <= 0:
            raise ValueError("parts는 1 이상이어야 합니다.")
        if parts > len(columns):
            raise ValueError("분할 개수가 종목코드 수보다 많습니다.")

        n = len(columns)
        k, m = divmod(n, parts)
        out: List[List[str]] = []
        start = 0
        for i in range(parts):
            end = start + k + (1 if i < m else 0)
            out.append(columns[start:end])
            start = end
        return out

    def picklize(
        self,
        excel_paths: Iterable[str | Path] | None = None,
        series_pkls: Iterable[str] | None = None,
        mapping_pkl: str = "code_name.pkl",
        split_parts: int = 2,
    ) -> None:
        """
        Read multiple Excel files and split each into multiple adjclose pickles.
        Also save a merged code-name mapping pickle.
        """
        if excel_paths is None:
            excel_paths = [
                self.static_dir / "수정주가1.xlsx",
                self.static_dir / "수정주가2.xlsx",
            ]

        excel_paths = [Path(p) for p in excel_paths]

        total_parts = len(excel_paths) * split_parts
        if series_pkls is None:
            series_pkls = [f"adjclose{i}.pkl" for i in range(total_parts)]

        series_pkls = list(series_pkls)
        if len(series_pkls) != total_parts:
            raise ValueError("series_pkls 길이는 excel_paths * split_parts와 같아야 합니다.")

        code_name_list: List[pd.Series] = []
        pkl_iter = iter(series_pkls)

        for path in excel_paths:
            price_df, code_name = self._read_excel_frame(path)
            code_name_list.append(code_name)

            col_parts = self._split_columns(list(price_df.columns), split_parts)
            for cols in col_parts:
                part_df = price_df[cols]
                price_series = self._frame_to_series(part_df)
                pkl_name = next(pkl_iter)
                price_series.to_pickle(self.static_dir / pkl_name)

        code_name_all = pd.concat(code_name_list)
        dup_codes = code_name_all.index[code_name_all.index.duplicated()].unique().tolist()
        if dup_codes:
            raise ValueError(f"중복 종목코드가 있습니다: {dup_codes}")

        code_name_all.to_pickle(self.static_dir / mapping_pkl)

        return None

    def load(
        self,
        series_pkls: Iterable[str | Path] | None = None,
        mapping_pkl: str = "code_name.pkl",
        exclude_spac: bool = True,
    ) -> pd.Series:
        """
        Load multiple adjclose pickles and merge into a single Series.
        """
        if series_pkls is None:
            series_pkls = [
                self.static_dir / "adjclose0.pkl",
                self.static_dir / "adjclose1.pkl",
                self.static_dir / "adjclose2.pkl",
                self.static_dir / "adjclose3.pkl",
            ]

        codes_to_exclude: set[str] = set()
        if exclude_spac:
            mapping_path = self.static_dir / mapping_pkl
            if not mapping_path.exists():
                raise FileNotFoundError(f"종목명 매핑 파일이 없습니다: {mapping_path}")
            code_name = pd.read_pickle(mapping_path)
            if not isinstance(code_name, pd.Series):
                raise TypeError(f"{mapping_path}는 pandas Series여야 합니다.")
            pattern = re.compile(r"(?:스팩\d+호|\d+호스팩)")
            mask = code_name.astype(str).str.contains(pattern, regex=True, na=False)
            codes_to_exclude = set(code_name[mask].index.astype(str))

        series_list: List[pd.Series] = []
        for pkl_path in series_pkls:
            pkl_path = Path(pkl_path)
            series = pd.read_pickle(pkl_path)
            if not isinstance(series, pd.Series):
                raise TypeError(f"{pkl_path}는 pandas Series여야 합니다.")
            if codes_to_exclude:
                codes = series.index.get_level_values("code").astype(str)
                series = series[~codes.isin(codes_to_exclude)]
            series_list.append(series)

        merged = pd.concat(series_list)
        dup_index = merged.index[merged.index.duplicated()].unique()
        if len(dup_index) > 0:
            raise ValueError("중복 (date, code) 인덱스가 있습니다.")

        merged = merged.sort_index()
        return merged
