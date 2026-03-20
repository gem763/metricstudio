# API

이 문서는 `metricstudio`의 핵심 사용자 API를 구현 기준으로 정리한다.
패턴 자체는 분량이 크므로 별도 문서인 [`패턴 가이드`](%ED%8C%A8%ED%84%B4%20%EA%B0%80%EC%9D%B4%EB%93%9C.md)로 분리했다.

관련 문서:
- [패턴 가이드](%ED%8C%A8%ED%84%B4%20%EA%B0%80%EC%9D%B4%EB%93%9C.md)
- [stay_cooldown_mask 매뉴얼](stay%EC%99%80%20cooldown.md)

## 1. 핵심 객체

### 1.1 `Univ`

목적:
- 백테스트가 처음부터 어떤 종목 집합을 로드할지 정하는 유니버스 설정 객체

기본 형태:

```python
from metricstudio import Univ

univ = Univ(
    market=["KOSPI", "KOSDAQ"],
    is_tradable=True,
    exclude_reits=True,
)
```

주요 입력:
- `market`: 대상 시장 목록. 예: `["KOSPI", "KOSDAQ"]`
- `is_tradable`: 거래 가능 종목만 남길지 여부
- `dept_excludes`: 제외할 부서/분류 목록
- `exclude_reits`: 리츠성 종목 제외 여부. 기본값은 `True`

의미:
- `Univ`는 `Backtest(...)` 생성 시점에 적용된다.
- 즉, 어떤 종목 가격 테이블을 읽어올지 자체를 결정한다.
- benchmark와 사용자 패턴은 모두 같은 `Univ` 위에서 계산된다.

언제 쓰나:
- 시장 자체를 KOSPI/KOSDAQ로 제한하고 싶을 때
- 거래정지/관리종목/리츠 제외 같은 "유니버스 공통 규칙"을 주고 싶을 때
- benchmark도 함께 더 좁은 우주에서 계산하고 싶을 때

### 1.2 `Filter`

목적:
- 이미 로드된 `Univ` 위에서, 날짜별 종목 마스크를 추가로 씌우는 실행 필터

기본 형태:

```python
from metricstudio import Filter

flt = Filter(
    market_cap=[5, 6, 7, 8, 9, 10],
    liquidity=[3, 4, 5, 6, 7, 8, 9, 10],
    order=["market_cap", "liquidity"],
)
```

주요 입력:
- `market_cap`: 날짜별 시가총액 데실(1~10) 선택
- `liquidity`: 날짜별 `amount / marketcap` 데실 선택
- `order`: `market_cap`, `liquidity`를 어떤 순서로 순차 적용할지 지정

의미:
- `Filter`는 `Backtest` 생성자에 넣지 않고 `bt.analyze(..., filter=flt)`에 넣는다.
- `Filter`는 현재 `Backtest`의 날짜축, 종목축, 가격행렬, `DataLoader`, `Univ`에 bind된 뒤 실행된다.
- 한 번 `analyze(..., filter=flt)` 하면, 그 analyzed pattern은 이후 `run()`과 `screen()`에서도 같은 필터를 재사용한다.

중요:
- 현재 `Filter(market_cap=[...])`는 절대 시총 금액 기준이 아니라 "날짜별 데실" 기준이다.
- `Filter`는 benchmark를 바꾸는 수단이 아니다.
- benchmark까지 더 좁은 우주로 계산하고 싶다면 `Filter`가 아니라 `Univ`를 더 좁혀야 한다.

### 1.3 `Backtest`

목적:
- `metricstudio`의 메인 실행 엔진
- 패턴 분석(`analyze`), 스크리닝(`screen`), 시뮬레이션(`run`)의 공통 컨텍스트를 담는다.

기본 형태:

```python
from metricstudio import Backtest, Univ, patterns as p

bt = Backtest(
    start="2000-01-01",
    end="2026-02-28",
    benchmark=p.AllStockPattern("benchmark"),
    regime=None,
    univ=Univ(market=["KOSPI", "KOSDAQ"]),
    by="day",
)
```

주요 입력:
- `start`, `end`: 백테스트 기간
- `benchmark`: 기준 패턴. 예: `AllStockPattern("benchmark")`
- `regime`: 기본 레짐. 패턴에 별도 레짐이 없으면 자동 적용될 수 있다.
- `univ`: 유니버스 설정
- `by`: 집계 방식. 현재 핵심 모드는 `day`와 `event`

의미:
- `Backtest`는 생성 시점에 `univ`에 맞는 가격 테이블을 로드한다.
- benchmark가 있으면 기준 통계를 미리 계산해 둔다.
- 이후 `analyze()`는 같은 가격축과 시장 데이터 축 위에서 패턴을 평가한다.

## 2. 핵심 메서드

### 2.1 `analyze(*patterns, include_base=True, filter=None)`

목적:
- 패턴들의 성과 통계를 계산해 `StatsCollection`을 만든다.

기본 형태:

```python
stats = bt.analyze(strat, filter=flt)
```

입력:
- `*patterns`: `BasePattern` 객체들
- `include_base`: benchmark를 결과에 같이 포함할지 여부
- `filter`: 실행 필터. `Filter(...)` 객체 또는 `None`

출력:
- `StatsCollection`

동작:
- 패턴 이름은 `named(...)` 또는 생성자 `name`이 우선한다.
- 이름이 충돌하면 `_2`, `_3` 같은 suffix를 붙인다.
- `filter`는 이 `analyze()` 호출에서 계산한 패턴들에만 연결된다.
- `include_base=True`라도 benchmark는 기존 `Univ` 기준 통계를 그대로 사용한다.

실무 해석:
- "유니버스는 그대로 두고, 특정 데실 구간만 전략 실행 대상으로 삼고 싶다"면 `filter=...`
- "benchmark부터 전체 비교 기준을 바꾸고 싶다"면 `univ=...`

### 2.2 `run(...)`

목적:
- `analyze()`로 만든 패턴 통계를 바탕으로 실제 포트폴리오 시뮬레이션을 실행한다.

기본 형태:

```python
sim = bt.run(
    pattern="trend_entry",
    target_horizon="1M",
    trade_price_mode="익일VWAP",
)
```

핵심 입력:
- `pattern`: `analyze()` 결과에 들어 있는 패턴 이름
- `target_horizon`: 목표 보유기간. 예: `"1W"`, `"1M"`, `20`
- `trade_price_mode`: `"당일종가"`, `"익일종가"`, `"익일VWAP"`
- `stop_loss_pct`, `take_profit_pct`: 기본 손절/익절
- `allow_reentry`, `min_cohort_size`: 포지션 운용 옵션

출력:
- `Simulator`

전제:
- 반드시 `analyze()`가 먼저 실행되어야 한다.
- `run()`은 analyze된 패턴 이름을 기준으로 실행된다.
- analyze 때 연결된 `filter`, pattern-level `trade(...)`, `rank_by(...)`, `nmax(...)`도 함께 반영된다.

참고:
- `rank_by(...)`는 후보 수를 직접 줄이지 않는다.
- 실제 종목 수 제한은 `nmax(...)`가 담당하고, `rank_by(...)`는 `nmax` 초과 후보가 나온 날짜에 어떤 종목을 남길지 정한다.
- 현재 `rank_by(...)`는 일자별 후보군 안에서 각 metric을 `0~1` 점수로 정규화한 뒤 합산하는 `rank_sum` 방식만 지원한다.
- 종목 공통 metric은 `("stock", "marketcap.desc")`처럼 줄 수 있다.

### 2.3 `screen(date, pattern, use_cache=True)`

목적:
- 특정 거래일에 패턴을 만족하는 종목 목록을 조회한다.

기본 형태:

```python
picked = bt.screen("2026-03-12", strat)
```

입력:
- `date`: 조회 날짜
- `pattern`: `BasePattern` 객체
- `use_cache`: 가능하면 analyze된 결과와 캐시를 재사용할지 여부

출력:
- 종목코드 index와 `name`, `close` 컬럼을 가진 `DataFrame`

동작:
- 같은 `pattern` 객체가 이미 analyze에 쓰였다면, 그때의 `filter`와 캐시를 재사용할 수 있다.
- analyze되지 않은 새 패턴 객체를 넣어도 동작하지만, 그 경우는 on-the-fly 평가에 가깝다.

## 3. `Univ`와 `Filter`를 어떻게 구분할까

| 항목 | `Univ` | `Filter` |
| --- | --- | --- |
| 적용 시점 | `Backtest(...)` 생성 시 | `analyze(..., filter=...)` 실행 시 |
| 기준 | 정적 유니버스 규칙 | 날짜별 실행 마스크 |
| benchmark 영향 | 있음 | 없음 |
| `run()`/`screen()` 반영 | 같은 `Backtest` 전체에 공통 | analyze된 패턴에 묶여 재사용 |
| 대표 예시 | 시장, 거래가능여부, REIT 제외 | 시총 데실, 유동성 데실 |

실무 규칙:
- benchmark까지 같이 바꾸고 싶으면 `Univ`
- 같은 universe 안에서 전략 실행 대상을 더 걸러내고 싶으면 `Filter`
- `Filter`를 `Backtest()` 초기화에 넣지 않는 이유는, `Univ`와 같은 "전역 유니버스 제약"으로 오해되기 쉽기 때문이다.

자주 헷갈리는 점:
- `Filter`는 `bt.run(...)` 인자가 아니다.
- `Filter`는 `analyze()`에 붙고, 그 결과가 `run()`과 `screen()`에 이어진다.
- 따라서 "실행 필터"라는 표현이 가장 정확하다.

## 4. 추천 사용 흐름

```python
from metricstudio import Backtest, Filter, Univ
from metricstudio import patterns as p

univ = Univ(market=["KOSPI", "KOSDAQ"])
flt = Filter(market_cap=[5, 6, 7, 8, 9, 10])

bt = Backtest(
    start="2000-01-01",
    end="2026-02-28",
    benchmark=p.AllStockPattern("benchmark"),
    by="day",
    univ=univ,
)

bb = p.Bollinger("bb").on(trigger="breakout_up", bandwidth_max=0.05)
high52w = p.High("52w").on(window=240, threshold=0.90, stay_days=1)
uptrend = p.Trending("ma200").on(trigger="ma_trend_up", window=200)
mfi50 = p.MFI("mfi50").on(trigger="above", threshold=50)
amt15 = p.AmountSurge("amt15").on(window=20, threshold=1.5)

strat = (
    bb + high52w + uptrend + mfi50 + amt15
).named("trend_entry").rank_by(
    ("stock", "marketcap.desc"),
    (amt15, "ratio.desc"),
    (bb, "bandwidth.asc"),
    (high52w, "proximity.desc"),
    (uptrend, "ma_slope.desc"),
    (mfi50, "value.desc"),
).nmax(5)

stats = bt.analyze(strat, filter=flt)
sim = bt.run(pattern="trend_entry", target_horizon="1M")
picked = bt.screen("2026-03-12", strat)
```

읽는 순서:
1. `Univ`로 전체 유니버스를 정한다.
2. `Backtest`를 만든다.
3. 패턴을 정의하고 필요하면 `.named(...)`, `.rank_by(...)`, `.nmax(...)`, `.trade(...)`를 붙인다.
4. 실행 대상을 더 좁히고 싶으면 `Filter`를 만들어 `analyze(filter=...)`에 넣는다.
5. 결과를 `run()` 또는 `screen()`으로 이어간다.

## 5. 패턴 파트

패턴 자체의 공통 API와 개별 패턴 설명은 별도 문서에서 다룬다.

- [`패턴 가이드`](%ED%8C%A8%ED%84%B4%20%EA%B0%80%EC%9D%B4%EB%93%9C.md)
- `BasePattern` 공통 API
- 패턴 생성자와 `on(...)` 입력
- `when()`, `market()`, `trim()`, `trade()`, `rank_by()`, `nmax()`
- 개별 패턴(`Bollinger`, `MFI`, `High`, `RetestBreakout`, `RelativeStrength` 등)

`stay_days`, `cooldown_days`의 정확한 동작은 [`stay_cooldown_mask 매뉴얼`](stay%EC%99%80%20cooldown.md)을 함께 보면 된다.
