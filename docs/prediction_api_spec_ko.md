# GAON 열섬 시뮬레이션 예측 API 명세

작성일: 2026-07-11
대상: 프론트엔드/백엔드 개발자 (+ 추후 LLM 연동)

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
- `green_ratio_delta`는 `green_ratio`에 더한다.
- `impervious_ratio_delta`는 `impervious_ratio`에 더한다. 프론트의 "불투수면 감소폭 5%"는 `-0.05`로 전달한다.
- `park_area_m2`는 `park_area_within_500m`에 더한다.
- `built_surface_ratio`는 `impervious_ratio`와 값이 100% 동일한 중복 변수여서 제거했다.
  두 변수를 연동하던 로직도 함께 삭제됐다.

응답:
```json
{
  "grid_id": "11110_00002",
  "gu_name": "종로구",
  "before_anomaly": 4.743,
  "after_anomaly": 4.282,
  "delta_c": -0.462,
  "uncertainty_std": 1.283,
  "changed_features": {
    "green_ratio": {"before": 0.16, "after": 0.21}
  },
  "message": "ML simulation completed",
  "warnings": []
}
```

### POST `/api/simulate/batch` — 여러 격자에 같은 변화 (구역 단위 정책)
```json
{ "grid_ids": ["11110_00002","11110_00003"], "changes": {"green_ratio":0.1} }
→ { "count":2, "mean_delta_c":-0.21, "results":[...] }
```

---

## 4. 물리적 제약 (LLM 임의 입력 대비) — 중요

RandomForest는 **학습 안 한 값에 헛소리(환각)**를 낼 수 있다. 그래서 API가 자동으로:

1. **범위 clip**: 각 변수를 학습 분포 min~max로 제한 (녹지 900% → 87%로)
2. **파생 일관성**: 불투수 변화량은 시가화면 비율에도 함께 반영
3. **경고 반환**: clip 등 제약이 작동하면 `warnings`로 알림 → 프론트가 "일부 조정됨" 표시 가능

주의: 현재 구현은 `building_ratio + impervious_ratio + green_ratio`를 합 1로 강제 정규화하지 않는다.
세 변수는 모델 feature이며 의미가 겹칠 수 있으므로, 정규화하면 사용자가 입력한 정책 변화 해석이 왜곡된다.
건물 비율은 사용자가 건물 관련 정책을 입력하지 않는 한 변경하지 않는다.

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
