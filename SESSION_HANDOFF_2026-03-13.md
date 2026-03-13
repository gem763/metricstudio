# Session Handoff (2026-03-13)

## 1) 작업 폴더 이슈 (중요)
- Codex가 실제로 작업한 경로: `/mnt/c/Users/USER/Documents/GitHub/metricstudio`
- 사용자가 새로 쓰려는 경로: `~/code/metricstudio` (`/root/code/metricstudio`)
- 두 경로는 **서로 다른 복사본**임.
- 따라서 새 세션에서 `/root/code/metricstudio`를 열면, 이번 세션 변경사항이 안 보일 수 있음.

권장 이관:
```bash
rsync -av /mnt/c/Users/USER/Documents/GitHub/metricstudio/src/db_manager.py ~/code/metricstudio/src/db_manager.py
rsync -av /mnt/c/Users/USER/Documents/GitHub/metricstudio/db/adj_factor.parquet ~/code/metricstudio/db/
rsync -av /mnt/c/Users/USER/Documents/GitHub/metricstudio/db/index.parquet ~/code/metricstudio/db/
rsync -av /mnt/c/Users/USER/Documents/GitHub/metricstudio/db/stock-*.parquet ~/code/metricstudio/db/
rsync -av /mnt/c/Users/USER/Documents/GitHub/metricstudio/db/adjusted-stock-*.parquet ~/code/metricstudio/db/
```

## 2) 이번 세션에서 완료된 내용

### 데이터 파일 생성/정리
- `db/index.parquet` 생성 완료 (`kr_index.daily` 원본)
- `db/adj_factor.parquet` 생성 완료 (`kr_events.adj_factor` 원본)
- `db/stock.parquet` 삭제
- `db/stock-1995.parquet` ~ `db/stock-2026.parquet` 생성 (연도 분할)
- `db/adjusted-stock-1995.parquet` ~ `db/adjusted-stock-2026.parquet` 생성

### 인덱스 및 dtype 정리
- `stock-*` 인덱스: `(date, ticker, name)` MultiIndex
- `index.parquet` 인덱스: `(date, name)` MultiIndex
- `adj_factor.parquet` 인덱스: `(date, ticker)` MultiIndex
- `stock-*` 원본 컬럼 dtype:
  - `open/high/low/close/amount/market_cap/volume/shares_outstanding` => `int64`

### 코드 변경
- 파일: `src/db_manager.py`
- 추가 함수 1: `load_adjusted_stock_from_parquet` (line ~22)
  - 입력: `db/stock-*.parquet`, `db/adj_factor.parquet`
  - 출력: `(date, ticker, name)` MultiIndex DataFrame
  - 조정 규칙:
    - `open/high/low/close` 곱셈
    - `volume/shares_outstanding` 나눗셈
    - `amount/market_cap` 유지
  - 기본적으로 `rank`, `created_at` drop
- 추가 함수 2: `build_adjusted_stock_yearly_parquet` (line ~107)
  - `adjusted-stock-YYYY.parquet`를 `db/`에 직접 생성

## 3) 미완료 TODO (다음 세션 우선)
- 사용자 합의사항: `duckdb` 헬퍼를 추가하기로 했지만, **아직 미구현**.
- 다음 세션에서 `src/db_manager.py`에 아래 함수 추가 권장:

1. `query_adjusted_stock_duckdb(sql: str, db_root_dir: str|Path|None = None) -> pd.DataFrame`
2. `load_adjusted_stock_duckdb(columns=None, start=None, end=None, tickers=None, db_root_dir=None) -> pd.DataFrame`

요구사항:
- 대상 파일: `db/adjusted-stock-*.parquet`
- 컬럼 projection + date/ticker filter pushdown
- 필요할 때만 pandas로 materialize (`.df()`)
- 전체 메모리 로드 최소화

## 4) 새 세션에서 즉시 검증할 명령
```bash
source ~/miniconda3/etc/profile.d/conda.sh && conda activate metricstudio
python - <<'PY'
from pathlib import Path
import pandas as pd
from src.db_manager import load_adjusted_stock_from_parquet, build_adjusted_stock_yearly_parquet

print("has_fn_1", callable(load_adjusted_stock_from_parquet))
print("has_fn_2", callable(build_adjusted_stock_yearly_parquet))

paths = sorted(Path("db").glob("adjusted-stock-*.parquet"))
print("adjusted_files", len(paths))
if paths:
    df = pd.read_parquet(paths[-1])
    print("sample_idx", type(df.index).__name__, df.index.names, len(df))
PY
```

## 5) 참고 메모
- 전체 연도 데이터를 pandas로 한 번에 올리면 WSL 메모리 부담이 큼.
- 따라서 실무 흐름은:
  1) 연도별 `adjusted-stock-*`를 미리 생성
  2) 분석 시 duckdb/polars lazy로 스캔
  3) 최종 소형 결과만 pandas로 변환
