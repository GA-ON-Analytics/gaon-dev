# GA:ON Ollama + Qwen Tool Calling 최소 PoC

사용자 질문에서 격자 ID를 추출한 Qwen이 `get_grid_data` 도구를 호출하고,
기존 `backend.ml.predict_core.get_grid_features()` 조회 결과만 이용해 한국어로
답하는 CLI PoC입니다.

이 PoC는 별도 CSV repository를 만들지 않으며 FastAPI, 프론트엔드, 시뮬레이션
로직을 연결하지 않습니다.

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

기본 테스트 질문을 실행합니다.

```bash
python -m backend.llm_poc.cli_test
```

다른 격자 질문도 위치 인자로 전달할 수 있습니다.

```bash
python -m backend.llm_poc.cli_test \
  "11230_00001 격자의 녹지율과 불투수율을 알려줘."
```

CLI는 다음 네 항목을 순서대로 출력합니다.

1. 호출된 도구명
2. 도구 인자
3. 도구 반환값
4. Qwen의 최종 한국어 답변

도구 반환값의 `green_ratio`와 `impervious_ratio`는 기존 데이터의 0~1 원본값을
유지합니다. 퍼센트 변환은 시스템 프롬프트의 지시에 따라 Qwen이 최종 답변에서만
수행합니다.

## 오류 동작

- `grid_id`가 비어 있으면 `success: false`와 명확한 오류를 반환합니다.
- 일치하는 격자가 없으면 조회 실패 이유를 반환합니다.
- 자치구명, 녹지율, 불투수율 또는 기존 모델 필수 피처가 누락되면
  `missing_fields`와 오류를 반환합니다.
- 오류 시 Qwen은 누락 값을 추측하지 않고 도구의 오류만 전달하도록 제한됩니다.
- 최종 답변이 도구 원본값으로 만든 허용 문장과 일치하지 않으면 답변 후보를
  검증 실패로 표시하고 종료 코드 1로 끝납니다.
