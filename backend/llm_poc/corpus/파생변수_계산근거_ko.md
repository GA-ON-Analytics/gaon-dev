# GAON 파생·계산 변수 근거와 정확한 식

작성일: 2026-07-19
대상: 팀원 (ML 비전공자 포함)
목적: "수집한 원본이 아니라 **계산해서 만든 데이터**"가 왜 필요했고, 정확히 어떤 식으로
      만들어졌는지 코드 기준으로 정리한다.

---

## 0. 먼저 — 변수는 두 종류다

| 종류 | 뜻 | 예 |
|---|---|---|
| **수집 변수** | 위성·공공API에서 그대로 받은 값 | 지표면온도(LST), 표고, 알베도 |
| **계산 변수(파생)** | 수집값을 가공해 만든 값 | anomaly, 건물비율, 용적률 proxy, 추정인구 |

이 문서는 **계산 변수**만 다룬다. "왜 원본을 그냥 안 쓰고 계산했나"가 핵심이다.

기호 약속: 격자 하나를 $g$, 그 격자 면적을 $A_g$(㎡)로 쓴다.

---

## 1. target / anomaly — 가장 중요한 계산 (모델의 정답)

### 정확한 식
```
target        = lst_daily                                   (그 격자의 그날 지표면온도)
anomaly(g, d) = lst_daily(g, d) − mean_{g' ∈ 같은 구}[ lst_daily(g', d) ]
```
코드: `build_seoul_dataset.py`
```python
seoul_time["lst_daily_anomaly"] = (
    seoul_time["lst_daily"]
    - seoul_time.groupby(["gu_name", "date"])["lst_daily"].transform("mean")
)
```
→ **그 격자의 그날 온도 − 같은 날 같은 구의 평균 온도**

### 왜 원본 온도(LST)를 안 쓰고 이걸 계산했나

원본 LST를 그대로 쓰면 모델이 **"그날 날씨가 더웠다"를 학습**해버린다. 우리가 알고 싶은 건
날씨가 아니라 **"같은 조건에서 이 블록이 왜 옆 블록보다 뜨거운가"** = 도시 구조의 효과다.

같은 날·같은 구의 평균을 빼면:
- 그날의 **기온·습도·구름**이 상쇄된다 (같은 날 같은 지역이니 공통으로 빠짐)
- 구별 **기저 온도차**(강남이 원래 덥다 등)도 상쇄된다
- 남는 건 **"같은 조건에서의 격자 간 차이"** = 순수 도시구조 효과

### 이 정의가 만든 중요한 결과 (팀 공유 필수)
- **기온·습도·대기질은 모델 변수가 될 수 없다.** 같은 날 같은 구에서 모든 격자가 같은 값이라,
  빼고 나면 0이 된다. (그래서 "왜 날씨 변수 없냐"는 질문의 답이 이것이다)
- **구 단위 anomaly는 정의상 0이다.** 구 전체를 평균 내면 편차 합이 0. 그래서 구 단위
  비교에는 `gu_anomaly_vs_seoul`(서울 평균 대비)을 따로 계산한다.

---

## 2. 지표면 피복 — 건물비율 / 녹지율 / 불투수면

### 2-1. building_ratio (건물 비율)
```
building_ratio(g) = (격자 안 건물 바닥면적 합) / A_g          , 0~1로 clip
```
코드: `collect_grid_built_environment_vworld.py`
```python
result["building_ratio"] = (result["building_area_m2"] / result["area_m2"]).clip(0, 1)
```
- 건물 바닥면적은 **건물 폴리곤 ∩ 격자**의 교차 면적 합. 건물이 격자 경계에 걸치면
  걸친 만큼만 센다(중복 방지).

### 2-2. green_ratio / built_surface_ratio / ndvi (위성)
코드: `collect_gu_environment_earth_engine.py` (Google Dynamic World, Sentinel-2)
```python
# NDVI: 근적외(B8)와 적색(B4)의 정규화 차이
ndvi = (B8 − B4) / (B8 + B4)                     # 식생 활력, -1~1

# green_ratio: Dynamic World 6개 식생 클래스 확률의 합
green_ratio = P(trees)+P(grass)+P(flooded_vegetation)+P(crops)+P(shrub_and_scrub)

# built_surface_ratio = impervious_ratio = 건조물 클래스 확률
built = P(built)
```
- **왜 계산인가**: 위성 픽셀마다 "이게 나무일 확률/건물일 확률"을 주는데, 격자 안 픽셀들의
  **평균 확률**을 그 격자의 비율로 삼는다. 원시 위성값을 격자 단위로 요약한 것.

### 2-3. ⚠️ 세 비율의 합은 1이 아니다 (중요, 실제로 버그였음)
`building_ratio + impervious_ratio + green_ratio`는 **1을 넘을 수 있다.**
- 출처가 다르다: 건물비율=건물 도형, 불투수=위성 built 확률, 녹지=위성 식생 확률
- 물리적으로 겹친다: 건물 위로 뻗은 가로수, 옥상정원 등
- **실측: 전체 격자의 24.6%가 이미 합 > 1** (최대 1.79)

→ 시뮬레이션에서 "합을 1로 정규화"하면 사용자가 안 건드린 변수까지 바뀌어 틀린 답이 난다.
   그래서 정규화하지 않고 각 변수를 학습범위로만 clip한다. (자세히: 예측 API 명세 참고)

---

## 3. 용적률 proxy (floor_area_ratio_proxy) — "고층 밀집도"

### 정확한 식
```
건물별  floor_area_proxy_m2 = (건물∩격자 면적) × 지상층수
격자합  floor_area_proxy_m2(g) = Σ_건물 (건물∩격자 면적 × 지상층수)
비율    floor_area_ratio_proxy(g) = floor_area_proxy_m2(g) / A_g
```
코드: `collect_grid_built_environment_vworld.py`
```python
joined["floor_area_proxy_m2"] = joined["intersection_area_m2"] * joined["ground_floor_count"]
# grid별 sum 후
result["floor_area_ratio_proxy"] = (result["floor_area_proxy_m2"] / result["area_m2"])
```

### 왜 계산했나
"용적률(연면적/대지면적)"은 열 관련 핵심 지표인데 **격자 단위 공식 용적률 데이터가 없다.**
대신 건물 데이터에 있는 **바닥면적 × 층수 = 연면적 근사(proxy)**로 만들었다.
- 바닥면적만 보면 단층 대형마트와 고층 아파트가 같아 보인다
- 층수를 곱하면 **"얼마나 높고 빽빽한가"**가 반영된다 → 열 축적·통풍과 직결

### avg_ground_floor_count (평균 층수)도 면적가중이다
```
avg_ground_floor_count(g) = Σ(층수 × 건물∩격자 면적) / Σ(건물∩격자 면적)
```
단순 평균이 아니라 **면적가중 평균**. 큰 건물의 층수가 더 큰 비중을 갖게 하기 위함.

---

## 4. 접근성 — 공원/하천 거리와 면적

### 정확한 식
```
nearest_park_distance_m(g) = 격자 중심점에서 가장 가까운 공원까지 거리(m)   (내부면 0)
park_area_within_500m(g)   = 격자 중심 반경 500m 원 안에 들어온 공원 면적 합(㎡)
```
코드: `collect_grid_accessibility_vworld.py`
```python
buffers["geometry"] = centroids.buffer(500)              # 중심에서 500m 원
area = buffer.intersection(공원들의 합집합).area          # 원 ∩ 공원 면적
```

### 왜 계산했나
"공원이 있다/없다"의 0·1보다, **얼마나 가깝고 얼마나 넓은 냉각원이 근처에 있나**가 온도에
직접 작용한다. 큰 공원은 주변 수백 m를 식히는 냉섬(cool island) 효과가 있어서 **거리 + 면적**
두 관점을 모두 만들었다.

---

## 5. 추정 인구 — dasymetric(면적 안분이 아닌 연면적 안분)

### 정확한 식
```
주거연면적(g) = floor_area_proxy_m2(g) × zoning_residential_ratio(g)
share(g)      = 주거연면적(g) / Σ_{같은 구} 주거연면적
est_population(g)     = share(g) × (그 구 총인구)
est_elderly(g)        = est_population(g) × (그 구 고령비율)     ※ 동 단위 있으면 동 비율
est_population_density = est_population(g) / (A_g / 1e6)          (명/㎢)
```
코드: `collect_grid_vulnerability.py`
```python
s["_res_floor"] = (floor_area_proxy_m2 * zoning_residential_ratio).clip(lower=0)
tot = s.groupby("gu_code")["_res_floor"].transform("sum")
s["_share"] = s["_res_floor"] / tot
s["est_population"] = s["_share"] * s["population"]
```

### 왜 계산했나 (그냥 면적으로 나누면 안 되나?)
공식 인구 통계는 **구/동 단위**까지만 있다. 격자 단위로 쪼개야 하는데:
- **면적 안분**(면적 비율로 나눔)은 틀리다 — 같은 넓이라도 산은 0명, 아파트는 수천 명이다
- **dasymetric = 사람이 실제 사는 곳(주거 연면적)에 비례 배분** → 훨씬 현실적

즉 "구 총인구를, 각 격자의 **주거용 건물 연면적 비중**만큼 나눠 갖는다"는 논리다.

### 알아둘 한계 (정직하게)
- 구 총인구는 상수라, **구 내부에서 est_population 순위 = 주거연면적 순위**다(새 정보가 아님).
- 고령비율이 구 상수면 **est_elderly 순위 = est_population 순위**다. 그래서 취약성에서
  "쉼터 거리"만이 건물과 독립인 새 신호다. (동 단위 고령비율을 쓰면 이 한계가 완화됨)

---

## 6. 파생 proxy 4종 — 상호작용 항

코드: `build_dongdaemun_grid_dataset.py`의 `add_derived_features`
```python
building_height_proxy_m   = avg_ground_floor_count × 3.0            # 층당 3m 가정한 높이
building_shadow_proxy      = building_ratio × avg_ground_floor_count # 그림자/캐니언 부피감
hardscape_exposure_proxy   = impervious_ratio × (1 − green_ratio)    # 그늘 없는 포장면 노출
built_heat_proxy           = built_surface_ratio × (1 − ndvi)       # 식생 없는 시가화 강도
```

### 왜 계산했나
두 변수의 **곱(상호작용)**이 단독보다 물리적으로 의미 있는 경우가 있다.
- `hardscape_exposure_proxy`: 불투수면이 많아도 나무 그늘이 있으면 덜 뜨겁다.
  → "불투수면이면서 **동시에** 녹지가 없는" 정도를 하나의 값으로. $\text{impervious}\times(1-\text{green})$
- `built_heat_proxy`: 시가화 면적이 식생으로 덮이지 않은 정도. $\text{built}\times(1-\text{NDVI})$
- 트리 모델도 상호작용을 어느정도 잡지만, 명시적으로 넣으면 해석과 안정성이 좋아진다.

> 주의: 이들은 서로·원변수와 상관이 높다. 그래서 변수 중요도를 **SHAP**으로 본다(공선성 보정).

---

## 7. 녹지확대 시나리오 저감효과 (green_delta_c)

### 정확한 식
```
green_delta_c(g) = model.predict( 녹지확대된 g ) − model.predict( 현재 g )
```
녹지확대 = { green_ratio +0.05, ndvi +0.03, impervious_ratio −0.05, built_surface_ratio −0.05 }

코드: `build_seoul_dashboard.py`
```python
GREEN_DELTAS = {"green_ratio": 0.05, "ndvi": 0.03,
                "impervious_ratio": -0.05, "built_surface_ratio": -0.05}
df["green_delta_c"] = (model.predict(Xs) - df["pred_anomaly"])
```

### 왜 재예측인가 (가중치 곱셈 안 되나)
모델이 RandomForest(비선형)라 **"녹지 21.8% 중요 × 변화량" 같은 곱셈 공식이 없다.**
바꾼 값을 **모델에 다시 넣어 예측**해야 한다. 음수면 냉각(저감)이다.

---

## 8. 개선 우선순위 (priority_score) — 이건 "모델"이 아니라 "정책 판단"

### 정확한 식 (구 내부 백분위 순위의 가중합)
```
score(g) = 0.30·rank(anomaly)          열 위험 (지금 얼마나 뜨거운가)
         + 0.20·rank(−green_delta_c)    냉각 여지 (녹지로 얼마나 내려가나)
         + 0.25·rank(est_elderly)       취약성 (고령 인구)
         + 0.15·rank(shelter_distance)  정책 갭 (쉼터가 먼가)
         + 0.05·rank(1 − green_ratio)   저녹지
         + 0.05·rank(impervious×(1−green)) 포장면 노출
```
`rank(·)`는 **그 구 안에서의 백분위(0~100)**. 코드: `build_seoul_dashboard.py`

### 왜 이렇게 계산했나
- **가중치(0.30 등)는 기술이 아니라 정책 판단**이다 → 상수로 분리해 팀이 조정 가능하게 했다.
- 세 축으로 나눈 이유: **산속 뜨거운 땅**과 **고령자 사는 뜨거운 주거지**를 같은 순위로
  두면 안 되기 때문. 물리(열·냉각) × 사람(취약) × 정책갭(쉼터)을 곱이 아닌 합으로 섞는다.
- 순위(rank)를 쓰는 이유: 단위가 다른 지표(℃·명·m)를 그대로 더할 수 없어, 각자를 구 내부
  **백분위**로 바꿔 공정하게 합산한다.

---

## 9. 해석용 계산 — SHAP 기여도 (top1~3_feature/shap)

각 격자의 예측을 변수별 기여로 분해한 값. `top1_feature`(가장 크게 기여한 변수) + `top1_shap`
(그 기여도, ℃). SHAP은 "이 격자가 왜 이 온도로 예측됐는지"를 **더하면 예측값이 되는** 방식으로
분해한다. 단순 변수 중요도와 달리 **격자마다 다르게**, 공선성에 덜 휘둘리며 계산된다.

---

## 부록 — 한눈에 보는 계산 변수 표

| 변수 | 한 줄 식 | 왜 계산 |
|---|---|---|
| anomaly | LST − 같은날·같은구 평균 | 날씨·구 기저온도 제거, 순수 구조효과 |
| building_ratio | 건물∩격자 면적 / 격자면적 | 격자 단위 밀도화 |
| green/built_ratio | 위성 클래스 확률의 격자 평균 | 픽셀→격자 요약 |
| floor_area_ratio_proxy | Σ(건물면적×층수) / 격자면적 | 공식 용적률 부재 → 연면적 근사 |
| avg_ground_floor_count | 면적가중 평균 층수 | 큰 건물에 가중 |
| nearest_park_distance_m | 중심→최근접 공원 거리 | 냉각원 근접성 |
| park_area_within_500m | 반경 500m 내 공원 면적 | 냉각원 규모 |
| est_population | 주거연면적 비중 × 구 인구 | 면적 아닌 거주 실태 배분 |
| est_elderly | est_population × 고령비율 | 취약 인구 추정 |
| hardscape_exposure_proxy | impervious × (1−green) | 그늘 없는 포장면 |
| built_heat_proxy | built × (1−ndvi) | 식생 없는 시가화 |
| green_delta_c | 재예측(녹지확대) − 현재예측 | 비선형이라 곱셈 불가 |
| priority_score | 구내 백분위 6개 가중합 | 정책 판단(팀 조정) |
| SHAP top1~3 | 예측을 변수 기여로 분해 | 격자별 원인 설명 |
