# analysis — 근거 측정·조사 스크립트

이슈 [#35](https://github.com/GA-ON-Analytics/gaon-dev/issues/35)와 대시보드 문서에 적힌 숫자를
**직접 재현**하기 위한 스크립트다. 주장만 남기면 다음 사람이 검증할 수 없다.

## 실행 방법

**레포 루트에서** 모듈로 실행한다. `import backend`와 `Path("public/...")`가
루트를 기준으로 하기 때문이다.

```bash
.\.venv\Scripts\python.exe -m analysis.measure_policies
```

`python analysis/measure_policies.py`로 실행하면 `import backend`가 깨진다.

---

## ③ 정책 우선순위 추천의 근거

| 스크립트 | 무엇을 확인하나 |
|---|---|
| `measure_policies` | 정책 4개의 `delta_c`를 격자 60개에서 측정. **결과가 `measure_policies_result.json`(240건)이고 이슈의 모든 ③ 숫자가 여기서 나온다** |
| `analyze_clip` | 위 240건을 다시 읽어 clip 영향을 분리. 순위 등재율 **녹지 60% · 식생 67% · 불투수 10% · 쿨루프 2%**와 격자별 분포(0개 18% · 1개 35% · 2개 37% · 3개 10%)를 재현 |
| `check_dispatch_guard` | intent 표가 어긋나면 import가 실제로 터지는지 |
| `check_prompt_budget` | 라우터 프롬프트가 컨텍스트 4,096 중 얼마를 쓰는지 |

> **"왜 순위표가 아니라 문장이냐"**에 답하려면 `analyze_clip`을 돌려 보면 된다.
> 4개가 모두 순위에 오르는 격자가 0%다.

## ④ 문서 검색 RAG의 근거

| 스크립트 | 무엇을 확인하나 |
|---|---|
| `bench_vectordb` | numpy 전수 vs FAISS vs Chroma. **정확도는 동일하고 Chroma가 45배 느리다**는 것과, 규모를 키웠을 때 언제 역전되는지 |

`bench_vectordb`는 `faiss-cpu`·`chromadb`가 필요하다. **앱 `.venv`에 설치하지 말 것** —
numpy 등 공용 의존성이 올라가 `predict_core`가 깨질 수 있다. 별도 venv에서 돌린다.

```bash
python -m venv bench && bench\Scripts\pip install numpy faiss-cpu chromadb
bench\Scripts\python analysis\bench_vectordb.py
```

같이 둔 `bench_data.npz`·`bench_meta.json`은 그때의 청크·질문 벡터라 ollama 없이도 재현된다.

Recall@4 측정은 여기가 아니라 `backend/llm_poc/eval/run_recall.py`에 있다.

## clip·연동 조사 (이슈 보류 목록의 근거)

| 스크립트 | 무엇을 확인하나 |
|---|---|
| `check_consistency` | 대시보드에 미리 계산된 `green_delta_c`와 지금 시뮬레이션 결과가 맞는지 |
| `check_priority_contamination` | clip으로 오염된 `green_delta_c`가 `priority_score`에 얼마나 섞였는지 (**보류 #1**) |
| `check_green_tile` | '녹지화 시 저감 가능' 타일의 "격자끼리 비교" 문구가 참인지 (**보류 #1**) |
| `check_batch_clip` · `check_batch_api` | 배치 시뮬레이션이 서빙과 같은 clip 기준인지 (**보류 #3**) |
| `check_desync` | 연동이 clip에 걸릴 때 녹지가 역산돼 줄어드는지 |
| `check_green_bound_grids` · `check_green_bound_sample` | 녹지율 '자체'가 한계인 격자 찾기 |

## 슬라이더·문구 조사

| 스크립트 | 무엇을 확인하나 |
|---|---|
| `check_slider_bounds` | 슬라이더 최대값에서 몇 %의 격자가 clip에 걸리는지 |
| `check_limit_truth` | 슬라이더 한계선이 실제 clip 지점과 맞는지 |
| `check_limit_wording` | 녹지 한계선이 '무엇의 한계'인지 |
| `check_warnings` | clip 경고 문구가 사람이 읽을 수 있는지 |
| `check_649` | 격자 `11170_00649`의 한계 표기(내림)와 적용 표기(반올림) 불일치 |

---

## 주의

- 대부분 **모델·CSV를 로드**한다. 첫 실행은 10초 이상 걸린다.
- `measure_policies`는 격자 60개 × 정책 4개라 약 23초.
- 측정 결과 파일(`measure_policies_result.json`)을 **다시 만들면 이슈의 숫자와 달라질 수 있다.**
  모델을 재학습했다면 그게 정상이고, 그때는 이슈 숫자도 함께 고쳐야 한다.
