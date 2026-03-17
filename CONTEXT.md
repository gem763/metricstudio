# CONTEXT (2026-03-16)

## 1) 이 문서의 목적
이 문서는 2026-03-16까지 이 저장소에서 진행한 레짐 재설계, 패턴 보강, 검증 결과를 집에서 이어서 작업할 Codex/에이전트가 최대한 빠르게 이해하도록 정리한 작업 인계 문서다.

핵심 목표는 학문적으로 시장을 잘 분류하는 것이 아니라,
`src/pattern.py`의 패턴들을 언제 켜야 실제로 의미 있는 성과가 나는지를 기준으로
레짐과 패턴 조합을 실전적으로 재설계하는 것이다.

---

## 2) 환경 / 실행 규칙

### 가상환경
이 저장소의 Python 실행은 반드시 `metricstudio` 가상환경에서 진행한다.

```bash
source ~/miniconda3/etc/profile.d/conda.sh && conda activate metricstudio
```

### 작업 디렉토리
```bash
cd /mnt/c/Users/USER/Documents/GitHub/metricstudio
```

### 주의
- `pytest`는 현재 환경에 설치되어 있지 않았다.
- 테스트는 `python -m unittest ...` 기준으로 남겼다.
- `src/pattern.py`는 CRLF 줄바꿈을 사용 중이라 diff가 실제보다 크게 보일 수 있다.
- 저장소 전체가 매우 dirty한 상태다. 내가 손댄 파일 외에도 사용자가 이미 수정한 파일이 많다.
- 다른 에이전트는 절대 넓게 정리/포맷팅/줄바꿈 통일을 하지 말고, 필요한 파일만 최소 범위로 수정해야 한다.

---

## 3) 현재 브랜치/워킹트리 상태에서 중요한 파일

이번 턴들에서 실질적으로 건드린 파일:

- `src/stats.py`
- `src/regime.py`
- `src/backtest.py`
- `src/pattern.py`
- `레짐분류_실용재설계안.md`
- `scripts/validate_trend_filters.py`
- `tests/test_pattern_filters.py`
- `tests/__init__.py`
- `CONTEXT.md`

중요: 저장소에는 이 외에도 사용자가 이미 수정해 둔 파일이 많이 있다.
다음 에이전트는 `git status`를 먼저 보고, 내가 실제로 만든 변경과 사용자의 기존 변경을 섞어 다루지 않도록 조심해야 한다.

---

## 4) 문제의식: 왜 레짐을 다시 설계했는가

사용자는 원래 5단계 레짐 설계를 쓰고 있었다.
개념적으로는 대략 아래와 같았다.

- `quiet_squeeze_expansion`
- `broad_bull_breakout`
- `narrow_leadership`
- `panic_rebound_risk`
- `sideways_choppy`

사용자의 주력 패턴은 아래 조합이었다.

- `Bollinger(trigger='breakout_up', bandwidth_max=0.05)`
- `High(window=240, threshold=0.90)`
- `Trending(trigger='ma_trend_up', window=200)`
- `MFI(trigger='above', threshold=50)`

즉 종목 단에서 이미
- squeeze
- breakout
- 52주 고가 갱신
- 장기 추세 정렬
- 수급 확인
을 강하게 요구하는 패턴이었다.

여기서 사용자 질문은 다음이었다.

- 이 패턴은 직관적으로 `quiet_squeeze_expansion`에 더 잘 맞을 것 같았는데,
  실제 결과는 `broad_bull_breakout`가 더 좋다.
- 그렇다면 레짐 정의가 잘못된 것인가?
- 아니면 추세 패턴에 대해 레짐 해상도가 너무 과한 것인가?

결론적으로, 현재 목적이 "시장을 예쁘게 설명하는 taxonomy"가 아니라
"패턴 스위치를 통해 실전 성과를 높이는 것"이라면,
기존 5단계는 과할 가능성이 높다고 판단했다.

---

## 5) 핵심 분석 결론

### 5.1 `QUIET` vs `BROAD` 재해석
처음엔 `quiet_squeeze_expansion`이 squeeze형 패턴에 더 좋을 것 같았지만,
실제 차트와 숫자를 다시 검토한 결과,
현재 주력 패턴에 대해서는 `broad_bull_breakout`가 전반적으로 더 잘 맞는 쪽이었다.

중요한 수정 사항:
- 처음에는 조건부 benchmark를 손으로 재구성하다가 해석이 과장된 부분이 있었다.
- 이후 사용자가 실제로 노트북에서 돌린 방식과 동일하게,
  `bm_regimed = Pattern('모든주식').when(regime)`를 같은 `bt.analyze(...)`에 넣는 비교를 기준으로 다시 해석했다.
- 그 기준에서는 사용자의 관찰이 맞았다. 현재 패턴은 `QUIET`보다 `BROAD`에서 전반적으로 더 강했다.

### 5.2 왜 그런가
핵심 이유는 현재 패턴 자체가 너무 "확인된 추세"에 가깝기 때문이다.

- `bb(0.05)` = 강한 squeeze breakout
- `52주 고가` = 이미 절대 강세
- `MA200 상향` = 장기 추세 확인
- `MFI > 50` = 수급 확인

이런 패턴은 "초기 조용한 준비장"보다
"이미 시장 전체 추진력이 붙은 확산장"과 더 잘 맞는다.

즉,
- `QUIET`와 `BROAD`는 둘 다 trend-friendly 축에 있고
- 사용자의 주력 패턴도 trend-following 축에 있으므로
- 레짐과 패턴이 같은 축을 두 번 자르는 구조가 되어 정보 이득이 작아졌다고 봤다.

### 5.3 실전적 결론
상위 레짐은 아래처럼 단순화하는 것이 낫다고 판단했다.

- `trend_friendly`
- `contrarian_friendly`
- `panic_rebound_risk`
- `neutral`

그리고 기존의 미세한 설명용 레짐은 보조 태그로 내리는 구조가 더 실용적이라고 판단했다.

- `quiet_tag`
- `narrow_tag`

---

## 6) 새 레짐 설계와 구현 상태

### 6.1 새 상위 레짐
`src/regime.py`에 아래 상수를 추가했다.

- `REGIME_TREND = 'trend_friendly'`
- `REGIME_CONTRARIAN = 'contrarian_friendly'`
- `REGIME_PANIC = 'panic_rebound_risk'`
- `REGIME_NEUTRAL = 'neutral'`
- `REGIME_QUIET_TAG = 'quiet_tag'`
- `REGIME_NARROW_TAG = 'narrow_tag'`

관련 위치:
- `src/regime.py:11`

### 6.2 호환성 유지
기존 kind도 계속 쓸 수 있도록 alias를 유지했다.

예:
- `quiet_squeeze_expansion`
- `broad_bull_breakout`
- `narrow_leadership`
- `sideways_choppy`

관련 위치:
- `src/regime.py:27`

### 6.3 label / legacy_label
`build_regime_frame()` 결과는 이제 아래 두 축을 같이 가진다.

- `label`: 새 4분류 상위 레짐
- `legacy_label`: 예전 5분류 레짐

또한 bool 컬럼도 같이 가진다.

- `trend_friendly`
- `contrarian_friendly`
- `panic_rebound_risk`
- `neutral`
- `quiet_tag`
- `narrow_tag`
- legacy mask들

관련 위치:
- `src/regime.py:316`
- `src/regime.py:340`

### 6.4 Backtest 연결 방식 변경
기존에는 `frame['label'] == kind` 방식으로 레짐을 찾았는데,
새 구조에서는 kind와 동일한 bool 컬럼이 있으면 그 컬럼을 직접 쓰도록 바꿨다.

추가된 helper:
- `regime_mask_from_frame(frame, kind)`

관련 위치:
- `src/regime.py:64`
- `src/backtest.py:22`
- `src/backtest.py:1331`

### 6.5 기본값 변경
`Regime().on()`의 기본 kind는 이제 `trend_friendly`다.

관련 위치:
- `src/regime.py:354`

### 6.6 현재 의미
이제 다음 둘을 모두 쓸 수 있다.

신규 방식:
```python
Regime().on(kind='trend_friendly', market='kospi')
Regime().on(kind='contrarian_friendly', market='kospi')
Regime().on(kind='quiet_tag', market='kospi')
```

구방식 호환:
```python
Regime().on(kind='broad_bull_breakout', market='kospi')
Regime().on(kind='quiet_squeeze_expansion', market='kospi')
```

### 6.7 실사용 검증
`Pattern(name='모든주식').when(Regime(...))` 경로로 실제 `Backtest.analyze()`에 붙여서
아래 kind들이 모두 정상 작동하는 것까지 확인했다.

- `trend_friendly`
- `contrarian_friendly`
- `quiet_tag`
- `narrow_tag`
- `broad_bull_breakout`
- `quiet_squeeze_expansion`

---

## 7) `stats.plot()` 관련 변경
이전 대화에서 `stats.plot()` 차트 개선 작업도 진행했다.
집에서 이어갈 에이전트는 이 변경이 이미 들어가 있다고 가정해야 한다.

### 7.1 추가된 4번째 차트
`stats.plot()`은 원래 3개 패널이었는데,
맨 오른쪽에 `Pattern Frequency (%)` 차트를 추가했다.

정의:
- 분자: 각 pattern의 horizon별 count
- 분모: benchmark(모든주식)의 horizon별 count
- benchmark 라인은 이 차트에서 표시하지 않음

관련 위치:
- `src/stats.py` 내부 `plot()` 로직
- 대표 검색 키워드: `Pattern Frequency (%)`
- 현재 검색 위치: `src/stats.py:1068`

### 7.2 y축 형식
사용자 요청에 맞춰 마지막 차트의 y축은 다음처럼 고정했다.

- 범위: `0 ~ 100`
- 정수 tick만 표시
- 소수점 제거

### 7.3 참고
현재 `stats.plot(annualized=True)`는 구현상 `after cost`도 함께 반영한다.
이 점을 차트 해석에서 항상 주의해야 한다.

---

## 8) `src/pattern.py`에 추가한 새 패턴

### 8.1 `RelativeStrength`
위치:
- `src/pattern.py:510`

의미:
- 최근 `window`일 종목 수익률 - 같은 기간 시장 수익률
- 이 값이 `threshold` 이상일 때만 통과

지원 방식 2개:
```python
RelativeStrength(name='상대강도').on(market='kospi', window=60, threshold=0.0)
RelativeStrength(name='상대강도').market('kospi').on(window=60, threshold=0.0)
```

### 8.2 `AmountSurge`
위치:
- `src/pattern.py:590`

의미:
- 당일 거래대금 / 최근 `window`일 평균 거래대금
- 이 비율이 `threshold` 이상일 때만 통과

예:
```python
AmountSurge(name='거래대금급증').on(window=20, threshold=1.5)
```

### 8.3 `RetestBreakout`
위치:
- `src/pattern.py`

의미:
- 최근 `breakout_window` 고점 돌파가 먼저 나온 뒤
- 그 돌파 레벨 근처(`retest_tolerance`)로 눌림이 들어오고
- 첫 양봉성 반등(`rebound_confirm='close_up'`)이 나올 때만 통과

예:
```python
RetestBreakout(name='돌파후눌림').on(
    breakout_window=20,
    retest_tolerance=0.03,
    rebound_confirm='close_up',
)
```

주의:
- 현재는 close only 기준의 1차 구현이다.
- intraday 저점/거래대금 재확인까지는 아직 넣지 않았다.

### 8.4 `PanicRebound`
위치:
- `src/pattern.py`

의미:
- 최근 `drawdown_window`일 기준 고점 대비 큰 낙폭(`drawdown_min`)이 먼저 나온 뒤
- `rebound_days` 연속 반등이 나올 때만 통과
- 선택적으로 신호일 거래량 급증(`volume_spike`)도 요구 가능

예:
```python
PanicRebound(name='패닉반등').on(
    drawdown_window=20,
    drawdown_min=-0.18,
    rebound_days=3,
    volume_spike=True,
)
```

주의:
- 현재는 close/volume 기반의 1차 구현이다.
- intraday 저점 반전이나 장중 reversal 캔들까지는 아직 반영하지 않았다.

### 8.5 export
넷 다 `__all__`에 반영했다.
- `src/pattern.py:1106`

---

## 9) 새 패턴 검증 결과

### 9.1 `RelativeStrength` 단위 검증
synthetic array에서 다음 둘을 모두 확인했다.

- `.on(market='kospi', ...)`
- `.market('kospi').on(...)`

관련 테스트 파일:
- `tests/test_pattern_filters.py`

### 9.2 `RelativeStrength` 실전 비교 결과
비교 대상:
- 기본패턴 = `볼린저돌파 + 52주고가 + MA200상향 + MFI>50`, 그리고 `trend_friendly`
- 비교 variant:
  - `RS60 >= 0`
  - `RS60 >= 5%p`
  - `RS120 >= 0`

결론:
- 표본 수는 대략 `75% ~ 87%` 수준으로 줄었음
- 그러나 현재 주력 breakout 패턴에서는 기본형을 일관되게 이기지 못했음
- 평균 `geom annualized gap after cost` 기준으로도 기본패턴이 더 좋았음

요약 점수(내부 비교용):
- 기본패턴: `0.2107`
- `RS60>=5%p`: `0.2014`
- `RS120>=0`: `0.1954`
- `RS60>=0`: `0.1932`

즉,
`RelativeStrength`는 아이디어 자체는 맞지만,
지금 주력 breakout 조합에서는 1순위 품질 필터는 아니라고 결론 내렸다.

### 9.3 `AmountSurge` 실전 비교 결과
비교 대상:
- 기본패턴
- `기본패턴 + AmountSurge(20, 1.5x)`
- `기본패턴 + AmountSurge(20, 2.0x)`

전체기간 결과(`trend_friendly` 안, benchmark 대비 `geom annualized gap after cost`):

기본패턴:
- `1M`: `0.3208`
- `2M`: `0.2415`
- `3M`: `0.1658`
- `6M`: `0.1148`

`AmountSurge(20, 1.5x)`:
- `1M`: `0.3811`
- `2M`: `0.2931`
- `3M`: `0.1947`
- `6M`: `0.1208`

`AmountSurge(20, 2.0x)`:
- `1M`: `0.3177`
- `2M`: `0.2343`
- `3M`: `0.1637`
- `6M`: `0.1110`

해석:
- `1.5x`는 전 구간에서 기본패턴보다 우세했다.
- `2.0x`는 너무 강해서 개선 폭이 거의 사라졌다.
- 즉 현재 실전 기본 옵션은 `AmountSurge(20, 1.5x)`가 가장 적절하다.

### 9.4 `AmountSurge + RelativeStrength` 추가 결합 결과
비교:
- `기본 + Amount1.5x`
- `기본 + Amount1.5x + RS60>=5%p`

결론:
- RS를 그 위에 한 겹 더 얹으면 오히려 약해졌다.
- 표본 수만 크게 줄었다.

대표 결과:
- `Amount1.5x` 1M gap: `0.3811`
- `Amount1.5x + RS60>=5%p` 1M gap: `0.3478`

즉 현재 우선순위는 명확하다.

1. `AmountSurge(20, 1.5x)`
2. `RelativeStrength`

---

## 10) Walk-Forward 점검 결과
이 검증은 과최적화 우려를 줄이기 위해 진행했다.

비교 대상:
- `기본패턴`
- `기본패턴 + AmountSurge(20, 1.5x)`

비교 지표:
- `after cost geometric annualized gap vs trend regime benchmark`

구간은 다음과 같이 나눴다.
- `2000-2006`
- `2007-2012`
- `2013-2018`
- `2019-2025`

### 10.1 결론 요약
- `1M ~ 3M`에서는 4개 구간 모두 `Amount1.5x`가 기본패턴보다 우세했다.
- `6M`은 4개 구간 중 3개에서 우세했고,
  `2007-2012`만 기본패턴이 아주 근소하게 더 좋았다.
- 즉 `Amount1.5x`는 특정 한 시기에만 먹힌 필터가 아니라,
  현재 추세 breakout 패턴에서 꽤 일관된 개선 필터로 볼 수 있다.

### 10.2 구간별 해석
#### 2000-2006
`Amount1.5x`가 전 구간 우세.
특히 `1M`, `2M` 개선 폭이 큼.

#### 2007-2012
`1M ~ 3M` 우세.
`6M`만 아주 근소 열세.

#### 2013-2018
전 구간 우세지만 개선 폭은 크지 않음.
즉 유효하긴 하지만 압도적이지는 않음.

#### 2019-2025
전 구간 우세.
특히 `1M ~ 3M`에서 다시 분명한 개선이 나타남.

### 10.3 실전 해석
현재 추세 전략의 보유기간이 `1M ~ 3M` 중심이라면,
`AmountSurge(20, 1.5x)`를 기본 품질 필터로 채택하는 것이 합리적이다.

`6M`까지 오래 끄는 전략에서도 대체로 나쁘지 않지만,
그 horizon에서는 개선 폭이 항상 크다고 보긴 어려우므로
보유기간 전략과 함께 다시 보는 것이 좋다.

### 10.4 재현 스크립트
반복 검증용 스크립트를 추가했다.

- `scripts/validate_trend_filters.py`

실행:
```bash
source ~/miniconda3/etc/profile.d/conda.sh && conda activate metricstudio
python scripts/validate_trend_filters.py
```

이 스크립트는 아래를 출력한다.
- `overall`
- `2000-2006`
- `2007-2012`
- `2013-2018`
- `2019-2025`

각 구간별로
- `count`
- `count_ratio`
- `rise_prob`
- `geom_ann_after_cost`
- `geom_ann_gap_after_cost`
를 출력한다.

### 10.5 `RetestBreakout` 1차 구현 및 비교 결과
이번 턴에서 `RetestBreakout` 1차 버전을 구현하고,
별도 비교 스크립트로 아래 3가지를 같이 비교했다.

- `base`
- `base + AmountSurge(20, 1.5x)`
- `retest = RetestBreakout + 52주고가 + MA200상향 + MFI>50`

재현 스크립트:
- `scripts/validate_retest_breakout.py`

전체기간 결과(`trend_friendly`, benchmark 대비 `geom annualized gap after cost`):

- `base`: `1M 0.3208`, `2M 0.2415`, `3M 0.1658`, `6M 0.1148`
- `base+amount1.5x`: `1M 0.3811`, `2M 0.2931`, `3M 0.1947`, `6M 0.1208`
- `retest`: `1M 0.1161`, `2M 0.1238`, `3M 0.0905`, `6M 0.0577`

핵심 해석:
- `RetestBreakout` 1차 버전도 benchmark보다는 양(+)의 gap을 냈다.
- 하지만 현재 구현은 `count_ratio`가 거의 `1.0`이라 표본 압축력이 너무 약했다.
- 결과적으로 현재 breakout 기준선이나 `AmountSurge(20, 1.5x)` 기준선보다 명확히 약했다.

구간별 관찰:
- `2000-2006`, `2007-2012`, `2013-2018`, `2019-2025` 전 구간에서
  `retest`는 대체로 benchmark보다는 낫지만,
  `base+amount1.5x`를 이기지는 못했다.

실전 해석:
- 아이디어 자체가 틀렸다고 보기보다,
  현재 close only 정의가 너무 느슨해서 사실상 benchmark에 가까운 밀도로 신호를 낸다고 보는 편이 맞다.
- 다음 단계는 `RetestBreakout`을 폐기하는 것보다,
  시간 제한/재돌파 확인/추가 품질 확인을 넣어 더 타이트하게 재정의하는 쪽이 자연스럽다.

### 10.6 `RetestBreakout` 2차 정교화 결과
이후 바로 2차 정교화를 한 번 더 진행했다.

추가한 내용:
- `max_retest_days`
- retest를 실제 breakout level 이하 구간으로 제한
- rebound day는 breakout level을 다시 회복해야만 진입
- 선택 옵션으로 `breakout_amount_threshold`를 추가해
  거래대금이 붙은 breakout만 setup으로 인정 가능하게 함

추가 비교:
- `retest`
- `retest + breakout_amount1.5x`

전체기간 결과(`trend_friendly`, benchmark 대비 `geom annualized gap after cost`):

- `retest`: `1M 0.1624`, `2M 0.1326`, `3M 0.0962`, `6M 0.0546`
- `retest+breakout_amount1.5x`: `1M 0.1321`, `2M 0.1349`, `3M 0.1135`, `6M 0.0606`

해석:
- 거래대금 필터를 얹으면 `count_ratio`는 `0.979 -> 0.936` 수준으로 내려왔다.
- `2M ~ 6M`은 약간 나아졌지만, `1M`은 오히려 약해졌다.
- 무엇보다도 두 variant 모두 여전히 `base`와 `base+AmountSurge(20, 1.5x)`를 이기지 못했다.

실전 결론:
- `RetestBreakout`은 아이디어 검증 수준으로는 의미가 있지만,
  지금 상태에서 주력 후보로 승격할 정도의 우위는 아직 없다.
- 즉 이 패턴은 잠정 보류하고,
  trend family의 주력은 여전히 `base + AmountSurge(20, 1.5x)`로 보는 것이 맞다.
- 이후 다시 손댄다면, 단순 파라미터 조정보다
  breakout day 품질이나 intraday retest 정보 같은 더 구조적인 조건이 필요해 보인다.

### 10.7 `PanicRebound` 1차 구현 및 비교 결과
이번 턴에서 `PanicRebound` 1차 버전을 구현하고,
`panic_rebound_risk` 레짐 안에서 아래 패턴들을 같이 비교했다.

- `Disparity(20, threshold=0.9)`
- `MFI(trigger='oversold_rebound')`
- `PanicRebound`
- `PanicRebound(volume_spike=True, volume_threshold=1.5)`

재현 스크립트:
- `scripts/validate_panic_rebound.py`

여기서 용어:
- `benchmark`: 같은 `panic_rebound_risk` 레짐 안의 전체 종목
- `count_ratio`: 패턴 count / benchmark count
- `geom_ann_gap_after_cost`: 비용 반영 후 연율화 기하수익률의 benchmark 대비 초과성과

전체기간 결과:

`Disparity(20, 0.9)`:
- `1M -0.0095`
- `2M -0.0209`
- `3M -0.0487`
- `6M -0.0581`

`MFI oversold_rebound`:
- `1M 0.0319`
- `2M 0.0258`
- `3M 0.0120`
- `6M 0.0041`

`PanicRebound`:
- `1M -0.2607`
- `2M -0.1732`
- `3M -0.1599`
- `6M -0.1470`

`PanicRebound + volume1.5x`:
- `1M -0.4705`
- `2M -0.3722`
- `3M -0.3358`
- `6M -0.2874`

해석:
- `PanicRebound` 1차 버전은 plain/volume 둘 다 benchmark를 크게 하회했다.
- volume 필터를 붙이면 `count_ratio`는 `0.982 -> 0.909`로 줄었지만 성과는 더 나빠졌다.
- 즉 현재 정의의 `PanicRebound`는 실전 후보로 보기 어렵다.

추가 빠른 점검:
- 같은 panic 레짐에서 `Bollinger(trigger='near_down') + MFI oversold_rebound`도 확인했지만
  전체기간 `1M -0.0354 / 2M -0.0865 / 3M -0.0480 / 6M -0.0230`로 약했다.

실전 결론:
- panic 레짐에서는 새 custom 패턴보다 기존 `MFI oversold_rebound`가 least-bad baseline에 가깝다.
- 현재 단계에서는 panic 구간을 기본적으로 꺼 두고,
  예외전략이 꼭 필요하면 `MFI oversold_rebound` 정도만 아주 보수적으로 보는 쪽이 맞다.

---

## 11) 테스트 파일
현재 표준 라이브러리 기반 테스트를 추가했다.

파일:
- `tests/test_pattern_filters.py`
- `tests/__init__.py`

실행:
```bash
source ~/miniconda3/etc/profile.d/conda.sh && conda activate metricstudio
python -m unittest tests.test_pattern_filters -v
```

현재 포함 테스트:
- `RelativeStrength`가 `.on(market=...)` 방식으로 동작하는지
- `RelativeStrength`가 `.market(...).on(...)` 방식으로도 동작하는지
- `AmountSurge`가 rolling mean 대비 스파이크를 제대로 감지하는지
- `RetestBreakout`이 돌파 후 첫 반등만 잡는지
- `RetestBreakout`이 허용폭 아래로 깨진 실패 retest는 무시하는지
- `RetestBreakout`이 `max_retest_days`를 넘기면 setup을 폐기하는지
- `RetestBreakout`이 breakout day 거래대금 급증을 선택적으로 요구할 수 있는지
- `PanicRebound`가 급락 후 반등 구간을 잡는지
- `PanicRebound`가 최근 panic drawdown이 없으면 신호를 내지 않는지
- `PanicRebound`가 volume spike를 선택적으로 요구할 수 있는지

참고:
- `pytest`는 현재 환경에 없어서 실패했다 (`pytest: command not found`).
- 다음 에이전트도 `pytest`를 가정하지 말 것.

---

## 12) 문서화 상태
재설계안 문서:
- `레짐분류_실용재설계안.md`

이 문서에는 이미 아래가 반영되어 있다.
- 왜 5레짐이 과한지
- 4개 상위 레짐 + 2개 태그 구조
- `RelativeStrength`, `AmountSurge`, `RetestBreakout`, `PanicRebound` 아이디어
- 1차 검증 메모
- walk-forward 점검 메모

현재 문서 안의 우선순위 문장 중 일부는 초안 시점과 나중 검증 메모가 같이 존재한다.
즉 초기에 적어둔 "추가 패턴 우선순위"와,
나중에 검증 결과를 반영해 적은 "실제 우선순위 수정"이 함께 있다.
집에서 이어갈 에이전트는 문서의 후반부 `10.5`, `10.6`을 더 높은 신뢰도로 읽어야 한다.

---

## 13) 현재 가장 중요한 사용 예시
지금 기준의 추천 기본 추세 패턴:

```python
from src.backtest import Backtest, Univ
from src.pattern import Bollinger, High, Trending, MFI, AmountSurge
from src.regime import Regime

bt = Backtest(
    start='2000-01-01',
    end='2025-12-31',
    by='day',
    univ=Univ(market=['KOSPI', 'KOSDAQ']),
    db=0,
)

reg_trend = Regime().on(kind='trend_friendly', market='kospi')

bb = Bollinger(name='볼린저돌파').on(
    trigger='breakout_up',
    breakout_cooldown_days=3,
    bandwidth_max=0.05,
)
high52w = High(name='52주 고가').on(window=240, threshold=0.90, stay_days=1)
uptrend = Trending(name='이평상향').on(trigger='ma_trend_up', window=200)
mfi_high = MFI(name='MFI상승').on(trigger='above', threshold=50)
amt = AmountSurge(name='거래대금급증').on(window=20, threshold=1.5)

pat = (bb + high52w + uptrend + mfi_high + amt).when(reg_trend)
stats = bt.analyze(pat)
stats.plot(annualized=True)
```

---

## 14) 검증 커맨드 모음
### 패턴 테스트
```bash
source ~/miniconda3/etc/profile.d/conda.sh && conda activate metricstudio
python -m unittest tests.test_pattern_filters -v
```

### trend filter 검증 스크립트
```bash
source ~/miniconda3/etc/profile.d/conda.sh && conda activate metricstudio
python scripts/validate_trend_filters.py
```

### retest 1차 검증 스크립트
```bash
source ~/miniconda3/etc/profile.d/conda.sh && conda activate metricstudio
python scripts/validate_retest_breakout.py
```

### panic rebound 검증 스크립트
```bash
source ~/miniconda3/etc/profile.d/conda.sh && conda activate metricstudio
python scripts/validate_panic_rebound.py
```

### 문법 검사
```bash
source ~/miniconda3/etc/profile.d/conda.sh && conda activate metricstudio
python -m py_compile src/regime.py src/backtest.py src/pattern.py src/stats.py \
  scripts/validate_trend_filters.py scripts/validate_retest_breakout.py \
  scripts/validate_panic_rebound.py \
  tests/test_pattern_filters.py
```

---

## 15) 남은 리스크 / 주의사항

### 15.1 `src/pattern.py` diff가 과장돼 보일 수 있음
이 파일은 CRLF라서 도구에 따라 diff가 비정상적으로 커질 수 있다.
논리적으로는 이번에 `RelativeStrength`, `AmountSurge`, `RetestBreakout`, `__all__` 정도가 핵심 변경이다.
다음 에이전트는 줄바꿈까지 대대적으로 손대지 말 것.

### 15.2 저장소가 이미 dirty함
`.gitignore`, `AGENTS.md`, `LICENSE`, `requirements.txt`, `src/*`, 노트북들 등
사용자 변경이 매우 많다.
다음 에이전트는 내가 건드린 파일만 보고 clean하다고 생각하면 안 된다.
항상 `git status`로 먼저 확인할 것.

### 15.3 WSL 안정성
사용자가 작업 중 WSL 연결이 끊긴 적이 있었다.
정확한 원인은 확정 못 했지만,
로그상으로는 리눅스 내부 OOM killer 증거보다는 WSL 인스턴스 재시작 쪽에 가까웠다.
다만 현재 실험은 메모리 피크가 커질 수 있으므로,
여전히
- 한 번에 패턴을 너무 많이 analyze 하지 말고
- 검증은 작은 세트로 쪼개어 실행하는 것이 좋다.

### 15.4 `stats.plot(annualized=True)` 해석 주의
현재 구현상 `annualized=True`면 `after cost`도 같이 켜진다.
차트 해석 시 raw return과 혼동하지 말 것.

---

## 16) 다음 에이전트에게 가장 중요한 추천 다음 단계
다음 단계로 가장 자연스러운 작업은 panic 예외전략을 더 만들기보다,
`panic_rebound_risk`를 기본적으로 off로 둘지 정책을 정리하는 것이다.

이유:
- `PanicRebound` 1차는 plain/volume 모두 매우 약했다.
- panic 레짐 안의 기존 후보 중에서는 `MFI oversold_rebound`만 겨우 소폭 플러스였다.
- 즉 panic은 “공격적으로 공략할 구간”이라기보다,
  대부분 전략을 끄고 아주 제한적인 예외만 허용할지 결정하는 구간에 가깝다.

즉 다음 Codex는

1. `CONTEXT.md`를 먼저 읽고
2. `scripts/validate_panic_rebound.py` 결과를 먼저 확인하고
3. panic 구간에서 정말 켤 전략을 `없음` 또는 `MFI oversold_rebound`로 둘지 먼저 결정하고
4. 그 다음에야 panic 레짐 경계 조정 또는 예외전략 고도화를 검토

순서로 가는 것이 가장 좋다.

---

## 17) 집에서 이어갈 때 추천 프롬프트
사용자는 집에서 Codex에게 아래처럼 말하면 된다.

```text
CONTEXT.md를 참고로 해서 후속작업을 진행하자.
지금까지의 레짐 재설계/패턴 검증 맥락을 이어서,
다음으로 가장 적합한 단계를 직접 판단해서 진행해.
```

이 문서를 읽은 다음 에이전트는,
별도 설명 없이도 현재 상태를 충분히 이어받을 수 있어야 한다.

---

## 18) `trend_friendly` 1차 완화
이번 턴에서 사용자가 직접 문제제기한 것은
`trend_friendly`가 너무 희소해서(`~14.3% coverage`)
좋은 추세 패턴의 절대 성과를 지나치게 깎고 있다는 점이었다.

### 18.1 확인한 사실
기존 `trend_friendly`는 coverage가 `0.1431` 수준이었다.

하지만 quality separation 자체는 맞았다.

- `base` 1M `geom_mean`: inside `0.0371`, outside `0.0155`
- `best` 1M `geom_mean`: inside `0.0405`, outside `0.0177`

즉 방향이 틀린 레짐이라기보다,
하드 on/off 게이트로 쓰기엔 너무 엄격한 레짐에 가까웠다.

### 18.2 완화안 비교
과최적화를 피하려고,
threshold를 많이 건드리는 대신 설명 가능한 소수 후보만 비교했다.

비교한 핵심 후보:

- `narrow_tag`를 trend에 합치기
- `trend_broad_branch` breadth threshold만 `0.55 -> 0.50`으로 완화
- quiet/broad를 같이 조금 더 완화

결론:
- `narrow_tag`를 합치는 안은 coverage는 늘지만 quality 저하가 더 컸다.
- quiet branch까지 여러 threshold를 같이 만지는 안은 coverage 추가 이득이 작고 임의성이 커졌다.
- 가장 자연스러운 안은 `trend_broad_branch`만 완화하는 것이었다.

### 18.3 최종 반영
`src/regime.py`에서 아래 둘만 조정했다.

- `pct_above60_all >= 0.55 -> 0.50`
- `aar5 >= 0.55 -> 0.50`

관련 위치:
- `src/regime.py`의 `trend_broad_branch`

### 18.4 반영 후 해석
이 완화는 “추세인데 breadth가 아주 강하지는 않은 구간”까지 포용하려는 조정이다.
즉 레짐 의미를 바꾸기보다,
지나치게 강한 breadth 확인을 조금 완화한 것이다.

내부 비교상 coverage는 대략 `14.3% -> 23.8%`로 늘었고,
`inside > outside` 품질 차이도 여전히 유지됐다.

대표값:

- `base` inside/outside 1M `geom_mean`: `0.0326 / 0.0138`
- `best` inside/outside 1M `geom_mean`: `0.0357 / 0.0158`

즉 현재 판단은:

- 기존 `trend_friendly`는 너무 엄격했다.
- 하지만 레짐 방향 자체는 맞았다.
- 그래서 이번 조정은 레짐 폐기가 아니라 “breadth 문턱의 보수적 완화”다.

### 18.5 `30%+ coverage` 추가 조정
이후 사용자가 trend coverage를 최소 `30%` 이상으로 올리고 싶다고 요청했다.

여기서도 과최적화를 피하려고,
무작정 threshold를 더 낮추는 대신
`narrow leadership`을 제한적으로 trend 안에 편입할 수 있는지 비교했다.

비교 결과:

- `narrow_tag`를 그대로 합치면 coverage는 충분히 늘지만 품질 희석이 더 컸다.
- `narrow_tag`에 안전장치(`dd60`, `rv20_pct240`, `aar5`)를 같이 걸면
  coverage `30%+`를 넘기면서도 inside/outside 분리력이 유지됐다.

최종 반영:

- `trend_common_gate`
  - `trend_score >= 2.0`
  - `dd60 > -0.08`
  - `rv20_pct240 < 0.80`
- `trend_narrow_branch`
  - `large_pct_above60 >= 0.60`
  - `small_pct_above60 <= 0.45`
  - `leadership_spread >= 0.15`
  - `aar5 >= 0.45`

즉 “좁은 리더십 장세이지만 시장 스트레스가 심하지 않고,
advancing amount도 완전히 죽지 않은 구간”만
trend로 받아들이는 쪽으로 정리했다.

반영 후 대표값:

- coverage: `0.3144`
- `base` inside/outside 1M `geom_mean`: `0.0287 / 0.0135`
- `best` inside/outside 1M `geom_mean`: `0.0303 / 0.0164`

해석:

- coverage는 목표한 `30%+`를 넘겼다.
- 품질은 이전 `23.8%` 버전보다 조금 희석됐지만,
  여전히 inside가 outside보다 뚜렷하게 낫다.
- 그래서 이 변경은 “추세 정의를 버린 확장”이 아니라,
  `safe narrow leadership`까지 포함한 실전형 trend 정의로 보는 편이 맞다.
- 이후 구현 가독성을 위해 `trend_friendly`는
  `trend_common_gate + quiet/broad/narrow branch` 구조로 정리했고,
  branch 계산식 안에서 `narrow_tag`를 직접 참조하지 않도록 바꿨다.

### 18.6 다음 체크포인트
다음 에이전트는 이 변경 후 바로 아래 두 가지를 다시 확인하는 것이 좋다.

1. 노트북에서 `trend_base`, `trend_amount1.5x` wealth / mean_exposure가 실제로 얼마나 개선되는지
2. `panic_rebound_risk`를 off 정책으로 갈지 여부를 다시 이어서 정리할지

---

## 19) 2026-03-17 회사(WSL) 세션 상세 인계
이번 세션은 회사 WSL 환경에서 진행했고,
다음 세션은 집 맥북에서 이어질 가능성이 높다.

즉 다음 에이전트는
“WSL에서 하던 레짐 재설계 / 패턴 검증 / 노트북 정리 작업을
맥북에서 자연스럽게 이어받는다”는 전제로 이해하면 된다.

이번 턴에서 실제로 정리된 것은 크게 5가지다.

1. `trend_friendly` 레짐 구조 재정리
2. `trend`/`panic` 패턴 실험 결과 정리
3. `stats.plot()` vs `wealth` 해석 기준 정리
4. `Backtest` / `Simulator` UX 개선
5. `레짐-패턴실험 2026.03.17.ipynb` 실행 구조 정리

아래에 하나씩 남긴다.

### 19.1 현재 가장 중요한 결론
현재 기준으로는,
`trend_friendly` 안에서 실전 주력 패턴은 여전히 아래 조합이다.

- `Bollinger breakout`
- `52주 고가`
- `MA200 상향`
- `MFI > 50`
- `AmountSurge(20, 1.5x)`

즉 shorthand로는
`trend_amount1.5x`
가 현재 대표 조합이다.

반면 아래는 아직 주력으로 채택하지 않았다.

- `RelativeStrength`
- `RetestBreakout`
- `PanicRebound`

해석은 단순하다.

- `AmountSurge(20, 1.5x)`는 trend breakout 계열의 quality filter로 유의미했다.
- `RelativeStrength`는 아이디어는 맞지만, 현재 breakout 기준선 위에 올렸을 때 일관된 우위를 보이지 못했다.
- `RetestBreakout`은 second-entry 아이디어로는 의미가 있었지만, 현재 정의로는 기준선을 넘지 못했다.
- `panic_rebound_risk`에서는 새 custom 패턴보다 기본적으로 `off`가 더 자연스럽고, 예외 후보가 필요하면 `MFI oversold_rebound` 정도만 보수적으로 남겨두는 편이 맞다.

### 19.2 `trend_friendly` 최종 코드 구조
현재 `src/regime.py`에서 `trend_friendly`는
`tag를 branch 내부에서 참조하는 구조`가 아니라,
가독성을 위해 아래처럼 정리돼 있다.

```python
trend_common_gate = (
    (trend_score >= 2.0)
    & (dd60 > -0.08)
    & (rv20_pct240 < 0.80)
)

trend_quiet_branch = trend_common_gate & (
    (bbw20_pct240 <= 0.20)
    & (rv20_pct240 <= 0.40)
    & (pct_above20_delta5 >= 0.05)
    & (pct_above20_all >= 0.45)
)

trend_broad_branch = trend_common_gate & (
    (pct_above60_all >= 0.50)
    & (aar5 >= 0.50)
)

trend_narrow_branch = trend_common_gate & (
    (large_pct_above60 >= 0.60)
    & (small_pct_above60 <= 0.45)
    & (leadership_spread >= 0.15)
    & (aar5 >= 0.45)
)

trend_friendly = (
    trend_quiet_branch
    | trend_broad_branch
    | trend_narrow_branch
)
```

의도는 아래와 같다.

- `trend_common_gate`: 추세 레짐이 공통으로 요구하는 최소 절대 강도와 스트레스 상한
- `trend_quiet_branch`: squeeze 성격의 추세
- `trend_broad_branch`: breadth가 확인된 추세
- `trend_narrow_branch`: raw `narrow_tag` 전체가 아니라, “안전한 narrow leadership”만 제한적으로 편입

중요:

- `quiet_tag`, `narrow_tag` 자체는 호환성과 설명용으로 남아 있다.
- 그러나 현재 `trend_friendly` 계산식은 branch 안에서 `narrow_tag`를 직접 참조하지 않는다.
- 즉 읽는 사람이 보기에 `common gate + branch OR` 구조가 바로 보이도록 정리한 상태다.

### 19.3 coverage 숫자 해석 주의
이번 세션에서 coverage 숫자는 두 종류가 섞여 나왔다.
다음 에이전트가 헷갈릴 수 있으니 분리해서 남긴다.

이전에 실험 메모에서 사용한 대표 숫자:

- coverage `0.3144`

이 숫자는 실험용 backtest slice / 기존 비교 맥락에서 사용한 값이다.

이번 턴 말미에 raw regime frame 기준으로 다시 확인한 값:

- `coverage_all_rows = 0.2872`
- `coverage_evaluable_rows = 0.2971`
- `rows = 7778`
- `evaluable_rows = 7519`

즉 다음 에이전트는
coverage 숫자를 말할 때 반드시 분모를 구분해야 한다.

- `all_rows`: raw frame 전체 날짜
- `evaluable_rows`: warmup/unavailable 제외 후 계산 가능한 날짜
- 실험용 backtest slice: 특정 시작/종료 구간 + 분석 맥락에서 본 coverage

따라서 `31.4% vs 28.7%`가 곧바로 “코드가 바뀌었다”는 뜻은 아니다.
분모와 slice 차이가 섞여 있을 가능성이 높다.

다음 세션에서 이 부분이 중요해지면,
반드시 “어떤 기간 / 어떤 분모”인지 함께 적을 것.

### 19.4 `stats.plot()`과 `wealth` 차이 해석
이번 세션에서 사용자와 가장 길게 맞춘 개념 중 하나가 이 부분이다.

핵심 결론:

- `stats.plot()`은 “신호가 나온 날의 코호트 품질”을 보여준다.
- `wealth` / `run()`은 “그 신호가 실제로 얼마나 자주 발생해서 자본이 얼마나 오래 노출되었는지”를 포함한 실제 포트폴리오 결과다.

중요한 해석 포인트:

- 처음에는 `stats.plot()`에서 annualized 차이가 큰데 `wealth CAGR` 차이가 작아서 혼란이 있었다.
- 결론적으로 핵심은 `20개 코호트 분할`보다 `평균 노출도(mean_exposure)`였다.
- 즉 코호트 품질이 좋아도, 신호가 드물고 실제 자본 노출이 낮으면 `wealth CAGR` 차이는 작게 나온다.

이 논의 이후,
`Pattern Frequency` 차트는 잘못된 해석을 유도한다고 판단했고,
이제 `StatsCollection.plot()`의 4번째 축은 `Pattern Exposure (%)`다.

정의:

- `by="day"`: `신호가 있었던 날짜 수 / 전체 날짜 수`
- `by="event"`: `패턴 event 수 / 산출 가능한 전체 stock-date opportunity 수`

즉 이제 4번째 축은 benchmark 대비 비율이 아니라
absolute exposure다.

### 19.5 `Backtest` / `Simulator` UX 변경점
이번 세션 이전까지 누적된 UX 변경을,
다음 에이전트가 한 번에 이어받기 쉽게 묶어 적는다.

#### A. 기본 레짐 자동 부착
`Backtest(..., regime=regime)`를 넣으면,
`analyze()`에 넘긴 패턴들과 benchmark에 default regime가 자동 부착된다.

즉 노트북에서는 아래처럼 쓸 수 있다.

```python
regime = Regime().on(kind="trend_friendly", market="kospi")
bt = Backtest(start=start, end=end, by="day", benchmark=benchmark, univ=univ, regime=regime)
```

그리고 이후 패턴은 `.when(regime)`를 반복할 필요가 없다.

#### B. `Pattern.named()`
패턴 이름 지정은 이제
`.named("...")`
를 쓴다.

예:

```python
base = (bb + high52w + uptrend + mfi_high).named("trend_base")
```

`name()` 체이닝은 속성과 충돌 위험 때문에 버렸다.

#### C. `bt.plot_wealth_curves()`
`Backtest.plot_wealth_curves()`는
마지막 `analyze()` 결과 전체를 한 화면에 wealth 차트로 그리고,
아래에 요약 테이블을 반환한다.

요약 테이블에는 `final_wealth` 열이 포함되어 있다.

즉:

- `total_return`: 누적 수익률
- `final_wealth = 1 + total_return`

#### D. KOSPI 오버레이 옵션
`wealth` 계열 차트에서
KOSPI 정규화 기준선을 옵션으로 그릴 수 있게 했다.

현재 시그니처:

```python
bt.plot_wealth_curves(..., show_kospi=True)
simul.plot(show_kospi=True)
```

기본값은 `False`다.
`KOSDAQ` 오버레이는 제거했다.

#### E. 레짐 음영
`Backtest(..., regime=...)`로 실행한 경우,
`wealth` 차트 배경에 활성 레짐 구간을 silver 톤으로 음영 처리한다.

동일하게 동작하는 곳:

- `bt.plot_wealth_curves(...)`
- `simul = bt.run(...); simul.plot(...)`

### 19.6 `total_fee_paid` 해석
사용자가 이 지표 의미를 물어봤고,
다음 에이전트도 같은 질문을 받을 가능성이 높아서 남긴다.

`total_fee_paid`는
현재 wealth 기준 수수료가 아니라,
전체 기간 동안 코호트 매수/매도 때 발생한 수수료를 전부 합친 누적치다.

단위는 원화가 아니라
`wealth`와 같은 정규화 단위다.
즉 시작 자산을 `1.0`으로 놓고 돌린 기준이다.

예:

- `total_fee_paid = 17.3`

이면

- “초기자산의 17.3배를 누적 수수료로 냈다”
- 또는 “초기자산 대비 1730% 수준의 누적 수수료가 돌았다”

는 뜻이다.

이 값이 큰 이유는
같은 자본이 여러 코호트로 반복 회전하기 때문이다.

### 19.7 `trend` 패턴 실험 상태
#### A. 대표 패턴
현재 대표 패턴은 아래 둘 비교로 본다.

- `trend_base`
- `trend_amount1.5x`

정의:

- `trend_base = Bollinger breakout + 52주 고가 + MA200 상향 + MFI > 50`
- `trend_amount1.5x = trend_base + AmountSurge(20, 1.5x)`

#### B. RelativeStrength
현재까지는 아래 테스트를 했고,
주력 필터로 채택하지 않았다.

- `RS60 >= 0`
- `RS60 >= 5%p`
- `RS120 >= 0`

이유:

- 표본은 줄지만,
  현재 breakout 기준선을 안정적으로 이기지 못했다.

#### C. RetestBreakout
`RetestBreakout`은 2차 정교화까지 했다.

추가했던 것:

- `max_retest_days`
- 실제 retest/reclaim 조건
- optional `breakout_amount_threshold`

그러나 결론은 아직 동일하다.

- benchmark 대비로는 완전한 실패는 아니었음
- 하지만 `trend_base` 또는 `trend_amount1.5x`를 넘지 못함
- 따라서 현 단계에서는 주력 채택이 아니라 “실험용 second-entry 후보” 정도

### 19.8 `panic_rebound_risk` 패턴 실험 상태
현재 결론은 거의 명확하다.

- `panic_rebound_risk`는 기본적으로 `off`가 우선이다.
- 예외 전략이 꼭 필요하면 `MFI oversold_rebound` 정도만 가장 덜 나쁜 후보로 남겨둔다.

`PanicRebound` 1차 구현은 완료했고,
volume spike 옵션까지 실험했지만
전체기간 기준으로 음수 성과가 강하게 나왔다.

즉 다음 에이전트는
panic 영역에서 “새 custom pattern을 더 만드는 것”보다,
정책 자체를 정리하는 쪽이 우선이다.

추천 정책 후보:

1. `panic_rebound_risk = 완전 off`
2. `panic_rebound_risk = MFI oversold_rebound만 예외 허용`

### 19.9 노트북 상태
현재 작업 노트북은
`레짐-패턴실험 2026.03.17.ipynb`
이다.

공통 설정 셀:

- import / `start` / `end` / `univ` / `benchmark`
- `benchmark = Pattern(name="benchmark")`

레짐별 섹션 구조:

- `trend_friendly`
  - 바로 아래에 `regime` / `bt` 정의 셀
  - 그 아래 `대표 패턴`
  - 그 아래 `실험`
- `panic_rebound_risk`
  - 바로 아래에 `regime` / `bt` 정의 셀
  - 그 아래 `대표 패턴`
  - 그 아래 `실험`

중요:

- 이번 세션 중간에 노트북 JSON 편집 과정에서
  `trend_friendly` 공통 셀의 `regime=regime`가 주석으로 빠진 적이 있었다.
- 이건 이미 수정했다.
- 즉 현재 on-disk 파일 기준으로는 `trend` / `panic` 둘 다 `Backtest(..., regime=regime)` 상태다.

또 하나 중요:

- VS Code 노트북 탭이 예전 cached 상태를 보여준 적이 있었다.
- 즉 다음 세션에서 “분명 파일은 바뀌었는데 화면이 다르게 보인다”면
  노트북 탭을 닫고 다시 열거나,
  파일 리로드를 먼저 의심하는 편이 좋다.

### 19.10 현재 노트북에서 바로 다시 확인할 것
다음 에이전트가 집 맥북에서 이어받으면,
우선 아래 순서로 확인하는 것이 가장 효율적일 가능성이 높다.

1. `레짐-패턴실험 2026.03.17.ipynb`의 `trend_friendly` 공통 셀 재실행
2. 대표 패턴 셀 실행
3. `stats.plot()`의 `Pattern Exposure (%)`
4. `bt.plot_wealth_curves(..., show_kospi=True)`에서
   `trend_base` vs `trend_amount1.5x`의 `wealth`, `final_wealth`, `mean_exposure`
5. 필요하면 AmountSurge / RS / Retest 실험 셀 순서대로 재확인

### 19.11 실제 미해결 상태
아직 끝나지 않은 질문은 아래다.

#### A. `trend_friendly`를 hard gate로 계속 쓸지
현재까지의 해석:

- strict trend gate는 지나치게 희소했다.
- 완화 후에는 훨씬 실전적이 됐지만,
  여전히 “전체 구간에서 패턴을 돌린 결과”와 비교하면 absolute wealth 차이가 클 수 있다.

즉 남은 핵심 질문은:

- `trend_friendly`를 hard on/off gate로 계속 유지할 것인가
- 아니면 `panic`만 확실히 빼고 나머지는 더 넓게 허용할 것인가

이건 다음 세션에서 가장 중요한 판단 포인트 중 하나다.

#### B. `contrarian_friendly` 섹션 실전성
문서상으로는 정의돼 있지만,
현재 노트북에서는 실전 후보가 충분히 정리되지 않았다.

즉 지금 레짐 재설계는 사실상

- `trend_friendly`
- `panic_rebound_risk`

두 축을 먼저 검증한 상태에 가깝다.

`contrarian_friendly`는
다음 단계에서 정말 독립 가치가 있는지,
패턴이 충분한지부터 다시 점검해야 한다.

#### C. coverage 숫자 재검산
앞서 적었듯
`trend_friendly` coverage는 분모와 구간에 따라 숫자가 달라 보일 수 있다.

맥북에서 이어받는 에이전트는
다음번에 coverage를 다시 언급할 때
반드시 아래 셋을 같이 적는 편이 좋다.

1. 기간
2. 분모(`all rows` / `evaluable rows` / `backtest slice`)
3. market / univ 기준

### 19.12 현재 코드/문서 파일 중 우선 참고 순서
다음 에이전트에게 추천하는 읽기 순서는 아래다.

1. 이 `CONTEXT.md`
2. `레짐분류_실용재설계안.md`
3. `src/regime.py`
4. `레짐-패턴실험 2026.03.17.ipynb`
5. `src/backtest.py`
6. `src/simulate.py`

특히 `레짐분류_실용재설계안.md`는
현재 `trend_common_gate + quiet/broad/narrow branch` 구조와
`safe narrow leadership` 편입까지 반영돼 있다.

### 19.13 이번 세션에서 실제로 확인한 검증
무거운 전체 노트북 실행은 WSL 메모리/시간 때문에 일부러 피했다.
대신 아래 수준까지는 확인했다.

- `python -m py_compile src/regime.py`
- `python -m py_compile src/backtest.py src/simulate.py tests/test_backtest_context.py`
- `python -m unittest tests.test_backtest_context -v`
- 노트북 코드 셀 AST 파싱
- `trend_friendly` old/new mask 동일성 확인
- KOSPI 오버레이 옵션 테스트

즉 코드 구조와 단위 동작은 신뢰해도 되지만,
맥북에서 이어받는 다음 에이전트는
실제 notebook 셀을 다시 실행해 결과를 확인하는 단계가 꼭 필요하다.

### 19.14 다음 에이전트에게 바로 줄 수 있는 시작 문장
사용자가 집에서 다시 이렇게 말할 가능성이 높다.

```text
CONTEXT.md를 참고로 해서 후속작업 진행하자.
```

그 경우 다음 에이전트는
우선 `19.10 현재 노트북에서 바로 다시 확인할 것`
순서대로 움직이면 된다.

현재 가장 자연스러운 다음 단계는 아래 둘 중 하나다.

1. `trend_friendly` 완화 후 실제 wealth / mean_exposure 개선을 노트북에서 재확인하고,
   hard gate 유지 여부를 판단
2. `panic_rebound_risk`를 정책적으로 `off`로 고정할지,
   `MFI oversold_rebound` 예외 허용으로 둘지 결정

---

## 20) 2026-03-17 집(맥북) 세션 추가 인계

이번 세션에서는 WSL에서 정리한 가설을 맥북 환경에서 다시 확인하고,
panic 정책을 더 실전적으로 다루기 위해
`panic_entry_off`와 `panic_flatten`을 구분할 수 있게 최소 기능을 추가했다.

### 20.1 맥 환경 확인
- `metricstudio` conda env는 맥에서 `~/anaconda3/envs/metricstudio`에 있었다.
- 아래 검증은 맥 환경에서 실제로 다시 통과했다.
  - `python -m py_compile src/regime.py src/backtest.py src/pattern.py src/stats.py src/simulate.py ...`
  - `python -m unittest tests.test_pattern_filters tests.test_backtest_context -v`

참고:
- macOS sandbox 때문에 `pyarrow`가 `sysctlbyname` 관련 warning을 출력했지만,
  계산/테스트 자체를 막는 오류는 아니었다.

### 20.2 `trend_friendly` hard gate 재확인 결론
맥에서 다시 확인한 결과,
`trend_friendly`는 코호트 품질 분리 자체는 맞지만
현재 대표 패턴의 기본 hard gate로 두기에는 희소 비용이 너무 컸다.

대표 비교(`1M`, `trade_price_mode='당일종가'`):

- `amount_all`
  - `final_wealth 116.3206`
  - `cagr 0.2008`
  - `mean_exposure 0.6388`
- `amount_trend`
  - `final_wealth 13.3157`
  - `cagr 0.1048`
  - `mean_exposure 0.2626`

즉 현재 실전 기본값으로는
`trend_friendly hard on`보다
더 넓게 돌리되 panic 정책을 따로 두는 쪽이 자연스럽다.

같은 세션에서 확인한 `trend_friendly` coverage:

- `all_rows`: `0.2872`
- `evaluable_rows`: `0.2971`

### 20.3 Regime API 단순화
이후 사용자가 지적한 대로,
기존 `Regime().off(...)`, `Regime().flatten(...)`는
entry gate와 exit policy를 한 객체에 섞어 API를 오히려 복잡하게 만들었다.

그래서 현재 `Regime`는 다시
“시장 날짜 집합을 표현하는 객체”로만 단순화했다.

현재 지원 연산:

- `a + b`: 교집합
- `a - b`: 차집합
- `a | b`: 합집합
- `~a`: 여집합

중요:

- Python의 `not a`는 오버로딩할 수 없으므로 `~a`를 써야 한다.
- 즉 `not_panic = ~panic` 형태가 현재 정식 문법이다.
- `flatten`처럼 강제 청산을 의미하는 정책은 더 이상 `Regime` 안에 넣지 않는다.
  그건 나중에 필요하면 별도 exit-policy 개념으로 다루는 편이 맞다.

### 20.4 현재 사용 방법 가이드
현재 권장 표기:

```python
from src.regime import Regime

panic = Regime().on(kind="panic", market="kospi")
trend_regime = Regime().on(kind="trend", market="kospi")
contrarian_regime = Regime().on(kind="contrarian", market="kospi")

not_panic = ~panic
trend_only = trend_regime - panic
contrarian_only = contrarian_regime - panic
```

예:

```python
trend = (bb + high52w + uptrend + mfi_high + amount15).named("trend_amount1.5x")
trend_no_panic = trend.when(~panic).named("trend_no_panic")

contra = MFI(name="contra_failure").on(
    trigger="bullish_failure_swing",
    lower=20,
    stay_days=1,
    cooldown_days=5,
).when(contrarian_regime - panic)
```

### 20.5 핵심 프레임 정리
사용자 지적대로
`panic이 아니면 trend를 쓴다`는 식의 단순화는 맞지 않다.

현재 더 자연스러운 프레임은 아래다.

1. `panic`은 selector가 아니라 veto 집합
2. `trend` / `contrarian`은 family 선택 집합
3. 각 family branch는 자기 성과가 admission을 통과할 때만 켠다

즉 실전 구조는 아래처럼 비대칭적일 수 있다.

- `trend`는 패턴 자체가 이미 강한 추세 정보를 담고 있으면 굳이 hard gate로 다시 자르지 않는다.
- `contrarian`은 `contrarian - panic`처럼 더 보수적으로 붙인다.

이게 현재 검증 결과와도 가장 잘 맞았다.

### 20.6 family router 재검증 결과
재현 스크립트:

- `scripts/validate_regime_router.py`

실행:

```bash
source ~/anaconda3/etc/profile.d/conda.sh && conda activate metricstudio
python scripts/validate_regime_router.py
```

스크립트가 비교하는 것:

- `trend_amount1.5x`
- `trend_no_panic = trend.when(~panic)`
- `blend_oversold = trend | contra_oversold.when(contrarian - panic)`
- `blend_failure = trend | contra_failure.when(contrarian - panic)`

현재 `1M`, `당일종가` 결과:

- `trend_amount1.5x`
  - `final_wealth 116.3206`
  - `cagr 0.2008`
  - `mdd -0.1559`
- `trend_no_panic`
  - `final_wealth 67.0321`
  - `cagr 0.1756`
  - `mdd -0.1640`
- `blend_oversold`
  - `final_wealth 102.6062`
  - `cagr 0.1951`
  - `mdd -0.2407`
- `blend_failure`
  - `final_wealth 107.6417`
  - `cagr 0.1973`
  - `mdd -0.2440`

해석:

- `~panic`를 trend에 hard gate로 다는 것도 아직 성과를 깎는다.
- `contrarian - panic` branch를 trend 위에 overlay하는 프레임 자체는 맞다.
- 하지만 현재 구현된 contrarian 후보(`MFI oversold_rebound`, `bullish_failure_swing`)는
  대표 추세 패턴보다 더 좋은 admission branch는 아직 아니다.

### 20.7 이번 세션 기준 추천 운영 결론
현재 기준으로 가장 실용적인 결론은 아래다.

1. 기본 패턴은 여전히 `trend_amount1.5x`
2. `Regime`는 now pure set algebra로만 사용
3. `panic`은 veto 집합으로 이해하되, trend branch에 hard gate로 바로 넣지는 않음
4. `contrarian`은 `contrarian - panic` 형태로 branch를 설계하되, 현재 후보는 아직 채택 전

즉 다음 세션에서 가장 자연스러운 후속 작업은
`contrarian_friendly` 안에서 실제로 `trend_amount1.5x`를 이길 수 있는
새 contrarian branch 후보를 만드는 것이다.

### 20.8 contrarian 후보 재탐색: `relative loser + oversold`
이번 세션 후반에는
기존 `MFI oversold_rebound` / `bullish_failure_swing`이 약했던 이유를
`oversold 자체`보다 `최근 loser를 유동성 공급 관점에서 받는 구조`가 부족했기 때문이라고 보고,
후보를 다시 만들었다.

핵심 구현 변화:

- `RelativeStrength`가 이제 `trigger='above' | 'below'`를 둘 다 지원한다.
- 즉 상대강도 상방 돌파뿐 아니라
  `시장 대비 최근 n일 상대낙폭` 같은 하방 조건도 같은 클래스로 표현할 수 있다.

현재 권장 후보 예시는 아래다.

```python
from src.pattern import RelativeStrength, MFI

contra_loser5_mfi35 = (
    RelativeStrength(name="5D상대낙폭").on(
        market="kospi",
        window=5,
        trigger="below",
        threshold=-0.08,
        cooldown_days=5,
    )
    + MFI(name="MFI<35").on(
        trigger="below",
        threshold=35,
        stay_days=1,
        cooldown_days=0,
    )
).named("loser5_mfi35")
```

의미:

- 최근 5거래일 동안 시장 대비 `-8%` 이상 뒤처진 종목
- 동시에 MFI가 `35` 아래로 눌려 있는 종목
- 즉 `상대 loser + oversold` 조합

검증 스크립트:

- `scripts/validate_contrarian_candidates.py`

실행:

```bash
source ~/anaconda3/etc/profile.d/conda.sh && conda activate metricstudio
python scripts/validate_contrarian_candidates.py
```

현재 `contrarian` 레짐 inside, `1M`, `당일종가` 기준 대표 결과:

- `benchmark`
  - `final_wealth 1.0562`
  - `cagr 0.0021`
  - `mdd -0.2089`
- `mfi_oversold`
  - `final_wealth 1.0837`
  - `cagr 0.0031`
  - `mdd -0.1932`
- `mfi_failure`
  - `final_wealth 1.1269`
  - `cagr 0.0046`
  - `mdd -0.2012`
- `loser5_amt`
  - `final_wealth 1.3970`
  - `cagr 0.0129`
  - `mdd -0.1709`
- `loser5_mfi35`
  - `final_wealth 1.4377`
  - `cagr 0.0141`
  - `mdd -0.1779`
  - `cohort_win_rate 0.5605`

`loser5_mfi35`의 benchmark 대비 연환산 기하평균 격차(after cost):

- `1M +0.1000`
- `2M +0.0509`
- `3M +0.0196`
- `6M -0.0414`

window별 `1M` 격차도 전 구간 플러스였다.

- `2000-2006 +0.0448`
- `2007-2012 +0.1280`
- `2013-2018 +0.2206`
- `2019-2025 +0.0385`

현재 해석:

- contrarian 레짐에서 쓸 패턴 후보가 처음으로 꽤 선명하게 잡혔다.
- 다만 edge는 `1M~3M` short-horizon에 집중되고 `6M`에서는 약해진다.
- 따라서 이 branch를 실제로 쓰려면
  contrarian 쪽은 기본적으로 `1M` 또는 길어도 `2M~3M` 성격으로 보는 편이 맞다.

### 20.9 배타적 regime router 1차 체크
새 contrarian 후보가 admission을 통과하는지 확인한 뒤,
아주 단순한 배타적 router도 바로 찍어봤다.

구조:

```python
trend_branch = trend_amount1.5x.when(Regime().on(kind="trend", market="kospi"))
contra_branch = contra_loser5_mfi35.when(Regime().on(kind="contrarian", market="kospi"))
router = (trend_branch | contra_branch).named("exclusive_router")
```

`1M`, `당일종가` 결과:

- `trend_amount1.5x`
  - `final_wealth 116.3206`
  - `cagr 0.2008`
  - `mdd -0.1559`
- `contra_loser5_mfi35`
  - `final_wealth 1.4377`
  - `cagr 0.0141`
  - `mdd -0.1779`
- `exclusive_router`
  - `final_wealth 19.0641`
  - `cagr 0.1201`
  - `mdd -0.2092`

해석:

- `trend when trend` 단독보다는 개선되지만,
  아직 전체 실전 기본값으로 `trend_amount1.5x`를 대체할 정도는 아니다.
- 즉 이번 단계의 결론은
  “contrarian branch 후보는 찾았지만,
  양 family를 hard-exclusive router로 묶는 것은 아직 시기상조”에 가깝다.

### 20.10 contrarian vs trend, horizon, router 추가 검증
사용자 지적대로
contrarian 후보를 평가할 때는
단순 benchmark뿐 아니라
`contrarian 레짐 inside에서 trend_amount1.5x보다 낫냐`도 같이 봐야 한다.

또 contrarian은
trend와 같은 `1M~6M` 홀드 감각보다
더 짧은 horizon이나 더 빠른 exit에서 edge가 살아날 수 있으므로,
`1W~3M`까지 직접 다시 비교했다.

재현 스크립트:

- `scripts/validate_contrarian_router.py`

#### A. contrarian 레짐 inside 결과
핵심 비교:

- `trend_amount1.5x`
- `loser5_mfi35`
- `loser5_mfi35 + Bollinger(loss_cut='mid_stop')`
- `loser5_mfi35 + Bollinger(loss_cut='trailing_stop')`

대표 결과(`당일종가`):

- `1W`
  - `trend_amount1.5x`: `final_wealth 1.5084`, `mdd -0.1444`
  - `loser5_mfi35`: `0.7923`, `mdd -0.2977`
- `2W`
  - `trend_amount1.5x`: `1.1397`
  - `loser5_mfi35`: `0.9057`
- `3W`
  - `trend_amount1.5x`: `1.1492`
  - `loser5_mfi35`: `1.2691`
- `1M`
  - `trend_amount1.5x`: `1.2381`, `cagr 0.0083`, `mdd -0.0993`
  - `loser5_mfi35`: `1.4377`, `cagr 0.0141`, `mdd -0.1779`
- `2M`
  - `trend_amount1.5x`: `1.2701`, `cagr 0.0092`
  - `loser5_mfi35`: `1.5890`, `cagr 0.0180`
- `3M`
  - `trend_amount1.5x`: `1.3224`
  - `loser5_mfi35`: `1.3158`

결론:

- contrarian 후보는 `1W~2W`에서는 trend보다 약하다.
- 하지만 `3W~2M`에서는 `trend_amount1.5x`보다 낫다.
- `3M`부터는 우위가 거의 사라진다.

즉 현재 후보의 자연스러운 성격은
`초단기 반등(1W)`도 아니고 `장기 보유(3M+)`도 아니라,
대략 `3W~2M` 구간의 short swing에 가깝다.

추가로,
이번 1차 실험에서는 `mid_stop`, `trailing_stop` 모두 성과를 개선하지 못했다.
즉 현재 구현된 단순 Bollinger exit는
이 contrarian 후보의 edge를 더 선명하게 만들지 못했다.

#### B. 완화된 router 비교
사용자 제안처럼
너무 보수적인 `trend.when(trend) | contra.when(contrarian)` 대신
더 넓은 switch도 같이 비교했다.

비교 구조:

```python
exclusive_router = trend.when(trend) | contra.when(contrarian)
switch_on_contrarian = trend.when((~panic) - contrarian) | contra.when(contrarian)
switch_on_trend = trend.when(trend) | contra.when((~panic) - trend)
```

중요:

- `switch_on_contrarian`의 날짜 커버리지는 사실상 `~panic`이다.
- 왜냐하면 현재 정의에서 `contrarian`은 `~panic`의 부분집합이기 때문이다.
- 따라서 이 router의 공정한 기준선은 `trend_amount1.5x` ungated가 아니라
  `trend.when(~panic)`이다.

결과(`당일종가`):

- `1M`
  - `trend_amount1.5x`: `final_wealth 116.3206`, `cagr 0.2008`
  - `trend_no_panic`: `67.0321`, `0.1756`
  - `trend_non_contra`: `54.8815`, `0.1666`
  - `exclusive_router`: `19.0641`, `0.1201`
  - `switch_on_contrarian`: `77.1105`, `0.1820`
  - `switch_on_trend`: `17.2643`, `0.1158`
- `2M`
  - `trend_amount1.5x`: `61.5321`, `0.1718`
  - `trend_no_panic`: `43.8927`, `0.1566`
  - `trend_non_contra`: `35.2652`, `0.1469`
  - `exclusive_router`: `15.7502`, `0.1119`
  - `switch_on_contrarian`: `54.4666`, `0.1663`
  - `switch_on_trend`: `20.9832`, `0.1243`
 - `3M`
  - `trend_no_panic`: `31.7993`, `0.1424`
  - `trend_non_contra`: `24.8857`, `0.1317`
  - `switch_on_contrarian`: `31.5604`, `0.1421`

결론:

- 세 router 중에서는 `switch_on_contrarian`이 가장 낫다.
- 즉 현재 데이터에서는
  `trend를 기본으로 깔고, contrarian 구간에서만 contra로 교체`
  하는 쪽이
  `trend를 trend 레짐에만 묶는 방식`보다 훨씬 자연스럽다.
- 그리고 공정한 기준선인 `trend_no_panic`과 비교하면,
  `switch_on_contrarian`은 `1M`, `2M`에서는 더 낫고 `3M`에서는 거의 비슷하다.
- 다만 drawdown은 `trend_no_panic`보다 더 나쁘다.

추가 확인:

- `trend.when(panic)`도 따로 확인했는데,
  `1M final_wealth 1.7575`, `2M 1.4310`, `3M 1.4155`로
  panic 날짜 진입 cohort 자체가 완전히 나쁘지는 않았다.
- 그래서 `ungated trend`가 `trend_no_panic`보다 강한 것은 실제로 맞다.

주의:

- 여기서 `trend.when(panic)`는 “panic 날짜에 신규 진입한 cohort”를 뜻한다.
- `when(...)`은 진입만 제한하고 보유는 계속하므로,
  “panic 구간 안에서만 보유한 성과”와는 다르다.

현재 운영 결론은 이렇게 정리된다.

1. contrarian 후보는 이제 admission을 통과했다.
2. 하지만 그 edge는 `3W~2M`에 집중된다.
3. 그리고 현재 엔진에서 family별 horizon을 다르게 줄 수 없으므로,
   다음 단계는 `branch-specific horizon/exit`를 어떻게 표현할지 정하는 것이다.

### 20.11 panic 제거 비교와 현재 운영 판단

사용자가 노트북에서
`panic`을 완전히 빼고 아래 3개를 직접 비교했다.

- `benchmark`
- `trend_amount1.5x`
- `switch_without_panic`
  - `trend.when(~contrarian) | contra.when(contrarian)`
  - 이름은 과거 셀과의 연속성 때문에 `without_panic`이지만,
    실제 의미는 `panic`을 전혀 쓰지 않는 full-period switch다.

사용자 노트북 결과(`1M`, `당일종가`)는 다음과 같다.

- `benchmark`
  - `final_wealth 8.8713`
  - `cagr 0.0876`
  - `mdd -0.6290`
- `trend_amount1.5x`
  - `final_wealth 116.3206`
  - `cagr 0.2008`
  - `mdd -0.1559`
- `switch_without_panic`
  - `final_wealth 133.2989`
  - `cagr 0.2072`
  - `mdd -0.2471`

해석:

- 현재 `panic`을 라우터 앞단 veto로 넣는 것은 성과 개선 근거가 약하다.
- 이건 앞서 확인한
  `trend.when(panic)`의 cohort가 완전히 나쁘지 않았다는 결과와도 일치한다.
- 즉 현재 `panic` 정의는
  “이 구간에서는 trend와 contrarian이 둘 다 안 먹힌다”는 실패영역을
  잘 분리하지 못하고 있다.

따라서 현 시점 운영 판단은 다음이 맞다.

1. `panic`은 라우팅 규칙에서 제거한다.
2. 기본 비교축은
   `trend_amount1.5x` vs `switch_without_panic`
   로 둔다.
3. `panic`은 당분간 execution veto가 아니라
   diagnostic tag로만 남긴다.

`panic`을 다시 살리려면 기준이 더 엄격해야 한다.
필요한 조건은 아래 둘 중 하나다.

- `panic` inside에서 `trend`와 `contra`가 둘 다 구조적으로 약해야 한다.
- 또는 `panic` inside에서 강한 risk-control 전술
  (예: 신규진입 금지, 부분축소, 유동성 취약 종목만 축소 등)이
  라우터 성과를 실제로 개선해야 한다.

그 전까지는 `panic 재설계`보다
`contrarian branch`와 `branch-specific horizon/exit` 고도화가 우선이다.

### 20.12 branch별 horizon/익절/손절 지원 추가

`switch_without_panic`의 높은 MDD를 줄이기 위해,
이제 branch별로 서로 다른 실행정책을 줄 수 있게 최소 기능을 넣었다.

사용 문법:

```python
trend = trend_pattern.trade(target_horizon="1M")
contra = contra_pattern.trade(target_horizon="3W", stop_loss_pct=8)
router = trend.when(~contrarian) | contra.when(contrarian)
```

의미:

- `Pattern.trade(...)`는 entry rule이 아니라
  해당 branch에서 발생한 진입의 보유정책을 지정한다.
- `UnionPattern(|)` 안에서는 branch별 정책이 유지된다.
- 현재 지원:
  - `target_horizon`
  - `stop_loss_pct`
  - `take_profit_pct`

현재 추가된 검증:

- branch별 정책이 `UnionPattern`에서 유지되는지
- 같은 날 여러 branch가 동시에 잡혀도 자금이 한쪽 branch에만 몰리지 않는지
- branch별 `horizon`과 `stop_loss`가 실제 청산에 반영되는지

관련 파일:

- `src/pattern.py`
- `src/backtest.py`
- `src/simulate.py`
- `tests/test_backtest_context.py`
- `scripts/validate_branch_trade_policies.py`

현재 바로 볼 실험축은 아래 3개다.

1. `switch_without_panic`
2. `switch_contra_3w`
3. `switch_contra_3w_stop8`

즉 먼저 `contra` branch를
`1M 공통보유`에서 `3W` 또는 `3W + 8% stop`으로 바꿨을 때
`CAGR` 대비 `MDD`가 얼마나 줄어드는지를 확인하는 것이
지금 가장 자연스러운 다음 단계다.

실제 1차 비교 결과(`2000-01-01`~`2025-12-31`, `당일종가`, 기본 run horizon=`1M`):

- `trend_amount1.5x`
  - `final_wealth 116.3206`
  - `cagr 0.2008`
  - `mdd -0.1559`
- `switch_without_panic`
  - `final_wealth 133.2989`
  - `cagr 0.2072`
  - `mdd -0.2471`
- `switch_contra_3w`
  - `final_wealth 113.1369`
  - `cagr 0.1996`
  - `mdd -0.2711`
- `switch_contra_3w_stop8`
  - `final_wealth 94.7959`
  - `cagr 0.1914`
  - `mdd -0.2387`

해석:

- `contra` branch를 단순히 `3W`로 줄이는 것은 성과와 MDD 모두 악화됐다.
- `3W + 8% stop`은 `3W` 단독보다는 MDD를 줄였지만,
  여전히 `switch_without_panic`보다 CAGR이 낮고
  `trend_amount1.5x`보다도 열위다.
- 즉 branch별 실행정책 기능은 필요했고 구현도 됐지만,
  현재 `contrarian` 후보에 대한 첫 번째 policy guess(`3W`, `8% stop`)는
  문제를 해결하지 못했다.

현재 자연스러운 다음 단계:

1. `contra` branch의 보유기간을 줄이는 대신
   `1M` 유지 + tighter stop / take-profit 조합을 탐색
2. 또는 `contra` branch의 sizing 자체를 줄이는 방향 검토
3. 그 전까지 실전 기본형은 여전히 `switch_without_panic`
   또는 더 보수적으로는 `trend_amount1.5x`

### 20.13 성능 조사 및 최적화 메모 (2026-03-18)

사용자가 지적한 대로
현재 코드/노트북 실험은 체감상 너무 오래 걸린다.
그래서 먼저 `scripts/validate_branch_trade_policies.py`를 기준 workload로 잡고
맥 `metricstudio` 환경에서 실제 `cProfile`로 병목을 확인했다.

#### 20.13.1 기준 스크립트

```bash
conda run -n metricstudio python -m cProfile -o /tmp/branch.prof scripts/validate_branch_trade_policies.py
```

#### 20.13.2 프로파일 결과 요약

같은 스크립트/같은 `cProfile` 기준 총 실행시간:

- 1차 기준: `206.271s`
- 1차 최적화 후: `143.478s`
- 2차 최적화 후: `119.391s`

즉 현재까지:

- 1차 개선: `-30.4%`
- 2차 추가 개선: `-16.8%`
- 최초 기준 대비 총 개선: `-42.1%`

#### 20.13.3 현재 남은 상위 병목

2차 최적화 후 누적시간 상위:

- `Backtest.analyze`: `84.152s`
- `_run_pattern`: `82.739s`
- `_run_pattern_trim`: `57.410s`
- `_build_mask_matrix`: `56.633s`
- `load_adjusted_stock_duckdb`: `40.427s`
- `_prepare_stock_sources`: `31.665s`
- `_prepare_regime_sources`: `25.321s`
- `Backtest.run`: `24.394s`
- `Pattern.__call__`: `24.267s`
- `UnionPattern._base_mask`: `20.415s`

내부시간 상위로 보면:

- DuckDB dataframe materialize (`_duckdb.df`): `13.021s`
- numpy array copy: `10.936s`
- `Pattern.__call__`: `8.316s`
- `Simulator.run`: `7.377s`

결론:

- 기존에도 `numba`는 충분히 들어가 있었다.
  - 롤링 통계/누적수익/trim 집계는 이미 `numba` 경로를 탄다.
- 현재 병목의 중심은 `Simulator.run`이 아니라
  Python 쪽 패턴 평가/조합과 DuckDB 로딩이다.
- 즉 지금 단계에서 `numba`를 더 붙여도
  가장 큰 문제를 바로 해결하지는 못한다.

#### 20.13.4 이번에 반영한 최적화

1. `src/backtest.py`
   - homogeneous pattern의 `policy_id_matrix`는
     전체 패턴을 다시 per-column 재평가하지 않고
     이미 만든 `pattern_mask`에서 바로 생성
   - `Regime` frame을 `Backtest` 인스턴스 간에도 재사용하도록
     global cache 추가

2. `src/pattern.py`
   - `Pattern.__call__` mask를
     단순 `id(ndarray)`가 아니라
     실제 메모리 토큰 기반으로 캐시
   - `RelativeStrength.__call__`도 동일 방식 캐시
   - `UnionPattern` child mask cache를
     stock/market source 변경까지 반영하도록 보강
   - `Bollinger` trailing ATR cache도 동일하게 보강
   - `Trending(ma_trend_up/down)`의 Python loop를 벡터화

3. 테스트
   - 같은 underlying price series 재호출 시
     mask를 재계산하지 않는지
   - stock field가 바뀌면 캐시가 무효화되는지
     테스트 추가

#### 20.13.5 실무 해석

- 지금 가장 큰 체감 개선은
  `numba` 추가보다
  “같은 mask를 다시 계산하지 않기”에서 나왔다.
- 추가 `numba` 후보는
  현재 구조 그대로 함수 몇 개만 감싸는 방식보다는,
  pattern tree를 array kernel 단위로 재구성할 때 의미가 크다.
- 즉 다음 성능작업 우선순위는:
  1. DuckDB 로딩/materialize 줄이기
  2. `_prepare_stock_sources` 호출량 줄이기
  3. pattern tree 평가를 더 적은 pass로 합치기

#### 20.13.6 주의: branch-policy 재실행 값 갱신

최적화 이후 동일 스크립트를 다시 돌린 현재 값은 아래다
(`2000-01-01`~`2025-12-31`, `당일종가`, 기본 run horizon=`1M`).

- `trend_amount1.5x`
  - `final_wealth 116.3206`
  - `cagr 0.2008`
  - `mdd -0.1559`
- `switch_without_panic`
  - `final_wealth 133.2989`
  - `cagr 0.2072`
  - `mdd -0.2471`
- `switch_contra_3w`
  - `final_wealth 177.5332`
  - `cagr 0.2205`
  - `mdd -0.2980`
- `switch_contra_3w_stop8`
  - `final_wealth 122.9436`
  - `cagr 0.2034`
  - `mdd -0.2899`

즉 현재는
`contra=3W`가 수익 기준으로는 더 강하지만
MDD는 더 나쁘다.
다음에 branch policy를 논의할 때는
이 최신 값 기준으로 다시 판단해야 한다.

### 20.14 새 스레드용 종합 인계 (2026-03-18)

이 섹션은 다음 세션 에이전트가
이 파일만 읽고도 현재 상태를 빠르게 파악하도록 만든
"최신 기준 handoff"다.

중요:

- 아래 내용이 현재 기준 최신 요약이다.
- 같은 파일 안의 더 오래된 수치/결론과 충돌하면
  이 섹션과 `20.11.6`의 최신 수치를 우선한다.
- 특히 branch-policy 관련 예전 수치
  (`switch_contra_3w`, `switch_contra_3w_stop8`)는
  현재 최신 재실행 값으로 갱신되었으니
  오래된 값을 다시 인용하지 말 것.

#### 20.14.1 현재 문제의식

이 프로젝트의 현재 핵심 과제는:

1. `trend` 대표 패턴은 이미 충분히 강하다.
2. `contrarian` 레짐 안에서만 선명하게 작동하는
   별도 `contrarian` 패턴을 찾아야 한다.
3. 그 패턴이 전체 구간의 기본 `trend`를 대체할 정도로 강하지 않더라도,
   특정 레짐에서 배타적으로 스위칭할 가치가 있는지 판단해야 한다.
4. 동시에 현재 실험/백테스트가 너무 느리므로,
   연구 속도를 해치는 병목을 줄여야 한다.

#### 20.14.2 현재 합의된 개념 프레임

현재는 아래 해석을 기본 전제로 삼는다.

- `Regime`는 pure set algebra 객체다.
  - `a + b`: 교집합
  - `a - b`: 차집합
  - `a | b`: 합집합
  - `~a`: 여집합
- `panic`은 아직 실전 하드 veto로 채택하지 않는다.
  - 현재 정의의 분별력이 약하다고 의심되고,
    실제로 panic을 빼고 돌린 쪽이 더 좋게 나온 적이 많다.
  - 따라서 지금은 `panic`을 diagnostic tag에 가깝게 본다.
- 현재 실전 비교의 중심은
  `trend_amount1.5x` vs `switch_without_panic`이다.
- `switch_without_panic`의 의미는:
  - `contrarian` 레짐이면 `contrarian` 패턴 사용
  - 그 외 전부는 `trend` 패턴 사용
  - 이름에 `without_panic`가 있지만,
    실제로는 panic을 특별 취급하지 않는 router 이름으로 굳어졌다.

#### 20.14.3 현재 대표 패턴 / 대표 레짐

대표 trend 패턴:

- `trend_amount1.5x`
  - `Bollinger(breakout_up, bandwidth_max=0.05, breakout_cooldown_days=3)`
  - `High(window=240, threshold=0.90, stay_days=1)`
  - `Trending(trigger='ma_trend_up', window=200)`
  - `MFI(trigger='above', threshold=50)`
  - `AmountSurge(window=20, threshold=1.5)`

현재 contrarian 후보:

- `loser5_mfi35`
  - `RelativeStrength(window=5, trigger='below', threshold=-0.08, market='kospi')`
  - `MFI(trigger='below', threshold=35, stay_days=1, cooldown_days=0)`

현재 기본 레짐 표현:

```python
panic = Regime().on(kind="panic", market="kospi")
trend_regime = Regime().on(kind="trend", market="kospi")
contrarian = Regime().on(kind="contrarian", market="kospi")
```

현재 가장 중요한 router:

```python
switch_without_panic = (
    trend_amount1.5x.when(~contrarian)
    | loser5_mfi35.when(contrarian)
).named("switch_without_panic")
```

#### 20.14.4 현재까지의 실험 결론

1. `trend_friendly` hard gate는 너무 보수적이었다.
   - trend 패턴 inside 성능은 선명하지만,
     coverage 희생이 너무 커서 기본 hard gate로 쓰기 어렵다.

2. `contrarian` 레짐 inside에서는
   `loser5_mfi35`가 benchmark보다 낫고,
   어떤 horizon에서는 `trend_amount1.5x` inside 성과도 이긴다.
   - 특히 `3W~2M` 정도에서 상대적으로 강했다.

3. 하지만 전체 엔진 차원에서는
   `contrarian` branch의 MDD가 아직 너무 높다.
   - 즉 "패턴을 찾았다" 수준이지
     "실전 기본형으로 채택했다" 수준은 아니다.

4. `panic`은 현재 정의 그대로는
   `trend`와 `contrarian`을 모두 막아야 할 독립 실패영역으로 보기 어렵다.
   - 따라서 현재 우선순위는 panic 재설계보다
     contrarian branch를 더 선명하게 만드는 쪽이다.

#### 20.14.5 branch policy 기능 상태

현재 코드베이스는 branch별 보유정책을 이미 지원한다.

- `Pattern.trade(...)`
  - `target_horizon`
  - `stop_loss_pct`
  - `take_profit_pct`
- `UnionPattern(|)` 내부에서도 branch별 policy가 유지된다.

예:

```python
trend_branch = trend_amount1.5x.trade(target_horizon="1M")
contra_branch = loser5_mfi35.trade(target_horizon="3W", stop_loss_pct=8)
router = trend_branch.when(~contrarian) | contra_branch.when(contrarian)
```

즉 현재 병목은 "이 기능이 없어서"가 아니라
"어떤 contrarian branch policy가 좋은지 아직 못 찾았다"는 데 있다.

#### 20.14.6 현재 최신 수치에서 봐야 할 비교축

최신 재실행 기준(`20.13.6`, `2000-01-01`~`2025-12-31`, `당일종가`, 기본 horizon=`1M`):

- `trend_amount1.5x`
  - `final_wealth 116.3206`
  - `cagr 0.2008`
  - `mdd -0.1559`
- `switch_without_panic`
  - `final_wealth 133.2989`
  - `cagr 0.2072`
  - `mdd -0.2471`
- `switch_contra_3w`
  - `final_wealth 177.5332`
  - `cagr 0.2205`
  - `mdd -0.2980`
- `switch_contra_3w_stop8`
  - `final_wealth 122.9436`
  - `cagr 0.2034`
  - `mdd -0.2899`

현재 해석:

- 수익 기준으로는 `switch_without_panic`, `switch_contra_3w`가
  `trend_amount1.5x`보다 강하다.
- 하지만 `MDD`는 훨씬 나쁘다.
- 특히 `switch_contra_3w`는
  수익을 더 올리는 대신 MDD가 너무 커진다.
- `3W + 8% stop`은
  pure `3W`보다는 MDD를 조금 줄이지만
  여전히 drawdown 문제를 해결했다고 보긴 어렵다.

즉 현재 연구 질문은
"contrarian를 넣을 것인가"가 아니라
"contrarian를 어떤 policy / sizing / timing으로 넣어야
MDD를 통제하면서 가치가 있는가"다.

#### 20.14.7 현재 부족한 점

아직 부족한 것은 아래다.

1. `contrarian` branch sizing 제어가 없다.
   - 현재는 branch별 horizon/stop/take는 되지만,
     branch별 비중 축소는 아직 없다.
   - MDD를 줄이려면 이것이 중요할 가능성이 높다.

2. `panic` 재설계가 안 끝났다.
   - 현재 정의는 diagnostic 성격만 유지하는 편이 안전하다.
   - 나중에 다시 본다면
     "trend와 contrarian가 모두 안 먹히는 실패영역" 관점에서
     다시 설계해야 한다.

3. `contrarian` entry timing을 더 정교하게 다듬지 못했다.
   - 예: 급락 후 n일 반등 확인,
     유동성/거래대금 조건,
     rebound confirm,
     size bucket 분리 등

4. 연구 속도를 해치는 성능 병목이 아직 남아 있다.
   - 특히 DuckDB 로딩/materialize
   - `_prepare_stock_sources`
   - pattern tree 재평가

#### 20.14.8 다음 세션에서 가장 자연스러운 작업 우선순위

다음 세션 에이전트는 아래 순서로 판단하면 된다.

1. `contrarian` branch의 MDD를 줄일 수 있는
   가장 값싼 방법부터 검증
   - `target_horizon`/`stop_loss_pct`/`take_profit_pct` 조합 재탐색
   - 가능하면 branch-specific sizing 설계 검토

2. `switch_without_panic` vs `trend_amount1.5x` 비교를
   최신 코드 기준으로 다시 넓은 축에서 검증
   - `1W, 2W, 3W, 1M, 2M, 3M, 6M`
   - 필요하면 `익절/손절` 조합 포함

3. `contrarian` branch 자체를 더 날카롭게 만들 새 후보를 탐색
   - 단순히 현재 `pattern.py` 구현에 갇히지 말고,
     새 패턴 조합 또는 레짐 정의 수정도 허용
   - 다만 과최적화는 피할 것

4. 성능작업은 2순위지만 계속 중요하다.
   - `numba`보다 먼저 DuckDB/materialize와 source-prepare 병목을 줄이는 편이 유리

#### 20.14.9 재현에 자주 쓰는 스크립트

- `scripts/validate_contrarian_candidates.py`
  - contrarian 레짐 inside 후보 비교
- `scripts/validate_contrarian_router.py`
  - router / horizon / benchmark 비교
- `scripts/validate_branch_trade_policies.py`
  - branch별 `trade(...)` policy 비교

권장 환경:

```bash
conda run -n metricstudio python scripts/validate_branch_trade_policies.py
```

mac 환경에서는 `pyarrow`가 `sysctlbyname` warning을 낼 수 있는데,
현재까지 계산/검증을 막는 오류는 아니었다.

#### 20.14.10 다음 에이전트가 특히 주의할 점

- panic을 현재 단계에서 hard veto로 복구하지 말 것.
- `switch_without_panic`의 MDD 문제를
  단순한 감으로 해석하지 말고,
  branch policy / sizing / timing으로 분해해서 볼 것.
- `CONTEXT.md` 안의 예전 수치와
  최신 재실행 수치를 섞어 인용하지 말 것.
- notebook에 바로 손대기 전에
  스크립트로 재현 가능한 비교축부터 확보할 것.

### 20.15 trend strength router 실험 메모 (2026-03-18)

이번 턴에서는 `contrarian` 연구를 잠시 홀드하고,
사용자 요청대로
`trend_amount1.5x`를 전체 구간 기본 패턴으로 둔 뒤
특정 레짐에서만 trend 계열 variation을 강화/완화하는 router를 점검했다.

핵심 질문은 아래였다.

1. `quiet / broad / narrow / panic` 내부에서
   어떤 trend variation이 제일 잘 맞는가
2. 그 variation을 전체 기간 router로 붙였을 때
   baseline `trend_amount1.5x`보다 실전적으로 개선되는가

#### 20.15.1 새 검증 스크립트

추가한 스크립트:

- `scripts/validate_trend_strength_router.py`

이 스크립트는 두 단계를 한 번에 한다.

1. 서브레짐 inside에서 아래 pattern들의 `1M/2M/3M` gap 비교
   - `trend_base`
   - `trend_amount1.3x`
   - `trend_amount1.5x`
   - `trend_amount2.0x`
   - `trend_retest`
   - `trend_retest_amt1.5x`

2. 전체 기간 `1M` 실행으로 아래 router들 비교
   - `router_broad_base`
   - `router_broad_13`
   - `router_quiet_retest`
   - `router_quiet_retest_amt`
   - `router_narrow_20`
   - `router_panic_20`
   - `router_panic_scale50`
   - `router_broad_base_quiet_retest`
   - `router_broad_base_narrow_20`
   - `router_broad_base_quiet_retest_narrow_20`
   - `router_broad_base_quiet_retest_narrow_20_panic_scale50`
   - `router_broad_13_panic_20`
   - `router_broad_13_narrow_20_panic_20`
   - `router_narrow_20_panic_20`

#### 20.15.2 서브레짐 inside 해석

이번 결과는 꽤 선명했다.

- `quiet`
  - `trend_amount1.5x`가 여전히 최고였다.
  - 대표 score:
    - `trend_amount1.5x`: `0.2430`
    - `trend_amount1.3x`: `0.2222`
    - `trend_retest`: `0.0763`
    - `trend_retest_amt1.5x`: `0.0349`
  - 즉 `quiet -> retest` 가설은 실패에 가깝다.

- `broad`
  - `trend_amount1.5x`와 `trend_amount1.3x`가 비슷했고,
    `1.3x` 완화가 소폭 유리한 구간이 있었다.
  - 대표 score:
    - `trend_amount1.5x`: `0.2797`
    - `trend_amount1.3x`: `0.2724`
    - `trend_base`: `0.2479`
  - 해석:
    `broad`에서는 완전한 base까지 풀기보다는
    `1.5x -> 1.3x` 정도 완화가 가장 자연스럽다.

- `narrow`
  - `trend_amount2.0x`가 가장 좋았다.
  - 대표 score:
    - `trend_amount2.0x`: `0.1055`
    - `trend_amount1.5x`: `0.0866`
    - `trend_base`: `0.0873`
  - 해석:
    `narrow`는 breadth가 좁아 취약할 수 있으므로
    더 강한 거래대금 확인이 낫다.

- `panic`
  - 절대적으로 강한 구간은 아니지만,
    trend variation 중에서는 `trend_amount2.0x`가 제일 나았다.
  - 대표 score:
    - `trend_amount2.0x`: `0.0460`
    - `trend_amount1.5x`: `0.0113`
    - `trend_base`: `-0.0087`
  - 해석:
    현재 `panic` 정의를 hard veto로 쓰기보다,
    `panic inside에서 신규 진입은 더 강하게 필터링`하는 쪽이 맞다.

#### 20.15.3 전체 기간 router 비교 핵심 수치

집중 재실행 기준(`2000-01-01`~`2025-12-31`, `당일종가`, 기본 horizon=`1M`):

- `trend_amount1.5x`
  - `final_wealth 116.3206`
  - `cagr 0.2008`
  - `mdd -0.1559`

- `router_broad_13`
  - `119.4215`
  - `0.2021`
  - `-0.1559`

- `router_panic_20`
  - `117.9777`
  - `0.2015`
  - `-0.1497`

- `router_broad_13_panic_20`
  - `121.1522`
  - `0.2027`
  - `-0.1497`

- `router_broad_13_narrow_20_panic_20`
  - `121.6882`
  - `0.2029`
  - `-0.1518`

- `router_narrow_20_panic_20`
  - `118.4971`
  - `0.2017`
  - `-0.1518`

반면 아래 계열은 분명히 탈락이다.

- `router_quiet_retest`
  - `94.9176`
  - `0.1915`
  - `-0.1618`

- `router_broad_base_quiet_retest`
  - `94.8004`
  - `0.1914`
  - `-0.1622`

- `router_panic_scale50`
  - `88.7647`
  - `0.1884`
  - `-0.1507`

즉 이번 단계 결론은 명확하다.

1. `quiet -> retest`는 버린다.
2. `broad -> amount1.3x`는 약하지만 일관된 개선 후보다.
3. `panic -> amount2.0x`는
   CAGR 소폭 개선 + MDD 개선이 같이 나온다.
4. `narrow -> amount2.0x`도 추가 가치가 약간 있지만,
   개선폭이 아주 작아서 분기 복잡도를 정당화할지는 별도 판단이 필요하다.
5. `panic scale-down(0.5)`은
   기대했던 것과 달리 CAGR 훼손이 더 컸다.

#### 20.15.4 현재 운영 판단

현재 시점에서 가장 실전적인 후보는 아래 둘이다.

1. 단순형:
   - `router_broad_13_panic_20`
   - 의미:
     - `broad`에서는 `amount1.3x`
     - `panic`에서는 `amount2.0x`
     - 나머지는 `trend_amount1.5x`
   - 장점:
     - 개선이 있고
     - 설명이 단순하다
     - drawdown도 baseline보다 줄었다

2. 약간 더 공격적인 형:
   - `router_broad_13_narrow_20_panic_20`
   - 장점:
     - 이번 세트에서 CAGR은 최고
   - 단점:
     - baseline 대비 개선폭이 매우 작고
       branch 하나가 더 늘어난다

현재 추천 우선순위는

1. `router_broad_13_panic_20`
2. `router_broad_13_narrow_20_panic_20`
3. baseline `trend_amount1.5x`

순이다.

#### 20.15.5 외부 자료에서 가져온 해석 방향

이번 아이디어는 로컬 코드만 본 것이 아니라,
아래 취지의 외부 자료 해석도 반영했다.

- trend-following은 long-run에서 broadly 유효하지만
  state dependence가 있다
- panic/rebound 국면은
  momentum/trend가 평소처럼 단순하게 작동하지 않을 수 있다

참고 링크:

- Chicago Booth / AQR 요약:
  `A Century of Evidence on Trend-Following Investing`
- NBER:
  `Momentum Crashes`

다음 에이전트는 이걸 “논문 재현”으로 볼 필요는 없고,
그냥 `broad는 완화`, `panic은 강화`가 왜 자연스러운지에 대한
아이디어 출처 정도로만 보면 된다.

#### 20.15.6 다음 에이전트에게 넘길 후속작업

다음 세션 에이전트는 `CONTEXT.md`를 읽고,
아래 우선순위로 이어서 진행하면 된다.

1. `router_broad_13_panic_20`과
   `router_broad_13_narrow_20_panic_20`
   두 후보만 robustness 검증
   - `1W, 2W, 3W, 1M, 2M, 3M, 6M`
   - subperiod
   - 비용 민감도
   - 최근 구간 일관성

2. 위 두 후보와 baseline의
   turnover / fee / exposure 성격 비교
   - 개선이 너무 미미하면
     복잡도 대비 채택 가치가 있는지 따져야 한다

3. 필요하면 `panic`과 `narrow`의 경계 조정보다 먼저
   현재 router가 “정말 일관되게” baseline을 넘는지 확인
   - 지금 단계에서는 레짐 재정의보다
     robustness 확인이 우선이다

4. `quiet` 쪽은 당분간 보지 않는다.
   - `retest` 계열은 이번 실험에서 충분히 약했다

#### 20.15.7 재현 명령

```bash
conda run -n metricstudio python scripts/validate_trend_strength_router.py
```

간단 검증:

```bash
conda run -n metricstudio python -m py_compile scripts/validate_trend_strength_router.py
```

#### 20.15.8 다음 에이전트가 특히 주의할 점

- `quiet -> retest`를 다시 살리려는 시도는
  우선순위를 낮게 둘 것
- `panic`을 다시 hard veto로 바꾸지 말 것
- 이번 단계의 핵심은
  `trend_amount1.5x`를 버리는 것이 아니라
  서브레짐에서 trend 강도를 약간 조절하는 것
- 최신 수치 인용 시
  반드시 `20.15`의 router 결과와
  이전 `contrarian` 결과를 섞지 말 것
