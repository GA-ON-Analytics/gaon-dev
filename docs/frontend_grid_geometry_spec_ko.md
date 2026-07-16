# 프론트/백 전달: 서울 격자 대시보드 명세 (4개 해상도)

작성일: 2026-07-11 (갱신)
대상: 프론트엔드/백엔드 개발자
목적: 확정된 격자 지오메트리 + 분석값이 담긴 대시보드 GeoJSON 전달.

---

## ⭐ 0. 최종 전달 파일 (분석값 포함, 바로 사용 가능)

```text
outputs/dashboard/
  100m/{구코드}_{구이름}.geojson   × 25   총 64,574셀 / 79MB   ← 상세 (블록 단위)
  250m/{구코드}_{구이름}.geojson   × 25   총 11,307셀 / 14MB   ← 동네 단위
  seoul_grid_500m.geojson          1개    3,225셀 /  5MB       ← 광역 개요
  seoul_gu_level.geojson           1개       25폴리곤 / 1.2MB  ← 구 비교
  manifest.csv                              파일 목록/셀 수
```

- **100m·250m은 구별 파일**이다. 대시보드는 한 번에 한 구만 보여주므로 선택한 구의 파일만 fetch한다.
  (서울 전체 100m는 76MB라 한 번에 받으면 안 된다.)
- **500m·구 단위는 통짜 파일**이다. 작아서 한 번에 받아도 된다.
- **4개 해상도 모두 속성 스키마가 동일**하다(구 단위만 일부 다름, 4장 참고).
  → 해상도 토글은 **파일만 갈아끼우면** 된다. 렌더링 코드는 하나.

### 해상도는 어떻게 만들었나 (알아두면 좋음)
```text
100m만 실제 학습했다. 250m/500m/구 단위는 100m 결과를 면적가중 집계한 것이다.
- 위성 온도(Landsat) 원본 해상도가 100m라, 거친 격자를 새로 학습해도 새 정보가 없다.
- anomaly는 "면적당 온도 편차"라서 면적가중 평균이 정확한 값이다(근사 아님).
- 검증: 500m 집계 후 전체 면적가중 평균이 100m와 일치(차이 1e-5), 총면적 605.27km² = 서울 실제 면적.
```

---

## 1. 핵심 원칙 (반드시 이해)

```text
격자(폴리곤)는 우리가 만들어서 GeoJSON으로 준다.
개발자는 그 폴리곤을 "그대로" 그린다. 격자를 직접 계산하지 않는다.
```

- 지도에서 100m 네모를 프론트가 생성하지 말 것 (라이브러리 그리드 오버레이, bbox 분할 금지)
- 우리가 준 `geometry` 좌표를 그대로 렌더 → 분석 격자와 화면 격자가 **수학적으로 100% 일치**
- 나중에 값이 바뀌어도 프론트 코드는 그대로. **새 GeoJSON 파일만 갈아끼우면** 색이 갱신됨

---

## 2. 지금 전달하는 것: 지오메트리 (확정, 안 바뀜)

파일 위치:
```text
data/raw/grid/seoul/{gu_code}_{gu_name}_grid_100m.geojson   (구별 25개 파일)
data/raw/grid/seoul/seoul_grid_100m_manifest.csv            (구별 격자 수 요약)
```

- 전체 서울 25개 구, 100m 격자 **총 64,676개 셀**
- 좌표계: **EPSG:4326** (경위도, WGS84). Leaflet/Mapbox/OpenLayers 바로 사용
- 형식: GeoJSON FeatureCollection, `geometry.type = Polygon`

각 구 파일 크기는 0.5~5MB. 대시보드는 **선택한 구의 파일 하나만** 로드하면 된다
(전체 25개를 한 번에 로드할 필요 없음).

### 지오메트리 파일의 properties (지금 들어있는 것)
| 필드 | 예시 | 뜻 |
|---|---|---|
| `grid_id` | `11230_00001` | 격자 고유 ID = `{구코드}_{5자리번호}`. **모든 데이터의 primary key** |
| `gu_code` | `11230` | 자치구 코드 |
| `gu_name` | `동대문구` | 자치구 이름 |
| `grid_size_m` | `100` | 격자 한 변 길이(m) |
| `area_m2` | `10000.0` | 셀 실제 면적(㎡). 경계 자투리 셀은 10000 미만 |

### grid_id 규칙 (개발 시 기준)
```text
형식: {gu_code}_{index:05d}   예) 11230_00001, 11680_04170
- gu_code는 행정표준 자치구 코드 (11110~11740)
- index는 구 내부 순번 (1부터)
- 전 서울에서 유일. 정렬 가능. 언어 중립.
```

---

## 3. 속성 스키마 (100m / 250m / 500m 공통)

| 필드 | 용도 | 프론트 활용 |
|---|---|---|
| `grid_id` (100m) / `display_grid_id` (250m·500m) | 셀 고유 ID | key, 클릭 식별 |
| `gu_code`, `gu_name` | 자치구 | 필터 |
| `priority_score` | 개선 우선순위 0~100 (구 내부, **취약성 반영**) | **우선순위 지도 색상** |
| `priority_rank` | 구 내부 순위 | TOP-N 목록 |
| `mean_actual_anomaly` | 여름철 열 위험 (**구** 평균 대비 ℃) | 열지도 색상 (구 내부 비교) |
| `seoul_anomaly` | 여름철 열 위험 (**서울** 평균 대비 ℃) | 열지도 색상 (구 간 비교) |
| `est_population` | 추정 생활인구 (명) | 취약성 패널 |
| `est_elderly` | 추정 고령인구 65세+ (명, 행정동 고령비율 반영) | **취약성 패널** |
| `dong_elderly_ratio` | 행정동 고령비율 (6~36%, 동마다 다름) | 취약성 패널 |
| `dong_avg_age` | 행정동 평균연령 (세) | 취약성 패널 |
| `est_population_density` | 인구밀도 (명/㎢) | 취약성 패널 |
| `nearest_shelter_distance_m` | 최근접 무더위쉼터 거리 (m) | **정책갭 표시** |
| `shelter_count_within_500m` | 반경 500m 내 쉼터 수 | 상세 패널 |
| `shelter_capacity_within_500m` | 반경 500m 내 쉼터 수용인원 합 | 상세 패널 |
| `mean_actual_lst` | 여름철 실제 지표면온도 (℃) | 상세 패널 |
| `pred_anomaly` | 모델 예측 anomaly | 상세 패널 |
| `pred_anomaly_std` | 예측 불확실성 (클수록 덜 확신) | 신뢰도 표시 |
| `green_delta_c` | 녹지확대 시나리오 저감효과 (음수=냉각) | **시나리오 지도** |
| `building_form_group` | 5분류 (아래) | **필터** |
| `top1_feature` / `top1_shap` | **이 셀이 뜨거운/시원한 1위 이유** | **클릭 팝업** ⭐ |
| `top2_feature` / `top2_shap` | 2위 이유 | 클릭 팝업 |
| `top3_feature` / `top3_shap` | 3위 이유 | 클릭 팝업 |
| `building_ratio`, `green_ratio`, `ndvi`, `impervious_ratio`, `built_surface_ratio` | 환경 특성 | 상세 패널 |
| `avg_ground_floor_count`, `elevation_m`, `albedo` | 층수·표고·반사율 | 상세 패널 |
| `nearest_park_distance_m`, `park_area_within_500m`, `nearest_stream_distance_m` | 공원·하천 접근성 | 상세 패널 |
| `area_m2` | 셀 실제 면적 | — |
| `source_cell_count` (250m·500m만) | 집계에 쓰인 100m 셀 수 | — |

### `building_form_group` 값 (5종)
```text
no_building              건물 거의 없음 (산·공원·하천) — 서울의 40%
low_floor_high_density   저층 고밀  ← 가장 뜨거움
high_floor_high_density  고층 고밀
high_floor_low_density   고층 저밀
low_floor_low_density    저층 저밀  ← 가장 시원
```

### `priority_score` 공식 (우선순위 지도가 무엇을 뜻하나)
```text
priority_score = 열위험 30% + 냉각여지 20%     ← 물리 (모델·시나리오)
               + 취약성(고령인구) 25%          ← 사람
               + 쉼터갭(쉼터 거리) 15%         ← 정책 갭
               + 녹지부족 5% + 포장면 5%       ← 환경
구 내부에서 0~100 백분위로 산출. 가중치는 정책 판단이라 조정될 수 있음.
```
**핵심**: 프로젝트 목표는 **도시 열섬 저감**이다. 우선순위는 "열섬을 낮추는 게 목표인데
예산이 한정되니 **어디부터** 낮출까"에 답하는 실행 지표다.
그래서 저감 효과(열위험·냉각여지)뿐 아니라 **사람이 사는 곳인지**도 반영한다 —
아무도 없는 산속 뜨거운 땅보다 사람 사는 뜨거운 블록을 먼저 식히는 게 같은 예산으로 효과가 크다.
(취약성은 순서를 정하는 보조 축이지 프로젝트의 목표가 아니다.)

### `top1_feature` 값 예시 (SHAP 기여 1위 변수)
`built_surface_ratio`(시가화면) / `impervious_ratio`(불투수면) / `green_ratio`(녹지율) /
`ndvi`(식생) / `building_ratio`(건물비율) / `elevation_m`(표고) 등

`topN_shap`의 **부호**가 방향이다: **양수 = 온도를 올림, 음수 = 내림.**
팝업 예시: *"이 블록이 뜨거운 이유 ① 시가화면 +0.82°C ② 녹지 부족 +0.41°C ③ 표고 낮음 +0.12°C"*

---

## 3.5 구 단위 파일은 스키마가 다르다 (중요)

`seoul_gu_level.geojson`에는 **`mean_actual_anomaly`가 없다.**

```text
이유: anomaly = (그 격자) − (같은 날 그 구의 평균).
      구 전체로 평균내면 정의상 0이 된다. 구 비교에 쓸 수 없다.
```

대신 구 비교용 필드를 쓴다.

| 필드 | 뜻 |
|---|---|
| `gu_anomaly_vs_seoul` | **그 구 평균 LST − 서울 전체 평균 LST** (℃). 구 비교는 이걸로 |
| `gu_heat_rank` | 서울 25개 구 중 더운 순위 (1=가장 더움) |
| `within_gu_anomaly_std` | 구 내부 온도 편차 (열섬 불균등도) |

**구 단위 열섬 순위 TOP5**: 동대문 +2.35℃ · 동작 +1.65 · 양천 +1.35 · 구로 +1.25 · 금천 +0.99
**가장 시원**: 서초 -2.97 · 도봉 -2.59 · 강북 -2.57

```text
주의: 값들은 모델 재학습 시 갱신된다(프론트 재작업 불필요, 파일만 교체).
      필드 목록(스키마)은 이 문서 기준으로 고정한다.
```

---

## 4. 렌더링 예시 (Leaflet) — 해상도 토글 포함

```javascript
// 해상도 토글: 파일 경로만 바뀐다. 렌더링 코드는 하나.
function pathFor(resolution, guCode, guName) {
  if (resolution === 'gu')   return '/dashboard/seoul_gu_level.geojson';   // 25개 구
  if (resolution === '500m') return '/dashboard/seoul_grid_500m.geojson';  // 서울 전체
  return `/dashboard/${resolution}/${guCode}_${guName}.geojson`;           // 100m, 250m는 구별
}

function load(resolution, guCode, guName) {
  fetch(pathFor(resolution, guCode, guName))
    .then(r => r.json())
    .then(geo => {
      if (layer) map.removeLayer(layer);
      layer = L.geoJSON(geo, {
        style: f => ({
          color: '#555', weight: 0.3, fillOpacity: 0.7,
          fillColor: colorByScore(f.properties.priority_score),
        }),
        onEachFeature: (f, lyr) => lyr.bindPopup(reasonPopup(f.properties)),
      }).addTo(map);
    });
}

// 클릭 팝업: "이 블록이 뜨거운 이유 TOP3" (SHAP)
function reasonPopup(p) {
  const rows = [1, 2, 3]
    .filter(i => p[`top${i}_feature`])
    .map(i => {
      const v = p[`top${i}_shap`];
      const dir = v > 0 ? '↑' : '↓';
      return `${i}. ${LABEL[p[`top${i}_feature`]]} ${dir} ${Math.abs(v).toFixed(2)}°C`;
    });
  return `<b>여름철 열섬 위험도 ${p.mean_actual_anomaly.toFixed(2)}°C</b><br>${rows.join('<br>')}`;
}

// 구 단위는 anomaly가 없다 → gu_anomaly_vs_seoul 을 쓸 것
```

**값이 갱신되면?** GeoJSON 파일만 교체한다. **프론트 코드는 그대로.**

---

## 5. 무엇이 고정이고 무엇이 바뀌나 (요약)

| 항목 | 상태 | 설명 |
|---|---|---|
| 격자 좌표 (geometry) | ✅ 확정 | 100m 전역 격자. 다시 안 바뀜 |
| grid_id 체계 | ✅ 확정 | `{gu_code}_{5자리}` |
| 필드 목록 (스키마) | ✅ 확정 | 3장 표 기준 |
| 각 필드의 값 | 🔄 갱신 | 모델 개선 시 바뀜. 파일만 교체하면 됨 |

---

## 6. 재생성 방법 (참고, 개발자는 몰라도 됨)

```bash
python src/create_seoul_grid.py                 # 전체 25개 구
python src/create_seoul_grid.py --only-gu 동대문구  # 특정 구만
```

전역 격자 원점을 EPSG:5179의 100m 배수에 스냅하므로, 경계가 미세하게 바뀌어도
셀 위치가 안정적이고 결과가 재현된다.
