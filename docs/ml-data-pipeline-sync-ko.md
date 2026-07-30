# 대시보드 데이터 로컬 보정 → ML 재생성 반영 목록 (지속 갱신)

최종 업데이트: 2026-07-21

> **목적**: 프론트/데이터에 **로컬로 추가·수정한 항목**을, Windows `gaon-ml` 파이프라인으로 데이터를
> 다시 산출해도 **그대로 자동 포함**되게 만들기 위한 "파이프라인 수정 지침".
> **데이터 변경이 생길 때마다 이 파일에 계속 추가한다.**

## 배경 / 원칙

- 대시보드 geojson은 Windows conda **`gaon-ml`**(Python 3.11) 파이프라인이 생성한다:
  ```
  build_seoul_dataset.py → analyze_seoul_shap.py → build_seoul_dashboard.py
  → aggregate_dashboard_resolution.py → split_dashboard_by_gu.py
  → robocopy outputs\dashboard  public\dashboard  /E
  ```
- 아래 항목들은 지금 그 **출력 geojson에 로컬 후처리로 주입**돼 있다 → **재생성하면 사라진다.**
- **근본 해결 = 각 파이프라인 스크립트에 아래 수정을 반영**. 반영 전까진 "로컬 재적용"으로 임시 복구.
- 좌표 거리 계산은 haversine(위경도) 기준으로 파이프라인 저장값과 오차 ~1m 이내로 일치함(검증됨).

---

## 1. 100m — 모델 피처 8개 export 누락 → 추가  ✅로컬적용됨

- **문제**: "온도 영향 요인 TOP"(SHAP)이 `slope_deg`·`max_ground_floor_count` 등을 가리키는데
  대시보드 geojson엔 그 컬럼이 없어 프론트가 "현재값"을 "데이터 준비중"으로 표시.
  (값은 `seoul_grid_dataset.csv`·모델엔 실제로 존재. ML 오류 아님.)
- **누락 컬럼(8)**: `slope_deg`, `max_ground_floor_count`, `floor_area_ratio_proxy`, `road_ratio`,
  `zoning_residential_ratio`, `zoning_commercial_ratio`, `zoning_industrial_ratio`, `zoning_green_ratio`
- **파이프라인 수정**: **`build_seoul_dashboard.py`** 출력 컬럼 목록에 위 8개를 추가한다.
  (이미 dataset·model feature이므로 값은 그대로 존재 → 출력만 하면 됨.
  `aggregate_dashboard_resolution.py`/`split_dashboard_by_gu.py`로 250/500m·구별까지 자동 전파.)
- **검증**: 100m geojson 임의 feature의 properties에 위 8개 키가 모두 있는지.
- **로컬 재적용(macOS)**: `seoul_grid_dataset.csv`를 `grid_id`로 join해 8개 컬럼을 각 feature properties에 삽입.

---

## 2. 250/500m — `member_grid_ids`(구성 100m 셀 목록) 추가  ✅로컬적용됨

- **문제**: 250/500m 시뮬레이션은 **구성 100m 셀들에 같은 정책을 적용해 `/api/simulate/batch`로 평균**을
  낸다. 그런데 집계 격자에 "어떤 100m 셀들로 이뤄졌는지" 목록이 없었다(`source_cell_count`만 있음).
- **추가 필드**: `member_grid_ids: string[]` — 그 집계 셀을 구성하는 100m `grid_id` 목록.
  (500m ≈ 25개, 250m ≈ 6~9개)
- **파이프라인 수정**: **`aggregate_dashboard_resolution.py`** — 100m→250/500m 집계 시, 각 집계 셀로
  묶인 **source 100m 셀들의 `grid_id` 리스트**를 `member_grid_ids` 컬럼으로 함께 출력한다.
  (집계 그룹에 이미 source 셀이 있으니 그 `grid_id`들을 모으면 끝. `split_dashboard_by_gu.py`도 이 컬럼 전파.)
- **주의**: 로컬은 "100m 셀 중심점이 집계 격자 bbox 안" 기준으로 근사했고, 구·해안 경계로 **잘린 슬리버
  셀**은 최근접 100m 셀 1개로 폴백했다. 파이프라인은 **실제 집계 그룹핑 기준**을 쓰면 더 정확하다.
- **검증**: 모든 집계 셀의 `member_grid_ids`가 비어있지 않은지. 500m 중앙값 ~25.

---

## 3. 250/500m — 가장 가까운 무더위쉼터 이름·주소·좌표 추가 (중심 기준)  ✅로컬적용됨

- **문제**: 집계 격자엔 쉼터 `nearest_shelter_distance_m`만 있고 `nearest_shelter_name/addr/lon/lat`가
  없어 상세 패널에 "정보 없음"으로 표시됐다.
- **추가 필드**: `nearest_shelter_name`, `nearest_shelter_addr`, `nearest_shelter_lon`, `nearest_shelter_lat`
  (거리 `nearest_shelter_distance_m`는 **블록 중심 기준** 유지 — 100m 규칙과 동일)
- **규칙**: 100m과 동일하게 **격자(집계 셀) 중심 → 가장 가까운 쉼터**를 골라 name·addr·lon·lat·distance를
  일관되게 넣는다. (min 구성셀 기준 아님 — 중심 기준.)
- **파이프라인 수정**: **`collect_grid_vulnerability.py`**(또는 `aggregate_dashboard_resolution.py`)에서
  집계 셀에도 100m과 똑같이 **중심 좌표 → 최근접 쉼터**를 계산해 `name/addr/lon/lat/distance`를 **모두**
  출력한다. (현재는 distance만 출력.) 이미 받아둔 쉼터 CSV 재사용(API 키 불필요).
- **검증**: 250/500m 모든 셀에 `nearest_shelter_name`이 있고, 그 이름과 `distance`가 **같은 쉼터**를 가리키는지.

---

## 로컬 재적용 스크립트 (임시 복구용, macOS)

파이프라인 반영 전, 재생성 후 이 데이터를 다시 주입해야 할 때 사용한 후처리 로직(출력 geojson에 삽입):
- **①** `seoul_grid_dataset.csv` → `grid_id` join → 8개 컬럼 삽입 (100m/*.geojson)
- **②** 100m 셀 중심점 ↔ 집계 bbox 매핑(+슬리버 최근접 폴백) → `member_grid_ids` 삽입 (250/500m)
- **③** 전역 쉼터 좌표 집합에서 집계 셀 **중심 최근접** → 쉼터 name/addr/lon/lat/distance 삽입 (250/500m)

> 세 스크립트 모두 geojson을 **줄단위 문자열 삽입**으로 처리해 geometry·포맷을 보존한다(diff 최소화).
> 필요하면 `scripts/`에 정식 커밋해 재사용 가능하게 만들 수 있음.

---

## 향후 추가 항목 (여기 계속 append)

- (예시) 쉼터 지도 핀: 좌표는 이미 데이터에 있음 → 프론트 확장 시 이 문서에 데이터 요구사항 추가.
- (데이터·컬럼 변경이 생기면: 문제 / 추가 필드 / 파이프라인 수정 스크립트 / 검증 / 로컬 재적용 순으로 기록)
