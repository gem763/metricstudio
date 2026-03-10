# CONTEXT (2026-03-10)

## 1) 운영 규칙
- 이 저장소 실행은 항상 `metricstudio` 가상환경에서 진행.
- 기본 실행:
  - `source ~/miniconda3/etc/profile.d/conda.sh && conda activate metricstudio`

## 2) 현재 작업 목표
- 패턴의 전구간 성과(analyze)와 코호트 실행 성과(run) 간 괴리를 줄이기.
- 핵심은 "분류력(gate classification power)" 검증.
- 롱온리 기준에서 게이트 유효성이 있는 파라미터 조합(`target_horizon`, `aggregate_lookback`) 찾기.

## 3) 핵심 개념 정리
- `event` vs `day` 집계 차이:
  - `event`: 모든 (날짜,종목) 이벤트를 동일 가중.
  - `day`: 날짜별 바스켓 평균을 먼저 구한 뒤 날짜를 동일 가중.
- 코호트 실행과 일관성은 일반적으로 `by='day'` 쪽이 높음.

## 4) 현재 코드 상태 (중요)

### analyze
- 시그니처:
  - `Backtest.analyze(*patterns, include_base=True, by='day', min_marketcap=None, marketcap_top_pct=None, cohort_top_n=None, top_n_type='marketcap')`
- `by`는 `'event'` 또는 `'day'`.
- 기본값은 `by='day'`.
- analyze에서 사용한 필터 설정은 pattern별로 저장되고, run/diagnose_gate에서 자동 상속됨.
- 현재 구조상 analyze 옵션은 패턴/벤치마크(모든주식)에 동일하게 적용됨.

### run
- 시그니처:
  - `Backtest.run(..., fallback_exposure=0.5, gate_geom_min=0.0, gate_arith_min=0.0, gate_rise_min=0.5, gate_use_geom=False, gate_use_arith=False, gate_use_rise=False, ...)`
- 게이트 판정식(활성화된 조건만 적용):
  - `pattern_geom  > max(gate_geom_min, market_geom)`
  - `pattern_arith > max(gate_arith_min, market_arith)`
  - `pattern_rise  > max(gate_rise_min, market_rise)`
- `gate_use_geom/gate_use_arith/gate_use_rise` 기본값은 모두 `False`.
- 단, 3개가 모두 `False`면 `ValueError` 발생(최소 1개는 True 필요).

### diagnose_gate
- 시그니처도 run과 동일한 gate 파라미터 구조.
- `fallback_exposure`, `gate_mode`, `*_floor`, `*_margin` 계열은 제거됨.
- 출력:
  - Return Split
  - Win Rate Split
  - Classification (precision/recall/f1)
  - DQS (return/win/class/quality/total)

### Simulator.plot
- 좌측 패널: 게이트 지표 spread 3개(산술/기하/상승확률).
- 승률/손익비는 현재 코호트 기준 집계.

## 5) 최근 게이트 스윕 결과 (볼린저돌파+52주고가)

### 공통 설정
- 패턴:
  - `bb = Bollinger(name='볼린저돌파').on(trigger='breakout_up', breakout_cooldown_days=3, bandwidth_max=0.05)`
  - `high52w = High(name='52주 고가').on(window=252, threshold=0.90, stay_days=1)`
  - `pat = bb + high52w`
- analyze:
  - `by='day', marketcap_top_pct=0.7, cohort_top_n=10, top_n_type='liquidity'`
- diagnose:
  - `trade_price_mode='당일종가'`
  - `gate_geom_min=0.0, gate_arith_min=0.0, gate_rise_min=0.5`
- grid:
  - `target_horizon in {5,10,15,20,40,60,120}`
  - `aggregate_lookback in {60,90,120,180,250,360,500}`

### full-sample 요약
- `geom_only` (`geom=True, arith=False, rise=False`)
  - best DQS: `(60, 90)` → `0.7945`
  - robust best: `(60, 90)` → `0.6528`
- `arith_only` (`geom=False, arith=True, rise=False`)
  - best DQS: `(60, 500)` → `0.7916`
  - robust best: `(40, 120)` → `0.6490`
- `rise_only` (`geom=False, arith=False, rise=True`)
  - best DQS: `(60, 90)` → `0.7752`
  - robust best: `(40, 120)` → `0.6310`

### 분할 검증 (2000-2012 vs 2013-2025)
- `geom_only (60,90)`: `0.754` vs `0.767` (가장 안정적)
- `geom_only (40,90)`: `0.777` vs `0.736` (안정)
- `rise_only (60,120)`: `0.768` vs `0.709` (중간 안정)
- `arith_only (40,120)`: `0.764` vs `0.319` (구간 의존 큼)

### 실무 권장 (현재 기준)
1. 1순위: `geom_only`, `target_horizon=40~60`, `aggregate_lookback=90~120` (대표: `60,90`)
2. 2순위: `rise_only`, `target_horizon=40~60`, `aggregate_lookback=90~180` (대표: `60,120`)
3. `arith_only` 단독 주게이트는 보조조건 용도로만 사용 권장

## 6) 바로 실행 템플릿
```python
from src.backtest import Backtest
from src.pattern import Pattern, Bollinger, High

bm = Pattern(name='모든주식')
bt = Backtest('2000-01-01', '2025-12-31', benchmark=bm)

bb = Bollinger(name='볼린저돌파').on(trigger='breakout_up', breakout_cooldown_days=3, bandwidth_max=0.05)
high52w = High(name='52주 고가').on(window=252, threshold=0.90, stay_days=1)
pat = bb + high52w

stats = bt.analyze(
    bb, pat,
    by='day',
    marketcap_top_pct=0.7,
    cohort_top_n=10,
    top_n_type='liquidity',
)

diag = bt.diagnose_gate(
    pattern='볼린저돌파 + 52주 고가',
    target_horizon=60,
    aggregate_lookback=90,
    trade_price_mode='당일종가',
    gate_use_geom=True,
    gate_use_arith=False,
    gate_use_rise=False,
)
diag.plot()

sim = bt.run(
    pattern='볼린저돌파 + 52주 고가',
    target_horizon=60,
    aggregate_lookback=90,
    trade_price_mode='당일종가',
    gate_use_geom=True,
    gate_use_arith=False,
    gate_use_rise=False,
    fallback_exposure=0.5,
)
sim.plot()
```

## 7) 참고 산출 파일
- `tmp_gate_grid.csv`
- `tmp_gate_grid_summary.json`
- `tmp_gate_grid_v2.csv`
- `tmp_gate_grid_v2_summary.json`
- `tmp_gate_grid_v2_walkforward.csv`
- `tmp_gate_grid_v2_*_dqs_pivot.csv`
- `tmp_gate_grid_v2_*_robust_pivot.csv`

## 8) 주의사항
- 지원 호라이즌은 `{5,10,15,20,40,60,120}`. (`30`은 지원 안 함)
- `gate_use_*` 모두 False면 에러가 정상 동작.
