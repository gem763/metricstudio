from __future__ import annotations

from pathlib import Path
import pandas as pd


class DB:
    def __init__(self, static_dir: str | Path | None = None) -> None:
        if static_dir is None:
            self.static_dir = Path(__file__).resolve().parents[1] / "static"
        else:
            self.static_dir = Path(static_dir)

    def picklize(
        self,
        excel_path: str | Path | None = None,
        series_pkl: str = "kospi_adjclose.pkl",
        mapping_pkl: str = "kospi_code_name.pkl",
    ) -> None:
        """
        Convert the Excel file to two pickle files in the static directory.
        """
        excel_path = Path(excel_path) if excel_path else self.static_dir / "kospi adjclose.xlsx"

        df = pd.read_excel(excel_path, header=[0, 1]).copy()
        if df.empty:
            raise ValueError("엑셀 파일이 비어 있습니다.")

        date_col = df.columns[0]
        dates = pd.to_datetime(df[date_col], errors="coerce")
        if dates.isna().all():
            raise ValueError("날짜 컬럼을 인식하지 못했습니다.")

        price_df = df.drop(columns=[date_col])
        codes = [c[0] for c in price_df.columns]
        names = [c[1] for c in price_df.columns]

        code_name = pd.Series(names, index=codes, name="name")

        price_df.columns = codes
        price_df.index = dates

        # Keep NaN rows by using melt (stack drops NA in newer pandas).
        long_df = (
            price_df.reset_index(names="date")
            .melt(id_vars="date", var_name="code", value_name="adjclose")
        )
        price_series = long_df.set_index(["date", "code"])["adjclose"].dropna()

        price_series.to_pickle(self.static_dir / series_pkl)
        code_name.to_pickle(self.static_dir / mapping_pkl)

        return None
