# GAON 열섬 시뮬레이션 예측 API 명세 — 배포 백엔드

작성일: 2026-07-11
대상: 프론트엔드/백엔드 개발자 (+ 추후 LLM 연동)

> **이 문서는 실제로 배포되는 서버 `backend/main.py`를 설명한다. 이쪽이 기준이다.**
> ML 레포(`gaon-ml`)에는 실험용 서버 `src/api/server.py`가 따로 있고 명세도 따로 있다.
> 엔드포인트 경로(`/api/health` 대 `/health`)와 실행 명령이 서로 다르므로
> **두 문서를 같게 만들면 안 된다.** 예측 로직(`predict_core.py`)만 두 레포가
> 같은 답을 내야 하며, ML 레포의 `check_parity_with_app.py`로 대조한다.

---

## 0. 이게 왜 필요한가

대시보드에서 **"이 블록에 이렇게 바꾸면 온도가 어떻게 변해?"**에 답하려면 이 API가 필요하다.

```
❌ 안 되는 방법: 가중치(식생 21.8%)를 곱해서 계산
   → 우리 모델은 RandomForest(비선형). 곱셈 공식이 없다.
✅ 되는 방법: 바꾼 값을 모델에 다시 넣어 재예측 → 이 API가 그걸 한다.
```

**고정 시나리오(녹지확대 등)는 대시보드 GeoJSON에 미리 계산돼 있다**(`green_delta_c`).
하지만 사용자가 **자유롭게** 바꾸거나 **LLM**이 "건물 줄이고 나무 심으면?"에 답하려면
이 API로 실시간 재예측해야 한다.

---

## 1. 구성 (개발자에게 전달할 것)

```
backend/main.py                              FastAPI 서버
backend/ml/predict_core.py                   예측·제약 로직 (모델 로드, 재예측)
backend/ml/build_feature_meta.py             변수 사전 생성 보조 스크립트
backend/models/seoul_grid_explain_model.joblib        compact 배포 모델
backend/models/seoul_grid_feature_columns.json        모델 입력 변수 목록
backend/models/feature_meta.json                      변수 사전(범위·메타)
backend/data/processed/seoul_grid_dataset.csv         격자별 현재 변수값 (예측 기준)
```

## 2. 실행 (로컬 / Oracle Cloud 공통)

```bash
python3 -m pip install -r backend/requirements.txt
python3 -m uvicorn backend.main:app --reload --port 8000

# 자동 문서(Swagger): http://localhost:8000/docs
```
모델은 최초 요청 시 1회 로드 후 캐시(lru_cache)한다.

---

## 3. 엔드포인트

### GET `/api/health`
```json
{"ok": true, "backend": "fastapi"}
```

### GET `/api/model/status`
필수 모델/데이터 파일의 존재 여부와 로딩 가능 여부를 반환한다.
```json
{"model":true,"feature_columns":true,"feature_meta":true,"dataset":true,"ready":true}
```

### GET `/api/features` — LLM/프론트가 "무엇을 바꿀 수 있나" 파악
각 변수의 한글명·설명·**자연어 별칭**·온도 방향(+올림/-내림)·조절가능성·범위 반환.
```json
{
  "count": 19,
  "editable_policy": ["building_ratio","ndvi","green_ratio","impervious_ratio","albedo", ...],
  "features": [
    {"name":"ndvi","korean":"식생지수","aliases":["나무","가로수","나무 심기","녹화"],
     "temp_direction":"-","editable":"policy","min":-0.329,"max":0.767,"is_ratio":false},
    ...
  ]
}
```
> LLM은 이 `aliases`로 사용자 말("나무 심자")을 변수(`ndvi`,`green_ratio`)로 번역한다.
> `editable`: `policy`=정책 개입 가능 / `derived`=간접 / `fixed`=고정(표고·경사·용도지역).

### GET `/api/grid/{grid_id}` — 격자 현재 값
```json
{"grid_id":"11230_00746","gu_name":"동대문구","features":{"building_ratio":0.0,"green_ratio":0.247, ...}}
```

### POST `/api/simulate` — ★ 핵심: 변화 → 재예측
요청:
```json
{
  "grid_id": "11110_00002",
  "policy_options": ["green_ratio_increase", "impervious_ratio_reduction"],
  "parameters": {
    "green_ratio_delta": 0.05,
    "impervious_ratio_delta": -0.05,
    "park_area_m2": 1000
  }
}
```
**모든 델타는 절대 변화량이다.** 비율 변수는 퍼센트포인트 단위로 원래 값에 그냥 더한다.
`green_ratio`가 0.40인 격자에 `+0.10`을 주면 **0.50**이 된다. "현재 값의 10%"를 더하는
상대 증가가 아니므로 0.44가 아니다.

- `green_ratio_delta`는 `green_ratio`에 더한다.
- `impervious_ratio_delta`는 `impervious_ratio`에 더한다. 프론트의 "불투수면 감소폭 5%p"는 `-0.05`로 전달한다.
- `park_area_m2`는 `park_area_within_500m`에 더한다(단위 ㎡).
- `ndvi`는 비율이 아니라 −1~1 지표라, `+0.05`면 0.30 → 0.35가 된다.
- `built_surface_ratio`는 `impervious_ratio`와 값이 100% 동일한 중복 변수여서 제거했다(#15).
- `couple_land_cover` (기본 `true`): 녹지↔불투수 연동. 아래 4장 참고.

응답:
```json
{
  "grid_id": "11110_00002",
  "gu_name": "종로구",
  "before_anomaly": 4.743,
  "after_anomaly": 4.282,
  "delta_c": -0.462,
  "uncertainty_std": 1.283,
  "delta_std": 0.981,
  "changed_features": {
    "green_ratio": {"before": 0.16, "after": 0.21},
    "impervious_ratio": {"before": 0.42, "after": 0.3875}
  },
  "message": "ML simulation completed",
  "warnings": ["녹지 +5.0%p에 연동해 불투수면을 -3.3%p 조정했습니다 (관측 기울기 -0.65). 불투수면을 직접 지정하면 연동하지 않습니다."]
}
```

> `changed_features`에는 **연동으로 자동 변경된 변수도 포함**된다. 사용자가 입력하지 않은
> 변수가 왜 바뀌었는지는 `warnings`에 문장으로 담기므로 그대로 화면에 노출하면 된다.

### POST `/api/simulate/batch` — 여러 격자에 같은 변화 (구역 단위 정책)
```json
{ "grid_ids": ["11110_00002","11110_00003"], "changes": {"green_ratio":0.1},
  "couple_land_cover": true }
→ { "count":2, "mean_delta_c":-0.21, "results":[...] }
```

---

## 4. 물리적 제약 (LLM 임의 입력 대비) — 중요

RandomForest는 **학습 안 한 값에 헛소리(환각)**를 낼 수 있다. 그래서 API가 자동으로:

1. **범위 clip**: 각 변수를 학습 분포 min~max로 제한 (녹지 900% → 87%로)
2. **녹지↔불투수 연동**: 아래 참고
3. **경고 반환**: clip·연동이 작동하면 `warnings`로 알림 → 프론트가 그대로 표시

### 녹지↔불투수 연동 (이슈 #14)

녹지를 늘리려면 그만큼 다른 지표면이 줄어야 한다. 연동이 없으면 비율 합이 실제보다 커지는
**학습 데이터에 없는 조합**이 모델에 들어가고, 효과가 크게 과소평가된다.

```
green_ratio +5%p, 격자 11230_00001 기준
  연동 ON   delta_c -0.175 °C   (impervious_ratio -3.3%p 자동)
  연동 OFF  delta_c -0.022 °C   ← 8배 과소평가
```

**계수는 1:1이 아니라 -0.65다.** 근거:

| 측정 | 값 |
|---|---|
| `impervious_ratio`를 `green_ratio`로 회귀한 기울기 | **-0.655** (1:1이면 -1.0) |
| 상관계수 | -0.737 |
| `green + impervious` 합 평균 | **0.715** (1이 아님) |
| 나머지 몫(물·나대지 등) 중앙값 | 0.242 |

두 변수는 Dynamic World 9개 클래스 중 식생 5개(green)와 `built` 1개라서 합이 1이 되지 않는다.
늘어난 녹지의 약 35%는 물·나대지에서 오는 것으로 관측된다. 다만 **녹지 구간별로 기울기가
크게 흔들리므로**(10~20% 구간은 부호가 반대인 +12.5) 전역 평균값을 쓰는 근사임을 유의한다.

**동작 규칙**
- 기본 `couple_land_cover: true`. `false`면 연동하지 않는다.
- `changes`에 `impervious_ratio`를 **직접 넣으면 연동하지 않는다** — 사용자 입력 우선.
- 역방향(불투수 → 녹지)은 연동하지 않는다. 불투수면을 줄인다고 녹지가 늘어난다는 보장이 없다.
- 연동 결과가 학습범위를 벗어나면 clip되고, 그 사실도 `warnings`에 담긴다.

주의: `building_ratio`는 연동 대상이 아니다. 건물은 사용자가 건물 관련 정책을 입력하지 않는 한
변경하지 않는다. 세 비율을 합 1로 강제 정규화하지도 않는다 — 정규화하면 사용자가 입력한
정책 변화의 해석이 왜곡된다.

---

## 5. LLM 연동 흐름 (추후)

```
사용자: "여기 건물 좀 줄이고 나무 심으면 얼마나 시원해져?"
  ↓ LLM이 /features의 aliases로 번역
changes = {"building_ratio": -0.15, "ndvi": 0.1, "green_ratio": 0.15}
  ↓ POST /api/simulate
delta_c = -1.2
  ↓ LLM이 자연어로
"건물을 15% 줄이고 나무를 심으면 약 1.2도 내려갑니다 (±1.3도 불확실성)."
```
**LLM은 계산하지 않는다. 변수 번역과 설명만 하고, 온도 계산은 이 API(모델)가 한다.**
(LLM에 온도를 직접 계산시키면 환각이 난다.)

---

## 6. 배포 (Oracle Cloud)

- compact 모델 25MB → Free Tier VM/컨테이너에 충분
- Python 백엔드(FastAPI) + 위 파일들만 올리면 됨
- 예측 1건 ~0.01초, 배치도 빠름
- 대시보드/LLM은 이 API URL만 호출

## 7. 한계 (정직하게)

- 예측은 grouped MAE 약 ±1.06°C 불확실성. `uncertainty_std`와 함께 "범위"로 제시할 것.
- 학습 분포를 크게 벗어난 조합(건물 0인데 고층 등)은 신뢰도 낮음 → `warnings` 참고.
- 격자 내부 "건물 배치"는 못 바꾼다(집계 변수라). 배치 최적화는 별도 미시 시뮬(Phase 2).
