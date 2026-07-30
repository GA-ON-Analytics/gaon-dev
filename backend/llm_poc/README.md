# GA:ON Ollama + Qwen Tool Calling

Ollama의 `qwen3:4b`는 사용자 질문을 JSON Schema에 맞는 구조화 결과로
해석합니다. Qwen은 Tool 인자를 직접 계산하지 않으며, Python이 구조화 결과를
검증하고 단위와 부호를 결정한 뒤 다음 Tool의 실제 인자를 만듭니다. Tool 실행
후 최종 한국어 답변도 Python formatter가 결정적으로 생성하며, 같은 공용
서비스를 CLI와 FastAPI `POST /api/chat`에서 사용합니다.

- `get_grid_data`: 현재 격자의 18개 모델 입력 지표 중 요청한 필드 조회
- `run_simulation`: 100m 격자의 정책 변경값을 기존 머신러닝 모델에 적용해 재예측

조회는 `backend.ml.predict_core.get_grid_features()`, 시뮬레이션은
`backend.ml.predict_core.predict()`를 직접 재사용합니다. 별도 CSV repository나
예측 계산식을 만들지 않습니다.

## 사전 준비

- Python 3.11 이상
- 설치 및 실행 중인 Ollama
- 프로젝트의 기본 backend 의존성

프로젝트 루트(`gaon-dev`)에서 다음 명령을 실행합니다.

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r backend/requirements.txt
python -m pip install -r backend/llm_poc/requirements.txt
ollama pull qwen3:4b
```

Ollama 서버가 실행 중이 아니라면 별도 터미널에서 시작합니다.

```bash
ollama serve
```

Ollama 요청 제한 시간은 `GAON_LLM_TIMEOUT_SECONDS`로 설정할 수 있습니다.
값을 생략하거나 0 이하, 숫자가 아닌 값, NaN 또는 무한대를 지정하면 안전한
기본값 120초를 사용합니다.

```bash
export GAON_LLM_TIMEOUT_SECONDS=120
```

이 제한은 질문 전체가 아니라 단일 Ollama HTTP 호출에 적용됩니다. 현재 지원
질문은 Ollama를 한 번만 호출합니다. 동기 클라이언트에는 서버 추론을 명시적으로
취소하는 API가 없으므로, 브라우저 요청 취소 또는 timeout이 이미 시작된 서버
추론까지 항상 중단한다고 보장하지 않습니다.

## CLI 실행

녹지율·불투수율 회귀, 범용 지표 조회, 미지원 지표, 전체 데이터 조회와
시뮬레이션 E2E 질문을 독립적으로 모두 실행합니다.

```bash
python -m backend.llm_poc.cli_test
```

단일 질문도 위치 인자로 전달할 수 있습니다.

```bash
python -m backend.llm_poc.cli_test \
  "11230_00001 격자의 녹지율과 불투수율을 알려줘."

python -m backend.llm_poc.cli_test \
  "11230_00001 격자의 NDVI와 알베도를 알려줘."

python -m backend.llm_poc.cli_test \
  "11230_00001 격자의 전체 데이터를 알려줘."

python -m backend.llm_poc.cli_test \
  "11230_00001 격자의 녹지율을 5%p 높이면 모델 기준 예상 변화량이 어떻게 돼?"
```

CLI는 각 질문에 대해 다음 항목을 출력하고 검증합니다. 사용자 질문 원문과
내부 추론 내용은 성능 로그에 출력하지 않습니다.

1. 사용자 질문 길이와 구조화 라우터 결과 상태
2. Python이 확정한 도구명과 실제 인자
3. 도구 반환값
4. 단계별 성능 지표와 Ollama 호출 횟수
5. Python formatter가 만든 검증 완료 한국어 답변
6. 전체 실행의 최종 종료 코드

`get_grid_data`의 `values`는 기존 데이터 원본값을 유지합니다. 비율 필드는
0~1 원본값이며 퍼센트 변환과 반올림은 중앙 표시 규칙을 재사용하는 Python
formatter가 수행합니다.

구조화 라우터는 `temperature=0`, `keep_alive="5m"`를 유지합니다.
`think=False`, `num_predict=768`로 실행하며, JSON
Schema 밖의 자유 형식 본문이나 내부 추론을 API·브라우저 저장소·성능 로그로
전달하지 않습니다.

## 응답 처리와 성능 로그

현재 처리 흐름은 다음과 같습니다.

```text
질문 → Qwen JSON Schema 구조화 해석 1회
     → Python intent·필드·단위·미해결 항목 검증
     → Python이 실제 Tool 인자 계산 → Python Tool 실행
     → Python formatter → 수치·단위·한계 검증 → API 응답
```

Tool 결과를 최종 답변으로 바꾸기 위한 두 번째 Ollama 호출은 없습니다.
`format_grid_data_answer()`와 `format_simulation_answer()`는 Tool 결과에 있는
값만 사용합니다. 시뮬레이션 답변에는 내부 Python 함수명을 노출하지 않으며,
경고와 모델 한계를 빠뜨리지 않습니다.

Qwen의 내부 구조화 결과는 다음 계약을 사용합니다.

```json
{
  "intent": "simulation",
  "lookup_all": false,
  "requested_fields": [],
  "excluded_scope": false,
  "changes": [
    {
      "field": "green_ratio",
      "operation": "increase",
      "value": 5,
      "unit": "percent",
      "basis": "direct",
      "source_text": "녹지율 5프로 올려줘",
      "value_text": "5프로"
    }
  ],
  "assumptions": [
    "일반 % 표현을 같은 수치의 %p로 적용"
  ],
  "unresolved": []
}
```

- `intent`: 현재 데이터 조회(`lookup`), 시뮬레이션 또는 미지원 요청을
  구분합니다.
- `lookup_all`: 전체 격자 데이터 조회인지 나타냅니다.
- `requested_fields`: 일부 조회에서 사용자가 요청한 지표를 정식 영문
  필드명으로 담습니다. 전체 조회에서는 빈 배열입니다.
- `excluded_scope`: 전체 범위 표현을 사용자가 명시적으로 부정했는지
  나타냅니다.
- `changes`: 변경 대상·방향·수치·단위와 `direct` 또는
  `relative_to_current` 해석을 담습니다.
- `assumptions`: Qwen이 정규화에 가정이 있었다는 신호입니다. 문구 자체를
  신뢰해 노출하지 않고 Python이 검증된 `changes`와 `source_text`로 안전한
  해석 문장을 다시 만듭니다.
- `unresolved`: 값이나 단위처럼 확정하지 못한 항목을 제한된 코드로
  기록합니다. Qwen의 자유 문장을 사용자 답변에 그대로 노출하지 않습니다.

Python은 `intent`, 허용 필드, 원문 alias 근거, 원문 수치와 구조화 수치의
일치, `value_text`에 연결된 단일 변경 수치, 수치의 유한성, 단위와 방향을
다시 검증합니다.
`unresolved`가 하나라도 있으면 Tool을 실행하지 않고 해당 항목만 묻는 부분
재질문을 반환합니다. Qwen이 생성한 delta를 신뢰하거나 정규식으로 Tool을
결정하지 않으며, 검증을 통과한 의미 정보로 Python이 실제 Tool 인자를
계산합니다.

`전체`, `모두`, `모든`, `전부`, 독립된 `다`는 전체 범위 검증의 안전한
보조 신호로만 사용합니다. `말고`, `제외`, `필요 없고`, `보여주지 말고`가
함께 있으면 전체 조회로 확장하지 않고 원문에서 확인된 일부 필드만 조회합니다.
명확한 전체 범위인데 Qwen이 빈 일부 조회 또는 미지원 요청으로 반환한 경우에도
Python이 `lookup_all=true`로 보정합니다.

요청마다 다음 성능 지표를 한 줄의 `gaon_llm_metrics` 로그로 남깁니다.

- `chat_total_seconds`
- `llm_router_seconds`
- `tool_execution_seconds`
- `answer_format_seconds`
- `validation_seconds`
- `ollama_call_count`
- `load_duration`, `prompt_eval_duration`, `eval_duration`
- `prompt_eval_count`, `eval_count`
- `intent`, `lookup_all`, `requested_fields`, `excluded_scope`
- `validation_result`, `final_branch`, `actual_used_tools`

질문 원문과 추론 내용은 로그에 포함하지 않습니다. `keep_alive="5m"`는 연속
요청 시 모델 재로딩을 줄이는 메모리 유지 정책입니다. 모델이 GPU 메모리에
로드되어 있는 상태와 실제 토큰 생성으로 GPU가 연산 중인 상태는 다릅니다.

## FastAPI와 대시보드 실행

프로젝트 루트에서 백엔드와 프론트엔드를 각각 실행합니다.

```bash
source .venv/bin/activate
uvicorn backend.main:app --reload --port 8000
```

```bash
npm run dev
```

대시보드의 단일 우측 패널에서 `GA:ON AI`를 선택하면 채팅 화면으로 전환됩니다.
현재 선택한 100m 격자의 실제 `grid_id`만 문맥으로 사용하며, 250m/500m의
표시용 ID는 Tool에 전달하지 않습니다.

`POST /api/chat` 요청:

```json
{
  "message": "이 격자의 녹지율을 5%p 높이면 어떻게 돼?",
  "selected_grid_id": "11230_00001"
}
```

응답:

```json
{
  "answer": "사용자에게 표시할 최종 한국어 답변",
  "used_tools": ["run_simulation"],
  "tool_data": {},
  "warnings": [],
  "limitations": []
}
```

질문에 격자 ID가 명시되어 있으면 선택 격자보다 우선합니다. 두 문맥이 모두
없으면 Ollama를 호출하지 않고 100m 격자 선택을 안내합니다. 내부 추론,
Ollama 원본 응답과 stack trace는 API 및 브라우저 저장소로 전달하지 않습니다.
채팅 기록은 현재 브라우저 탭의 `sessionStorage`에 저장되며 새 대화 버튼으로
삭제할 수 있습니다.

구조화 라우터 결과는 내부 처리 계약이며 `POST /api/chat` 요청·응답 필드를
추가하거나 바꾸지 않습니다. 기존 `get_grid_data()`와 `run_simulation()`,
`POST /api/simulate`의 입력·반환 계약도 그대로 유지합니다.
적용한 `assumptions`는 별도 API 필드를 추가하지 않고 최종 `answer`에서
사용자에게 설명합니다. `unresolved`가 남은 부분 재질문은 `used_tools: []`로
기존 응답 구조 안에서 반환합니다.

## get_grid_data 입력과 결과

```python
get_grid_data(
    grid_id: str,
    fields: list[str] | None = None,
) -> dict
```

- `fields`를 생략하면 기존 동작과 같이 `green_ratio`,
  `impervious_ratio`를 조회합니다.
- `fields`가 있으면 요청한 필드만 조회합니다.
- 중복 필드는 첫 등장 순서대로 하나만 유지합니다.
- 명시적인 빈 배열은 실수로 전체 데이터가 노출되지 않도록 오류로 처리합니다.
- 미지원 필드는 비슷한 필드로 바꾸지 않으며 `unsupported_fields`와
  `available_fields`를 반환합니다.
- “전체 데이터”는 아래 18개 모델 입력 필드를 모두 `fields`로 전달합니다.
  `built_surface_ratio`는 현재 모델 입력에서 제거되어
  `predict_core.get_grid_features()`가 반환하지 않으므로 값을 복원하거나
  다른 필드로 대체하지 않습니다.

공통 반환 구조:

| 필드 | 의미 |
|---|---|
| `success` | 조회 성공 여부 |
| `grid_id`, `gu_name` | 대상 100m 격자와 자치구 |
| `requested_fields` | 중복 제거 후 실제 조회한 필드 순서 |
| `values` | 필드명과 `get_grid_features()` 원본 숫자값의 매핑 |
| `field_metadata` | 요청 필드별 정식 표시명·단위·비율 여부·확정 표시값. 최종 답변 생성용이며 브라우저에는 저장하지 않음 |
| `answer_prefix` | `grid_id`와 `gu_name`이 포함된 최종 답변 시작 문구 |
| `answer_template` | 실제 Tool 값으로 동적으로 만든 전체 조회용 한국어 표시문. 테스트 기대값을 하드코딩하지 않음 |
| `error` | 성공 시 `null`, 실패 시 사용자에게 전달할 오류 |

## 조회 가능 필드와 표시 규칙

표시명과 의미는 기존 `backend/models/feature_meta.json`을 기준으로 합니다.
비율 8개는 원본값에 100을 곱해 `%`로 표시하고, NDVI와 albedo는 무단위
원본값으로 표시합니다. `floor_area_ratio_proxy`는 단위가 정의되지 않은
proxy이므로 단위를 붙이거나 공식 용적률로 표현하지 않습니다.

| 필드 | 한국어 표시명 | 최종 표시 단위 | 주요 별칭 |
|---|---|---|---|
| `building_ratio` | 건물 바닥면적 비율 | `%` | 건물 비율, 건물밀도, 건폐율 |
| `avg_ground_floor_count` | 평균 지상층수 | `층` | 층수, 고층, 저층 |
| `max_ground_floor_count` | 최대 지상층수 | `층` | 최고층, 최대높이 |
| `floor_area_ratio_proxy` | 연면적비 proxy | 단위 없음 | 용적률, 연면적, 개발밀도 |
| `road_ratio` | 도로율 | `%` | 도로, 포장도로 |
| `zoning_residential_ratio` | 주거지역 비율 | `%` | 주거지역, 주거 |
| `zoning_commercial_ratio` | 상업지역 비율 | `%` | 상업지역, 상업 |
| `zoning_industrial_ratio` | 공업지역 비율 | `%` | 공업지역, 공장 |
| `zoning_green_ratio` | 녹지지역 비율 | `%` | 녹지지역, 그린벨트 |
| `ndvi` | 식생지수 | 무단위 | NDVI, 식생지수, 식생 |
| `green_ratio` | 녹지율 | `%` | 녹지율, 녹지 |
| `impervious_ratio` | 불투수면 비율 | `%` | 불투수율, 불투수면 |
| `nearest_park_distance_m` | 최근접 공원거리(m) | `m` | 공원까지 거리, 공원 거리 |
| `park_area_within_500m` | 500m내 공원면적(㎡) | `㎡` | 500m 내 공원 면적, 공원 면적 |
| `nearest_stream_distance_m` | 최근접 하천거리(m) | `m` | 하천까지 거리, 하천 거리 |
| `elevation_m` | 표고(m) | `m` | 고도, 표고 |
| `slope_deg` | 경사(도) | `°` | 경사도, 경사 |
| `albedo` | 표면 반사율 | 무단위 | 알베도, 반사율 |

조회 성공 답변은 `requested_fields`에 있는 지표만 한국어 표시명으로 작성해야
합니다. `grid_id`, `gu_name`, 각 원본값에서 정확히 변환·반올림한 수치와 단위를
검증하며, 요청하지 않은 지표나 Tool 결과에 없는 숫자가 있으면 실패합니다.

## run_simulation 입력

```python
run_simulation(
    grid_id: str,
    green_ratio_delta: float = 0,
    impervious_ratio_delta: float = 0,
    park_area_delta: float = 0,
) -> dict
```

| 입력 | 단위와 부호 | 기존 `predict()` changes 키 |
|---|---|---|
| `green_ratio_delta` | 0~1 원본 비율에 더할 부호 있는 delta. 5%p 증가는 `0.05` | `green_ratio` |
| `impervious_ratio_delta` | 0~1 원본 비율에 더할 부호 있는 delta. 5%p 감소는 `-0.05` | `impervious_ratio` |
| `park_area_delta` | 반경 500m 내 공원 면적 증가량(㎡). 음수 불가 | `park_area_within_500m` |

구조화 결과의 `intent`, `changes`, `unresolved`를 Python이 검증해 조회와
시뮬레이션을 결정합니다. 여러 줄 또는 한 문장에 여러 변경이 있으면 검증된
세 delta를 하나의 `run_simulation` 호출에 결합합니다. 예를 들어
`녹지율을 올려줘`처럼 변경량이 빠지면 현재 녹지율 조회로 바꾸지 않고,
`unresolved`에 변경량을 남겨 그 항목만 다시 묻습니다.

비율과 면적의 실제 Tool 인자는 다음 규칙으로 Python이 계산합니다.

| 사용자 표현 | 구조화 해석 | Python이 만드는 delta |
|---|---|---|
| 녹지율 `5%p` 증가 | `direct` | `green_ratio_delta=0.05` |
| 녹지율 `5%` 증가 | `direct` | 같은 수치의 `5%p`, 즉 `0.05`; 이 해석을 `assumptions`에 기록 |
| 현재 녹지율 대비 `5%` 증가 | `relative_to_current` | 현재 0~1 원본값 × `0.05` |
| 공원 면적 `1,000㎡` 증가 | 면적 변경 | `park_area_delta=1000` |

감소 방향은 계산된 비율 delta에 음수 부호를 붙입니다.
`relative_to_current`는 기존 격자 조회 경로에서 현재 원본 비율을 얻은 뒤
Python이 계산합니다. 공원 면적은 `㎡`, `m²`, `m2`처럼 제곱미터로 확정된
입력만 허용합니다. 다른 면적 단위나 단위 누락은 환산하지 않고
`unresolved`에 남겨 부분 재질문합니다.

기존 `/api/simulate`와 같이 녹지율 감소나 불투수율 증가도 실행합니다. 이 경우
Tool은 일반적인 열 저감 정책 방향과 반대인 시나리오라는 설명을 반환합니다.
비율 변경 후 값이 학습 범위를 벗어나도 미리 거부하지 않습니다.
`predict_core.predict()`가 기존 방식대로 학습 범위로 clip하며, 그 경고를
`warnings`에 그대로 반환합니다.

## run_simulation 결과

| 필드 | 의미 |
|---|---|
| `success` | Tool 실행 성공 여부 |
| `grid_id`, `gu_name` | 대상 격자와 자치구 |
| `requested_changes` | Tool이 `predict()`에 전달한 delta |
| `applied_changes` | `predict()`의 `changed_features`에 기록된 실제 변경 전·후 값 |
| `before_anomaly` | 변경 전 입력에 대한 모델 예측 anomaly |
| `after_anomaly` | 제약 적용 후 입력에 대한 모델 예측 anomaly |
| `delta_c` | 두 모델 예측의 차이인 모델 기준 예상 변화량 |
| `uncertainty_std` | 변경 후 절대 anomaly 예측의 트리 간 표준편차. `delta_c`의 오차범위가 아니다 |
| `delta_std` | 트리별 (변경 후 − 변경 전) 변화량의 표준편차. `delta_c`의 안정성 지표 |
| `warnings` | 학습 범위 clip 등 `predict()`가 반환한 경고 |
| `policy_direction_notes` | 일반적인 열 저감 방향과 반대인 입력에 대한 설명 |
| `interpretation_basis` | anomaly와 `delta_c`의 해석 기준 |
| `limitations` | 모델 결과를 해석할 때의 한계 |

`before_anomaly`와 `after_anomaly`는 실제 절대온도가 아니며 “기존 온도” 또는
“변경 후 실제 온도”로 해석하면 안 됩니다. `delta_c` 역시 모델 기준 예상
변화량이지 실제 정책의 인과효과가 아닙니다.

경고가 있으면 최종 답변은 학습 범위 밖 입력이 내부적으로 보정되었음을 설명합니다.
이때 요청한 입력값이 그대로 반영됐다고 표현하지 않고 `applied_changes`를 실제
반영값으로 사용합니다.

모델은 비용, 토지 확보, 공사기간, 행정 가능성을 반영하지 않습니다.

## 오류 동작

- 빈 질문, 격자 문맥 누락 또는 여러 격자 ID가 섞인 질문은 HTTP 400으로
  처리합니다.
- Ollama 연결 실패, 모델 미설치 또는 잘못된 모델 응답은 HTTP 503으로
  처리합니다.
- Ollama 응답 제한 시간을 넘으면 HTTP 504로 처리합니다.
- `grid_id`가 비어 있으면 `success: false`와 명확한 오류를 반환합니다.
- `fields: []`, 문자열 배열이 아닌 `fields`, 미지원 필드는 오류로 처리합니다.
  미지원 필드는 조회 가능한 18개 필드 목록도 함께 반환합니다.
- 일치하는 격자가 없거나 필수 모델 피처가 누락되면 해당 이유를 반환합니다.
- 변경값이 숫자가 아니거나 NaN·무한대이면 오류를 반환합니다.
- 일반 `%` 비율 변경은 `direct`로 해석해 같은 수치의 `%p`를 적용하고 그
  가정을 사용자에게 알립니다. “현재값 대비”처럼 상대 변화가 명시되면
  `relative_to_current`로 계산합니다.
- 공원 면적 변경은 제곱미터 단위만 처리합니다. 다른 단위 또는 필수 값이
  `unresolved`에 남으면 Tool을 호출하지 않고 누락된 부분만 다시 묻습니다.
- `park_area_delta`가 음수이면 기존 `/api/simulate`의 `park_area_m2 >= 0`
  규칙과 같이 오류를 반환합니다.
- 모델·feature metadata·데이터셋 등 필수 파일이 없으면 `missing_files`와
  오류를 반환합니다.
- 비율 방향과 학습 범위 이탈은 오류가 아니며 기존 `predict()`의 clip과
  `warnings` 처리를 따릅니다.
- 오류 시 서비스는 누락 값을 추측하지 않고 도구의 오류만 전달하도록
  제한됩니다.
- 최종 답변이 도구 원본값으로 만든 허용 문장과 일치하지 않으면 답변 후보를
  검증 실패로 표시하고 종료 코드 1로 끝납니다.
