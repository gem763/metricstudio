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
