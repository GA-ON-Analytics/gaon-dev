# 격자 상세 패널 — 진행 정리 (Phase 1 완료)

최종 업데이트: 2026-07-21

> **목적**: 다른 세션에서 이어서 작업하거나, 팀원에게 진행 내용을 설명하기 위한 요약.
> 세부 시작 가이드는 [`detail-panel-handoff.md`](./detail-panel-handoff.md), 건물 보정 근거는 백엔드 `GAON/docs/건물비율_위성보정_ko.md` 참고.

---

## 0. 한눈 요약

지도에서 100m/250m/500m 격자를 클릭하면 뜨는 **우측 상세 패널**(`src/components/GridDetailSidePanel.tsx`)을 목업대로 구현 완료. 부수적으로 **건물비율 위성 보정**과 **가장 가까운 무더위쉼터 이름/주소**를 백엔드 데이터까지 반영.

- **프론트 브랜치**: `feat/#5-grid-detail-panel-impl`
- **백엔드 브랜치**: `main` (GAON 레포)
- **상태**: Phase 1(정적 표시) ✅ · Phase 2(시뮬레이션) ⏳ · Phase 3(격자 비교) ⏳

---

## 1. Phase 1 — 상세 패널 구성 (완료)

위→아래 카드 5개. 모든 CSS는 `.gridDetailSidePanel` 스코프 + `--gdp-` 변수(앱 전역과 충돌 방지).

| 카드 | 내용 | 쓰는 필드 |
|---|---|---|
| **히어로** | 구·동명, id·면적, 지표면온도(큰 숫자), 열위험 태그, 온도 막대, 우선순위 칩 | `gu_name`,`dong_name`,`display_grid_id`,`area_m2`,`mean_actual_lst`,`mean_actual_anomaly`,`priority_rank` |
| **핵심 타일 2개** | 녹지화 시 저감(ⓘ 시나리오 설명), 추정 거주 인구 | `green_delta_c`,`est_population` |
| **온도 영향 요인 TOP3** | SHAP 다이버징 막대(양수=주황 오른쪽, 음수=파랑 왼쪽) + 실제값 병기 | `top1~3_feature`,`top1~3_shap` |
| **환경 프로필** | 도넛 3개(녹지율·불투수면·건물) | `green_ratio`,`impervious_ratio`,`building_ratio` |
| **취약성** | 추정 인구·고령인구(비율)·무더위쉼터(이름·거리, 주소는 ⓘ) | `est_population`,`est_elderly`,`nearest_shelter_*` |

### 핵심 설계
- **온도 톤(색) = 지도 anomaly 색과 동일 경계**: `<0` 파랑(비교적 시원) · `0~1.5` 회색(보통) · `1.5~3` 주황(다소 더움) · `≥3` 빨강(열 위험 높음). (`heatLevel()`)
- **우선순위**: 구 내부 순위(`priority_rank`) + 구 전체 격자 수(`guGridTotal`, 클릭 시 계산)로 **백분위/순위** 표시. **원점수 `priority_score`는 표시 안 함**(사용자 결정).
- **SHAP**: 피처 한글 라벨(`FEATURE_LABELS`) + 막대 길이는 최대 기여도 대비 46%로 정규화.
- **도넛**: SVG `pathLength=100` + `stroke-dasharray="퍼센트 100"` 트릭.

### 지도 상호작용 (`MapDashboard.tsx`, `CanvasGridLayer.tsx`)
- **호버 주황 테두리**: 지도선택 모드에서만(이동 모드 X). 100m=캔버스 `L.rectangle`, 250/500m=GeoJSON `setStyle`(선택 격자의 검은 테두리는 보존).
- **250/500m 순위 분모**: 클릭 시 같은 구의 격자 수를 세어 `guGridTotal`로 넘김.

### 커스텀 툴팁 `InfoTip`
- ⓘ 아이콘 + CSS 말풍선(JS 없이 `:hover`/`:focus-visible`). `align`(left/center/right)로 패널 밖 안 나가게. `white-space:pre-line`로 줄바꿈 지원.
- 지표 설명은 `METRIC_DESC` 맵 한 곳에서 관리(불투수면·NDVI·알베도·평균층수 등).

---

## 2. 건물비율 위성 보정 (백엔드 + 프론트)

**문제**: VWorld 건물 도형 누락(특히 **용산 미군기지** 등 공공·군사시설)으로 `building_ratio=0`인데 위성 지표면(`built_surface_ratio`)은 건물이 있다고 함. 서울 전체 ~3.1%, 용산은 10.3%.

**해결**: `build_seoul_dataset.py`의 `correct_building_ratio()` — `building_ratio ≤ 0.02 & built_surface_ratio ≥ 0.15`면 `built_surface × 0.56`(실데이터 중앙값)로 추정하고 `building_ratio_estimated=True` 플래그. **파생변수 계산 전**에 적용 → 데이터셋·모델재학습·SHAP·대시보드·250/500m 전부 같은 값.

- 근거·수치: `GAON/docs/건물비율_위성보정_ko.md`
- **프론트 반영**: 플래그가 true면 "건물 (추정)" + 회색 callout 안내. 특수지역은 인구·고령인구도 **"집계 제외"**(추정 불가).
- **경계 기준(왜 0.15/0.56)**: 0.15 미만은 공원·녹지일 수 있어 가짜 건물 방지, 0.56은 실데이터의 건물/위성지표면 비율 중앙값.

---

## 3. 무더위쉼터 이름/주소 (백엔드 + 프론트)

- `collect_grid_vulnerability.py`: 원래 버리던 **가장 가까운 쉼터 인덱스**를 살려 `nearest_shelter_name`(R_AREA_NM)·`nearest_shelter_addr`(도로명)·`nearest_shelter_lon/lat` 산출. 이미 받아둔 쉼터 CSV 재사용(API 키 불필요).
- `build_seoul_dashboard.py`: `VUL_COLS`에 4개 컬럼 추가 → 출력·구별 분할 파일까지 전파.
- **프론트**: 취약성 카드에 **이름·거리 한 줄**, 주소는 **ⓘ 툴팁**. 좌표(lat/lon)도 데이터에 있어 향후 **지도에 쉼터 핀** 확장 가능.

---

## 4. 데이터 파이프라인 & 재생성 (중요)

- **실행 환경**: conda **`gaon-ml`**(Python 3.11) — `C:\Users\ww\miniforge3\envs\gaon-ml\python.exe`. (전역 3.14엔 geopandas 없음)
- **프론트 데이터 소스**: `/api/dashboard/...`(백엔드 API) 실패 시 정적 `GA_ON_app/public/dashboard/...` 폴백. 실제로는 **정적 파일**을 씀.
  - 100m 상세: `public/dashboard/100m/{gu_code}_{구}.geojson` (패널이 클릭 시 fetch)
  - 지도 개요(줌아웃 색칠): `public/dashboard/seoul_grid_100m_map.geojson` — **⚠️ 파이프라인이 생성 안 함**(별도 파일). 개요 지도 색은 옛 데이터일 수 있음.

### 재생성 순서
**건물 보정까지 전체**(모델·SHAP 갱신):
```
build_seoul_dataset.py → analyze_seoul_shap.py → build_seoul_dashboard.py
→ aggregate_dashboard_resolution.py → split_dashboard_by_gu.py
→ robocopy outputs\dashboard  public\dashboard  /E
```
**쉼터/취약성만**(모델·SHAP 불필요):
```
collect_grid_vulnerability.py → build_seoul_dashboard.py
→ aggregate_dashboard_resolution.py → split_dashboard_by_gu.py → robocopy ... /E
```

### 재생성 시 주의 (실제로 겪은 함정)
1. **robocopy에 `/E` 필수** — 없으면 하위 폴더(`100m/`, `250m/`)가 복사 안 돼서 패널이 옛 값을 봄.
2. **브라우저 캐시** — 패널은 클릭 시 `fetch()`로 geojson을 받는데 캐시가 남아 옛 값이 보임. **DevTools Network의 "Disable cache" 켜고 리로드**하면 확실. (일반 F5·Ctrl+Shift+R로 안 될 때 있음)
3. 데이터 커밋이 매번 무겁게(15만 줄) 뜨는 건 LF↔CRLF 정규화 탓. `.gitattributes`에 `public/dashboard/**/*.geojson -text` 넣으면 정리됨(미적용).

---

## 5. 주요 파일

| 파일 | 역할 |
|---|---|
| `GA_ON_app/src/components/GridDetailSidePanel.tsx` | **상세 패널 본체** (모든 카드·InfoTip·Donut) |
| `GA_ON_app/src/components/MapDashboard.tsx` | 격자 클릭/호버, `guGridTotal`, `formatAnyProperty`, 패널 렌더 |
| `GA_ON_app/src/components/CanvasGridLayer.tsx` | 100m 캔버스 렌더 + 호버 테두리 |
| `GA_ON_app/src/types/dashboard.ts` | `GridAnalysisProperties`(building_ratio_estimated, nearest_shelter_* 등) |
| `GA_ON_app/src/services/api.ts` | 데이터 fetch, `simulateGridPolicy`(Phase 2용) |
| `GAON/src/build_seoul_dataset.py` | `correct_building_ratio()` — 건물 보정 진원지 |
| `GAON/src/collect_grid_vulnerability.py` | 쉼터·인구 산출(쉼터 이름/주소 추가됨) |
| `GAON/src/build_seoul_dashboard.py` | 대시보드 최종 산출(출력 컬럼) |
| `GAON/docs/건물비율_위성보정_ko.md` | 건물 보정 근거·수치 |

---

## 6. 커밋 이력

**프론트 `feat/#5-grid-detail-panel-impl`** (아래→위 순):
- `54c3227` 히어로(지도기준 온도톤·순위/백분위·건물 추정표시)
- `5081f07` 호버 주황 테두리 + 250/500m 순위 분모
- `1efd4ee` 타일·SHAP 막대·지표 툴팁(ⓘ)·특수지역 인구 안내
- `34abc69` 환경 도넛 3개 + 툴팁 위치 보정
- `8d49f12` 취약성 카드·쉼터 이름/주소·안내 카드화 (Phase 1 마무리)
- `df7b704` 쉼터 표시 정리(이름·거리 한 줄, 주소는 툴팁)
- `c39e566` (데이터) 대시보드 재생성(건물 보정 + 쉼터)

**백엔드 `main`**:
- `3c1f42f` building_ratio 위성 보정을 공용 데이터셋으로(SHAP·모델까지 일치) + 근거 문서
- `c251d70` 가장 가까운 무더위쉼터 이름·주소·좌표 산출 + 대시보드 출력

---

## 7. 남은 작업 / 다음 단계

- **Phase 2 — 직접 시뮬레이션**: 슬라이더(녹지·불투수·NDVI·알베도·공원면적) `useState` → "시뮬레이션 적용" 시 `api.ts`의 `simulateGridPolicy(payload)` 호출 → 응답 `delta_c` 표시. **백엔드 API 서버 실행 + `/api/dashboard/simulate` 엔드포인트 확인 필요.**
- **Phase 3 — 격자 비교**: 두 번째 격자 선택 → 가장 큰 차이 콜아웃 + 지표 나란히.
- **미해결/개선거리**:
  - 지도 개요 파일 `seoul_grid_100m_map.geojson` 재생성 경로 불명 → 개요 색칠은 옛 데이터. 생성 스크립트 찾거나 만들 것.
  - `.gitattributes`로 데이터 diff 정리.
  - 쉼터 지도 핀(좌표는 이미 데이터에 있음).
