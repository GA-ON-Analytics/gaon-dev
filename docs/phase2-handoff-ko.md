# GA:ON 인수인계 (단일 문서 — 계속 여기에만 갱신)

최종 업데이트: 2026-07-21

> **이 파일이 유일한 인수인계 문서다.** 새 handoff 파일을 만들지 말고 여기에 계속 갱신한다.
> - 데이터→ML 재생성 반영 지침은 **별도**: [`ml-data-pipeline-sync-ko.md`](./ml-data-pipeline-sync-ko.md)
> - 목표 디자인: [`detail-panel-mockup.html`](./detail-panel-mockup.html)
> - Phase 1 상세 기록(건물 보정 근거 등): [`detail-panel-progress.md`](./detail-panel-progress.md)
> - 100m 최적화·선택 로직 상세: [`map_100m_optimization_worklog_ko.md`](./map_100m_optimization_worklog_ko.md)

> **작업 방식**: 사용자는 프론트 초보 → 코드는 직접 고치고 "무엇/왜/바꾸는 법"으로 설명, 변경마다
> `npx tsc --noEmit`. **커밋/push는 사용자가 직접**(요청 없이 금지, Claude 공동저자 트레일러 금지). 한글로.

---

## 0. 한눈 요약

- **Phase 1(정적 상세 패널)** ✅ · **Phase 2(직접 시뮬레이션)** ✅ · 패널 재구성·데이터 보정 ✅ · **Phase 3(격자 비교)** ⏳
- 상세 패널(오른쪽)에 **목업 슬라이더 5개**(녹지·불투수·NDVI·알베도·공원면적) → "시뮬레이션 적용" →
  모델 재예측 → `delta_c`. **100m·250m·500m 전부 동작.**
- 왼쪽 대시보드 = **지도/지표 선택 전용**. 죽은 코드 정리까지 완료.

---

## 1. 로컬 실행 (macOS, 백+프론트)

> ⚠️ 문서 곳곳의 `C:\...gaon-ml`는 **Windows 테스트 머신**용. 이 개발 머신은 macOS.
> 모델은 **scikit-learn 1.9.0**(Python 3.10+ 필요). 시스템 python3=3.9라 부족 → brew python@3.12로 `.venv`.

```bash
# 최초 1회
/opt/homebrew/opt/python@3.12/bin/python3.12 -m venv .venv
.venv/bin/python -m pip install -r backend/requirements.txt   # sklearn==1.9.0, numpy<2.5, pandas<3 고정
npm ci
# 실행 (터미널 2개)
.venv/bin/python -m uvicorn backend.main:app --reload    # 백엔드 8000
npm run dev                                              # 프론트 5173 (vite가 /api → 8000 프록시)
```
헬스: `curl localhost:8000/api/model/status` → `ready:true`.

---

## 2. 백엔드 / API 핵심 (문서와 다른 실제)

- 서버 = **`backend/main.py`**(FastAPI), 예측 = `backend/ml/predict_core.py`. 모델 파일 4종 존재 → 진짜 예측.
- 시뮬 엔드포인트(주의: `/api/dashboard/simulate` **아님**):
  - **`POST /api/simulate`** `{ grid_id, changes:{...델타} }`
  - **`POST /api/simulate/batch`** `{ grid_ids:[...], changes }` → `{ count, mean_delta_c, results }`
- **해상도별**: 100m=단일 `/api/simulate` → `delta_c` · 250/500m=geojson `member_grid_ids`(구성 100m 셀)로
  batch → `mean_delta_c`.
- 슬라이더→changes: 녹지 `green_ratio=+v/100`, 불투수 `impervious_ratio=-v/100`, NDVI `ndvi=v/100`,
  알베도 `albedo=v/100`, 공원 `park_area_within_500m=v`.

---

## 3. 프론트 구조

### 오른쪽 상세 패널 `src/components/GridDetailSidePanel.tsx`
- 카드 순서: 히어로 → 핵심타일 → SHAP TOP → 환경 도넛 → 취약성 → **직접 시뮬레이션(취약성 아래)**.
- `SimulationCard`: 목업 슬라이더 5개 + "시뮬레이션 적용" + "예상 온도 저감(delta_c)".
  `canSimulate = 대상격자 있음 && !불완전`. 100m=단일 / 250·500m=batch(`member_grid_ids`).
- `isIncompleteGrid()`: 핵심 피처 결측 격자 → 경고 배지 + SHAP 숨김 + 시뮬 비활성.
- SHAP "현재값" 없으면 표기 생략. props: `properties, guGridTotal, selectedDistrict, selectedGridResolution, isOpen, onToggle, formatValue`.

### 왼쪽/지도 `src/components/MapDashboard.tsx`
- 왼쪽 `SearchPanel` = 제목 + 선택기(지역·기준·격자크기) + **지도 지표 선택**만.
- 삭제됨: `DatasetRail`·`TopMenu`, `RightToolbar`는 지도선택·이동만, 툴바 `top:20px`.
- **죽은 코드 정리 완료**(1770→1218줄): 옛 `SimulationApiPanel`·`GridDetailPanel`·`SimulationResultSummary`·관련 상수/타입/import 제거.
- API/타입: `api.ts`에 `simulateGridPolicy`+`simulateBatchGridPolicy`. `types/dashboard.ts`에 `BatchSimulationResponse`·`member_grid_ids`·모델 피처 8개.

---

## 4. 기존 완성 기능 (건드리지 말 것)

- **100m = Canvas 렌더**(`CanvasGridLayer.tsx`, 공간 인덱스). 250/500m = GeoJSON.
- **격자 선택 유지**: `grid_id` 기반, 지도 이동·확대·모드전환에도 유지. 지도선택 모드에서만 선택.
- **주황 테두리(focus outline) 해결**: `styles.css`의 `path.leaflet-interactive:focus{outline:none}`. 선택 검은 테두리는 유지.
- **지연 로딩**: 클릭 시 경량 속성 → 구별 상세 파일에서 `grid_id`로 전체 속성 보충.

---

## 5. 데이터 보정 (로컬 주입 — 재생성 시 사라짐 → `ml-data-pipeline-sync-ko.md` 참조)

1. **100m**: 모델 피처 8개(`slope_deg`·`max_ground_floor_count`·`floor_area_ratio_proxy`·`road_ratio`·`zoning_*`4) — SHAP 현재값용.
2. **250/500m** `member_grid_ids`(구성 100m 셀) — batch 시뮬용. 슬리버 셀은 최근접 폴백.
3. **250/500m** 무더위쉼터 `name/addr/lon/lat` — **블록 중심 기준** 최근접(100m과 동일). haversine이 파이프라인과 ~1m 일치.
- 대상: `public/dashboard/{100m,250m}/*.geojson`, `seoul_grid_250m.geojson`, `seoul_grid_500m.geojson`. 줄단위 삽입(포맷 보존).

---

## 6. 커밋 이력 (이번 세션, 최신 위)

```
(미커밋 예정) refactor: 미사용 시뮬/상세 코드 정리 + docs 인수인계/정리
3b8e006 docs: 데이터 로컬 보정의 ML 파이프라인 반영 지침 문서 추가
1d35140 fix: 250/500m 최근접 쉼터를 블록 중심 기준으로 계산
edc4b1a feat: 250/500m 쉼터 이름·주소·좌표 추가            (1d35140이 정정)
ee2b38e feat: 상세 패널 시뮬레이션 슬라이더(목업)·전 해상도 batch, 좌측 중복 제거·툴바
a3b12f9 feat: 250/500m member_grid_ids 추가
34d9085 refactor: 지도 위 미사용 패널 정리
db95bd8 feat: 100m geojson 모델 피처 8개 보완
aef4c59 fix: SHAP '데이터 준비중' 처리·불완전 격자 방어
```

---

## 7. 함정 / 주의 (실제로 겪음)

1. **브라우저 캐시**: 패널은 클릭 시 geojson `fetch`. 데이터 바꾸면 DevTools **"Disable cache" 켜고 새로고침**.
2. **sklearn 1.9.0** 필수(py3.11+). py3.9면 1.6.1까지 → 버전경고.
3. **불완전 격자**: 모델 피처 진짜 NaN 격자(~33개 노출) → 프론트 경고 배지로 방어 중.
4. main 공동작업 → amend 대신 새 커밋.

---

## 8. 남은 작업 / 다음 단계

- **Stage 4 — 왼쪽 패널**: 스크롤 없이 한 화면 + 오른쪽(gdp) 풍 디자인 재스타일.
- **아코디언(보류)**: 패널 길면 시뮬/상세 접기(지금은 목업대로 인라인).
- **Phase 3 — 격자 비교**: 두 번째 격자 선택 → 가장 큰 차이 콜아웃(목업 "다른 격자와 비교" 카드).
  두 번째 격자 state를 `MapDashboard`에 추가, 선택 로직 확장.
- **ML 파이프라인 반영**: `ml-data-pipeline-sync-ko.md`의 3건을 Windows 스크립트에 반영.
- (개선) 쉼터 지도 핀(좌표 이미 있음), `seoul_grid_100m_map.geojson` 재생성 경로 불명.

---

## 9. 디자인 원칙 (사용자 확정)

- **토스 느낌**: 흰 카드 on 연회색, 라운드, 큰 숫자, 넉넉한 여백. 앱 초록 `#0d5743` 액센트. 열=주황, 냉각=파랑.
- **priority_score 원점수 숨김** → 백분위·순위로만. **SHAP = 기여도 + 실제값** 둘 다. 라이트/다크 둘 다.
