# GA:ON Ollama + Qwen Tool Calling PoC

Ollama의 `qwen3:4b`가 사용자 질문에 따라 다음 도구를 선택하는 CLI PoC입니다.

- `get_grid_data`: 현재 격자의 녹지율과 불투수율 조회
- `run_simulation`: 100m 격자의 정책 변경값을 기존 머신러닝 모델에 적용해 재예측

조회는 `backend.ml.predict_core.get_grid_features()`, 시뮬레이션은
`backend.ml.predict_core.predict()`를 직접 재사용합니다. 별도 CSV repository나
예측 계산식을 만들지 않으며 FastAPI와 프론트엔드는 연결하지 않습니다.

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

## 실행

조회와 시뮬레이션 기본 E2E 질문을 모두 실행합니다.

```bash
python -m backend.llm_poc.cli_test
```

단일 질문도 위치 인자로 전달할 수 있습니다.

```bash
python -m backend.llm_poc.cli_test \
  "11230_00001 격자의 녹지율과 불투수율을 알려줘."

python -m backend.llm_poc.cli_test \
  "11230_00001 격자의 녹지율을 5%p 높이면 모델 기준 예상 변화량이 어떻게 돼?"
```

CLI는 각 질문에 대해 다음 항목을 출력하고 검증합니다.

1. 사용자 질문
2. 첫 번째 `message.thinking`과 `message.content`
3. 호출된 도구명과 인자
4. 도구 반환값
5. 최종 `message.thinking`과 `message.content`
6. 검증된 최종 한국어 답변
7. 전체 실행의 최종 종료 코드

도구 반환값의 `green_ratio`와 `impervious_ratio`는 기존 데이터의 0~1 원본값을
유지합니다. 퍼센트 변환은 시스템 프롬프트의 지시에 따라 Qwen이 최종 답변에서만
수행합니다.

Ollama 호출은 `think=True`를 사용합니다. 추론은 `message.thinking`으로 분리해
출력만 하며, 최종 답변과 숫자 검증은 `message.content`만 대상으로 합니다.
최종 `message.content`에 `<think>` 또는 `</think>`가 있으면 실패합니다.

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
| `uncertainty_std` | 모델 트리별 시나리오 예측의 표준편차 |
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

- `grid_id`가 비어 있으면 `success: false`와 명확한 오류를 반환합니다.
- 일치하는 격자가 없거나 필수 모델 피처가 누락되면 해당 이유를 반환합니다.
- 변경값이 숫자가 아니거나 NaN·무한대이면 오류를 반환합니다.
- `park_area_delta`가 음수이면 기존 `/api/simulate`의 `park_area_m2 >= 0`
  규칙과 같이 오류를 반환합니다.
- 모델·feature metadata·데이터셋 등 필수 파일이 없으면 `missing_files`와
  오류를 반환합니다.
- 비율 방향과 학습 범위 이탈은 오류가 아니며 기존 `predict()`의 clip과
  `warnings` 처리를 따릅니다.
- 오류 시 Qwen은 누락 값을 추측하지 않고 도구의 오류만 전달하도록 제한됩니다.
- 최종 답변이 도구 원본값으로 만든 허용 문장과 일치하지 않으면 답변 후보를
  검증 실패로 표시하고 종료 코드 1로 끝납니다.
