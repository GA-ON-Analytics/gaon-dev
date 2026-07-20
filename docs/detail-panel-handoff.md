# 격자 상세 패널 구현 — 인수인계 (2026-07-20)

> **새 세션에서 첫 메시지로 이렇게 시작하세요:**
> *"`docs/detail-panel-handoff.md` 읽고, 나를 프론트엔드 초보로 생각하고 **선생님 모드**로 아주 자세히 가이드해줘. 코드는 내가 직접 타이핑할 거야. 한 번에 한 섹션씩, 코드 위치·왜·바꾸는 법까지 설명해줘."*

---

## 0. 한눈 요약

- **무엇을**: 지도에서 격자를 클릭하면 뜨는 **우측 상세 패널**(`GridDetailSidePanel.tsx`)을, 아래 목업 디자인대로 채운다.
- **목업(정답 디자인)**:
  - 아티팩트: https://claude.ai/code/artifact/5cd563cd-6f45-4a89-ad6e-efb37dcb53ea
  - 레포 파일: [`docs/detail-panel-mockup.html`](./detail-panel-mockup.html) ← **마크업/CSS의 소스. 이걸 열어서 JSX로 옮기면 됨**
- **방식**: 사용자가 직접 타이핑. Claude는 한 섹션씩 코드 + "무엇/왜/바꾸는 법" 설명.
- **브랜치**: `feat/#5-grid-detail-panel`
- **커밋/push는 사용자가 직접** 한다. Claude가 요청 없이 커밋/push 금지. 커밋 메시지에 Claude 공동저자 트레일러 금지.

---

## 1. 프로젝트 컨텍스트

- 앱: **도시 열섬 해결 대시보드** 프론트엔드 (`GA_ON_app/`, React + TypeScript + Vite + React‑Leaflet).
- 화면: 배경 지도 + 왼쪽 검색/지표 패널 + **오른쪽 상세 패널**(이번 작업 대상).
- 격자를 클릭하면 그 격자의 ML 분석 결과를 오른쪽 패널에 보여준다.
- 확인 방법: 터미널에서 **`npm run dev`** (프론트) + 백엔드(8000) 실행 → 브라우저에서 100m 격자 클릭. 지도 옵션 관련은 **F5(하드 새로고침)** 필요할 때 있음.

---

## 2. 현재 상태 (이미 돼 있는 것 — 건드리지 말 것)

지난 세션에서 아래는 **완성**됐다. 상세 패널 작업이 이걸 망가뜨리면 안 된다.

- **100m 격자 = Canvas 타일 렌더링** (`CanvasGridLayer.tsx`, 공간 인덱스). 6만 개 Path 렉 해결. 250m/500m는 기존 GeoJSON.
- **격자 선택 유지**: `grid_id` 기반. 이동 모드 전환·지도 이동·확대에도 선택 유지. 지도선택 모드에서만 선택.
- **주황 테두리 버그 해결**: 브라우저 포커스 outline이었음 → `styles.css`에 `.gisMap path.leaflet-interactive:focus { outline: none; }`. 검은 테두리(선택 표시)는 유지.
- **지연 로딩**: 클릭 시 지도용 경량 속성으로 즉시 표시 → 구별 상세 파일에서 `grid_id`로 전체 속성 보충. 자세한 건 `docs/map_100m_optimization_worklog_ko.md`.

### 지금 `GridDetailSidePanel.tsx`는?
"의미별 섹션 리포트"의 **텍스트 버전**이 이미 들어가 있다 (헤더 + 열 상태 + SHAP 텍스트 + 환경 + 취약성을 라벨/값으로). **목업의 시각 요소(온도 히어로, 도넛, 다이버징 막대, 슬라이더, 비교표)는 아직 없다.** 이걸 목업대로 업그레이드하는 게 이번 작업이다.

현재 받는 props (그대로 유지):
```tsx
properties: GridAnalysisProperties | null;   // 선택된 격자의 전체 속성 (지연 로딩 후 채워짐)
selectedDistrict: string;                    // '전체' 또는 '성동구' 등
selectedGridResolution: GridResolution;      // '100m' | '250m' | '500m'
isOpen: boolean;                             // 패널 펼침 여부
onToggle: () => void;                        // 접기/펴기 토글
formatValue: (props, key) => string;         // ★ MapDashboard의 formatAnyProperty. 단위 자동 포맷
```

`MapDashboard.tsx`의 렌더부(약 1078줄)에서 이 props를 넘긴다. `formatValue={formatAnyProperty}`.

---

## 3. 패널이 쓸 데이터 (`GridAnalysisProperties`)

`src/types/dashboard.ts`에 정의. `properties`로 들어온다. **`formatValue(properties, '필드명')`** 을 쓰면 단위가 자동으로 붙는다:
- `green_ratio`, `building_ratio`, `impervious_ratio` → `%` (값 ×100)
- `ndvi`, `albedo` → 소수 2자리
- `mean_actual_lst`, `mean_actual_anomaly`, `green_delta_c` → `℃`
- `nearest_shelter_distance_m`, `elevation_m` → `m`
- `area_m2`, `park_area_within_500m` → `㎡`
- 없거나 로딩 전 → `"데이터 준비중"`

### 목업 각 섹션 ↔ 필드 매핑
| 목업 섹션 | 쓰는 필드 |
|---|---|
| 히어로 구·동 | `gu_name`, `dong_name` (dong 없으면 구만) |
| 히어로 id·면적 | `display_grid_id` ?? `grid_id`, `area_m2` |
| 히어로 온도/열위험 | `mean_actual_lst`, `mean_actual_anomaly` |
| 우선순위 칩 | `priority_rank` (백분위·순위로만 표시. **원점수 `priority_score`는 안 보여줌** — 사용자가 이해 어려워함) |
| 타일 저감가능 | `green_delta_c` |
| SHAP 원인 | `top1_feature`/`top1_shap`, `top2_*`, `top3_*` + 각 feature의 실제 값(예: `impervious_ratio`) |
| 환경 도넛 | `green_ratio`, `impervious_ratio`, `building_ratio` |
| 취약성 | `est_population`, `est_elderly`, `nearest_shelter_distance_m` |

> **지연 로딩 주의**: SHAP(`top1~3_*`), `est_population/elderly`는 클릭 직후 잠깐 비어 있다가 채워진다. 없을 때 대비(옵셔널 체이닝 `?.`, 조건부 렌더 `{x && (...)}`)를 항상 넣을 것.

> **SHAP 실제값**: 목업은 "불투수면 비율 · 현재 40.7% → +0.84℃"처럼 **feature의 현재 값과 기여도를 함께** 보여준다. `top1_feature`는 문자열(예 `"impervious_ratio"`)이니, 그걸 키로 `formatValue(properties, top1_feature)` 하면 실제 값이 나온다.

---

## 4. 구현 단계 (Phase) — 이 순서로 가르치기

### Phase 1 — 화면 표시 (API 불필요, `properties`만 사용) ★여기부터
목업의 **정적 표시 부분**을 JSX로 옮긴다. 데이터는 이미 `properties`에 다 있다.

1. **CSS 먼저 이식**: `docs/detail-panel-mockup.html`의 `<style>` 내용을 `src/styles.css` 맨 아래에 복사. 클래스명 그대로 유지(`.gauge`, `.tile`, `.shap-item`, `.donut`, `.cmp-row` 등). 단, 목업의 `:root` 색 변수는 앱과 충돌할 수 있으니 **패널 전용 변수는 `.gridDetailSidePanel` 스코프 안**에 넣거나 접두사(`--gdp-`)를 붙여 정리.
2. **히어로 섹션**: 구·동, id·면적, 온도 큰 숫자, "열 위험" 태그, 시원→뜨거움 막대, 우선순위 칩.
3. **핵심 타일**: 저감 가능(`green_delta_c`), 추정 인구.
4. **SHAP 원인**: 다이버징 막대. `top1~3_feature`/`shap` 반복. 양수=주황(오른쪽), 음수=파랑(왼쪽). feature 실제값 병기.
5. **환경 도넛**: SVG 원 3개. `pathLength="100"` + `stroke-dasharray="퍼센트 100"` 트릭으로 채움.
6. **취약성**: 라벨/값 행 3개.

각 섹션은 **목업 HTML의 해당 부분을 JSX로 옮기고**, 고정 숫자 자리에 `formatValue(properties, '필드')`를 끼우면 된다. (class → className, style="..." → style={{...}} 또는 CSS 클래스로)

### Phase 2 — 시뮬레이션 (API 필요)
- 슬라이더 값 = `useState`로 관리 (녹지·불투수·NDVI·알베도·공원면적).
- "시뮬레이션 적용" 클릭 → `api.ts`의 **`simulateGridPolicy(payload)`** 호출.
- payload = `SimulationRequest`: `{ grid_id, changes: { green_ratio: 0.05, impervious_ratio: -0.05, ndvi: 0.05, albedo: 0.05 }, parameters: { park_area_m2: 1000 } }` (타입은 `types/dashboard.ts`, 백엔드는 `backend/main.py`의 `/api/dashboard/simulate` 계열).
- 응답 `SimulationResponse.delta_c`(예상 저감 ℃)를 결과 박스에 표시. 목업의 실시간 슬라이더 계산은 **가짜(mock)** 이니, 실제로는 API 결과로 교체.

### Phase 3 — 격자 비교 (선택 로직 확장, 마지막)
- 두 번째 격자를 담을 state(예: `compareGridProperties`)를 `MapDashboard`에 추가하고, "비교 격자 추가" 모드/버튼으로 두 번째 클릭을 잡는다.
- 두 `properties`를 비교해 **가장 큰 차이** 필드를 계산 → 콜아웃 + 표. 비교 격자의 `dong_name`도 표시.
- 선택 로직을 건드리므로 Phase 1·2 안정화 후 진행.

---

## 5. 관련 파일

| 파일 | 역할 |
|---|---|
| `src/components/GridDetailSidePanel.tsx` | **작업 대상.** 상세 패널 |
| `docs/detail-panel-mockup.html` | **목표 디자인**(마크업+CSS 소스) |
| `src/components/MapDashboard.tsx` | `formatAnyProperty`, `selectedGridProperties`, 패널 렌더(≈1078줄) |
| `src/types/dashboard.ts` | `GridAnalysisProperties`, `SimulationRequest/Response`, `LayerKey` |
| `src/services/api.ts` | `simulateGridPolicy(payload)` (≈115줄) |
| `src/styles.css` | 패널 CSS 추가 위치 |
| `src/components/CanvasGridLayer.tsx` | 100m Canvas 렌더 (건드리지 말 것) |
| `docs/map_100m_optimization_worklog_ko.md` | 100m 최적화·선택 로직 상세 기록 |

---

## 6. 교육 진행 규칙 (새 세션 Claude용)

- **한 번에 한 섹션.** 코드 블록 → 사용자가 타이핑 → 저장 후 브라우저 확인 → 다음.
- 모든 코드에 **"무엇 / 왜 / 네가 바꾸는 법"** 3줄 설명.
- React 초보 개념은 짚어주기: `className`(class 아님), `style={{...}}`, 조건부 렌더 `{조건 && (...)}`, 삼항 `{조건 ? A : B}`, 리스트 `.map()`, 옵셔널 체이닝 `?.`.
- **JSX 변환 팁**: 목업 HTML의 `class=` → `className=`, `stroke-width` 등 SVG 속성은 JSX에서 그대로 쓰되 `stroke-dasharray`는 문자열 OK. 인라인 `style`은 객체(`style={{width:'42%'}}`)로.
- 막히면: 빨간 에러 메시지 복붙 요청 / F12 콘솔 확인 / 스샷.
- **검증**: 화면 확인이 어려우면 `npx tsc --noEmit`로 타입 통과부터 확인.
- 커밋/push는 **사용자에게 맡긴다**.

---

## 7. 결정된 디자인 원칙 (사용자 확정)

- **토스 느낌**: 흰 카드 on 연회색, 라운드, 큰 숫자, 여백 넉넉.
- **색감**: 앱 초록 `#0d5743` 액센트 (왼쪽 패널과 통일). 열=주황, 냉각=파랑.
- **priority_score 원점수(61.2)는 표시하지 않는다** → "상위 4%", "구 내 12위" 같은 **백분위·순위**로만.
- SHAP은 **기여도 + feature 실제값** 둘 다.
- 비교는 **동 이름 + 상세 지표(양쪽 값 나란히) + 가장 큰 차이 콜아웃**.
- 라이트/다크 모드 둘 다 지원.
