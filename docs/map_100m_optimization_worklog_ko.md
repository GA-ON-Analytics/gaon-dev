# 지도 격자 선택 및 100m 성능 최적화 작업 정리

작성일: 2026-07-20

## 1. 작업 목적

지도 선택과 이동 모드 사이의 선택 상태 충돌을 해결하고, 서울 전체 100m 격자를 표시하면서도 브라우저 렉을 줄이는 것이 이번 작업의 목적이다.

현재 구현의 핵심 결론은 다음과 같다.

- 기본 격자 단위는 `100m`이다.
- 분석 지역이 `전체`이면 서울 전체 100m 격자 64,574개를 사용할 수 있다.
- 분석 지역으로 특정 자치구를 선택하면 해당 구의 100m 격자만 표시한다.
- 100m는 Leaflet 개별 Path 레이어 대신 공간 인덱스 기반 Canvas 타일 레이어로 렌더링한다.
- 250m와 500m는 기존 React-Leaflet GeoJSON 방식을 유지한다.
- 선택한 격자는 이동 모드 전환이나 지도 이동·확대만으로 해제되지 않는다.

## 2. 현재 사용자 동작

| 분석 지역 | 격자 단위 | 지도 표시 | 클릭 동작 |
| --- | --- | --- | --- |
| 전체 | 100m | 서울 전체 64,574개 | 클릭한 격자를 직접 선택하며 분석 지역은 `전체`로 유지 |
| 특정 자치구 | 100m | 선택한 구의 격자만 표시 | 해당 구의 격자를 선택 |
| 전체 | 250m/500m | 서울 전체 격자 | 지역 내부 또는 자치구 이름 클릭 시 해당 자치구로 진입 |
| 특정 자치구 | 250m/500m | 선택한 구의 격자만 표시 | 해당 구의 격자를 선택 |

추가 동작은 다음과 같다.

- 격자 선택은 `지도선택` 모드에서만 실행된다.
- `이동` 모드로 바꾸어 다른 위치를 클릭하거나 지도를 드래그해도 기존 선택은 유지된다.
- 팝업의 닫기 버튼을 누르거나 다른 격자를 선택하면 기존 선택이 해제된다.
- 마우스 왼쪽 드래그는 Leaflet 기본 이동이며, 오른쪽 드래그 이동도 지원한다.

## 3. 지도 선택 테두리 수정

### 3.1 주황색 테두리 제거

Leaflet SVG Path가 클릭 후 브라우저 포커스를 받으면서 기본 포커스 outline이 주황색으로 표시되고 있었다.

`src/styles.css`에서 다음 선택자에 `outline: none`을 적용해 브라우저 포커스 테두리를 제거했다.

```css
.gisMap path.leaflet-interactive:focus {
  outline: none;
}
```

검은색 테두리는 애플리케이션이 선택 격자에 직접 적용하는 선택 표시이므로 유지했다.

### 3.2 선택 상태 유지

기존 구현은 격자 레이어가 다시 생성되거나 팝업이 닫힐 때 `selectedGridProperties`를 초기화해 이동 모드에서도 선택이 해제될 수 있었다.

현재는 다음 값으로 선택을 관리한다.

- `selectedGridIdRef`: 현재 선택된 격자 ID
- `selectedGridProperties`: 상세 패널과 지표에 표시할 속성
- `selected100mFeature`: 100m Canvas에서 선택한 도형
- `selected100mPopupPosition`: 100m 팝업 위치
- 250m/500m의 `selectedGridLayerRef`: 기존 Leaflet Path 선택 표시

100m Canvas는 레이어 객체가 아니라 `grid_id`를 기준으로 검은색 선택 테두리를 다시 그린다. 따라서 Canvas 타일이 교체되어도 선택 상태가 유지된다.

## 4. 빈 상태 안내 문구 수정

데이터가 이미 존재하지만 아무 격자도 선택하지 않은 상태를 `데이터 준비중`으로 표시하던 문제를 수정했다.

현재 안내 문구는 상태에 따라 달라진다.

- 전체 + 100m: `서울 전체에서 분석할 격자를 클릭하세요`
- 전체 + 250m/500m: `지도에서 분석할 지역을 클릭하세요`
- 특정 자치구: `{자치구명}에서 분석할 격자를 클릭하세요`
- 지표 값: 선택 전에는 `선택 전`
- 실제 속성이 누락된 선택 격자: `데이터 준비중`

좌측 패널과 우측 상세 패널에 같은 기준을 적용했다.

## 5. 100m 데이터 최적화

### 5.1 원본 데이터 상태

원본 파일은 `public/dashboard/seoul_grid_100m.geojson`이다.

- 파일 크기: 약 99.6MB
- 격자 수: 64,574개
- 속성 JSON 합계: 약 67.8MB
- Geometry JSON 합계: 약 15.3MB

원본 전체를 그대로 지도 렌더링에 사용하면 상세 분석용 속성까지 최초 로딩과 메모리에 포함된다.

### 5.2 지도 전용 경량 파일

`scripts/generate-100m-map-data.mjs`가 원본에서 지도 렌더링과 클릭에 필요한 속성만 추출한다.

생성 파일:

```text
public/dashboard/seoul_grid_100m_map.geojson
```

현재 크기는 약 40.1MB이며 다음 속성을 포함한다.

```text
grid_id
gu_code
gu_name
area_m2
priority_score
mean_actual_anomaly
mean_actual_lst
green_delta_c
green_ratio
ndvi
building_ratio
impervious_ratio
nearest_shelter_distance_m
```

원본 100m 데이터가 변경되면 다음 명령으로 지도 파일도 다시 생성해야 한다.

```bash
npm run generate:100m-map
```

### 5.3 API와 정적 파일 경로

프런트엔드는 다음 순서로 지도용 100m 데이터를 요청한다.

```text
/api/dashboard/grids/100m/map
/dashboard/seoul_grid_100m_map.geojson
```

FastAPI에는 `/api/dashboard/grids/100m/map` 엔드포인트를 추가했다.

### 5.4 상세 속성 지연 로딩

지도용 파일에 없는 상세 속성은 격자를 클릭한 뒤 해당 자치구의 기존 100m 파일에서 보충한다.

동작 순서는 다음과 같다.

1. 지도 전용 속성으로 즉시 격자 선택과 팝업 표시
2. `gu_code`와 `gu_name`으로 해당 자치구 상세 파일 요청
3. 같은 `grid_id`를 찾아 전체 속성으로 상세 패널 갱신
4. 한 번 받은 자치구 상세 파일은 `district100mCacheRef`에 저장

서울 전체 지도 파일도 `seoul100mMapCacheRef`에 저장하므로 자치구를 바꿀 때 40MB 파일을 다시 요청하지 않는다. 특정 자치구 표시 시에는 캐시된 전체 데이터에서 `gu_code`로 필터링한다.

## 6. 100m Canvas 타일 렌더링

### 6.1 변경 이유

`preferCanvas`만 적용해도 SVG DOM 문제는 줄지만 Leaflet이 여전히 64,574개의 Path 객체와 이벤트 판정을 관리한다. 지도 이동·확대와 지표 변경 시 이 객체들을 갱신하는 비용이 남는다.

현재 100m 구현은 `src/components/CanvasGridLayer.tsx`의 사용자 정의 `L.GridLayer`를 사용한다.

### 6.2 렌더링 구조

```text
100m 경량 GeoJSON
  -> FeatureSpatialIndex 생성
  -> 현재 Leaflet 타일 범위와 겹치는 격자 조회
  -> 256 x 256 Canvas 타일에 직접 그리기
```

주요 구현은 다음과 같다.

- 위·경도 0.01도 버킷 기반 공간 인덱스
- 타일 경계와 겹치는 Feature만 조회
- Canvas `fill`과 `stroke`로 격자 렌더링
- `grid_id`가 선택 ID와 같으면 검은색 3px 테두리 적용
- 마우스 좌표가 포함된 Polygon을 공간 인덱스로 검색해 클릭 처리
- 마우스 이동은 `requestAnimationFrame`으로 묶어 툴팁 검색 횟수 제한
- 툴팁은 위치를 먼저 지정한 뒤 지도에 추가해 Leaflet 위치 오류 방지

테스트 화면에서는 64,574개의 Leaflet Path 대신 약 24개의 Canvas 타일이 생성됐다. 타일 수는 화면 크기, 줌 단계와 `keepBuffer`에 따라 달라진다.

### 6.3 250m/500m 유지

250m와 500m는 기존 React-Leaflet `GeoJSON` 레이어를 그대로 사용한다. 100m 최적화가 다른 해상도의 클릭, 팝업과 스타일에 영향을 주지 않도록 렌더링 분기를 분리했다.

## 7. 전체 화면과 자치구 화면 처리

기본 해상도는 다음 값으로 설정돼 있다.

```ts
const DEFAULT_GRID_RESOLUTION: GridResolution = '100m';
```

100m 데이터 표시 기준은 다음과 같다.

```ts
setGridGeoJson(sigCode ? filterGridByDistrict(data, sigCode) : data);
```

- 분석 지역이 `전체`이면 `sigCode`가 없으므로 전체 데이터 사용
- 특정 자치구이면 `gu_code === sigCode`인 Feature만 사용

주의할 점은 `전체 + 100m` 격자 클릭 시 자치구로 이동시키는 과거 분기를 다시 추가하면 안 된다는 것이다. 현재 요구사항은 전체 화면의 모든 100m 격자를 직접 선택하는 것이다.

## 8. 관련 파일

| 파일 | 역할 |
| --- | --- |
| `src/components/MapDashboard.tsx` | 해상도·자치구별 데이터 로딩, 선택 상태, 팝업, Canvas/GeoJSON 렌더링 분기 |
| `src/components/CanvasGridLayer.tsx` | 100m 공간 인덱스, Canvas 타일 렌더링, 클릭·호버 판정 |
| `src/components/GridDetailSidePanel.tsx` | 우측 상세 패널과 선택 유도 문구 |
| `src/services/api.ts` | 100m 지도 파일 API와 정적 파일 fallback |
| `src/styles.css` | 포커스 테두리 제거, 선택 유도 UI와 상세 패널 스타일 |
| `backend/main.py` | `/api/dashboard/grids/100m/map` 제공 |
| `scripts/generate-100m-map-data.mjs` | 원본 100m GeoJSON에서 경량 지도 파일 생성 |
| `public/dashboard/seoul_grid_100m_map.geojson` | 실제 지도 렌더링용 100m 데이터 |
| `package.json` | `generate:100m-map` 명령 등록 |

## 9. 검증 결과

프로덕션 빌드:

```bash
npm run build
```

검증된 동작은 다음과 같다.

- 기본 진입 해상도 `100m`
- 전체 화면 격자 수 64,574개
- 전체 화면에서 성북구 격자 `11290_00800` 직접 선택
- 전체 화면에서 다른 구의 격자를 선택해도 분석 지역 `전체` 유지
- 서초구 선택 시 100m 격자 4,956개만 표시
- 서초구 격자 `11650_03094` 선택과 상세 속성 표시
- 이동 모드 전환 및 지도 이동 후 선택 ID와 팝업 유지
- 지도 지표 변경 후 선택 ID와 팝업 유지
- 서초구 250m 858개 표시 및 격자 `11650_00555` 클릭 정상
- Canvas 호버와 클릭 후 브라우저 콘솔 오류 없음
- TypeScript와 Vite 프로덕션 빌드 통과

## 10. 알려진 한계와 다음 최적화 후보

Canvas 타일 방식은 지도 이동·확대와 Feature 객체 관리 비용을 크게 줄이지만 최초 40.1MB 파일 다운로드와 JSON 파싱 비용은 남아 있다.

추가 최적화 우선순위는 다음과 같다.

1. 배포 서버에서 Gzip 또는 Brotli 압축 확인
2. Web Worker에서 GeoJSON 파싱과 공간 인덱스 생성
3. IndexedDB에 100m 지도 파일 또는 파싱 결과 캐시
4. 여전히 부족하면 PMTiles/MVT와 MapLibre WebGL 전환 검토

고사양 컴퓨터에서는 CPU와 메모리 성능 때문에 더 부드럽지만, 기존 Leaflet 개별 Path 구조의 병목을 하드웨어만으로 완전히 없앨 수는 없다.

## 11. Canvas 변경만 되돌리는 방법

Canvas 렌더링에서 예상하지 못한 문제가 생기면 경량 데이터/API 작업은 유지하고 렌더링 부분만 되돌릴 수 있다.

되돌릴 범위:

1. `MapDashboard.tsx`의 100m `CanvasGridLayer` 분기를 기존 `GeoJSON` 렌더링으로 교체
2. `selected100mFeature`와 `selected100mPopupPosition` 기반 Popup 처리 제거
3. `src/components/CanvasGridLayer.tsx` 제거

경량 100m 파일, 상세 속성 지연 로딩과 전체 데이터 캐시는 Canvas와 독립적이므로 그대로 재사용할 수 있다.

