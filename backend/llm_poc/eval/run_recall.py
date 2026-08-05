"""문서 검색의 Recall@K를 잰다.

Recall@K = 정답이 든 청크가 상위 K개 안에 들어온 질문의 비율.
여기가 낮으면 프롬프트를 아무리 고쳐도 소용없다. LLM에게 정답이 없는 자료를
주고 답을 만들라고 하는 셈이기 때문이다.

실행:
    python -m backend.llm_poc.eval.run_recall
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

from backend.llm_poc.doc_search import (
    DEFAULT_TOP_K,
    DenseIndex,
    HybridIndex,
    KeywordIndex,
    SearchHit,
    build_embeddings,
    load_corpus,
)

QUESTION_PATH = Path(__file__).resolve().parent / "doc_search_questions.json"


def is_correct(hit: SearchHit, question: dict) -> bool:
    """정답 청크인지 판정한다.

    청크 경계는 청킹 파라미터를 바꾸면 움직인다. 그래서 청크 번호가 아니라
    (문서, 헤딩에 포함된 문자열)로 표시해 두었다.
    """

    if hit.chunk.doc != question["doc"]:
        return False
    return any(key in hit.chunk.heading_path for key in question["heading_any"])


def _rank_of(index: object, question: dict) -> tuple[int | None, float]:
    started = time.perf_counter()
    hits = index.search(question["question"], top_k=10)  # type: ignore[attr-defined]
    elapsed = time.perf_counter() - started
    rank = next(
        (i for i, hit in enumerate(hits, start=1) if is_correct(hit, question)),
        None,
    )
    return rank, elapsed


def _recall_at(k: int, ranks: list[int | None]) -> float:
    if not ranks:
        return 0.0
    return sum(1 for rank in ranks if rank is not None and rank <= k) / len(ranks)


def main() -> int:
    payload = json.loads(QUESTION_PATH.read_text(encoding="utf-8"))
    questions = payload["questions"]
    chunks = load_corpus()
    keyword_index = KeywordIndex(chunks)
    dense_index = DenseIndex(chunks, build_embeddings(chunks))
    indexes = {
        "낱말(BM25)": keyword_index,
        "임베딩(bge-m3)": dense_index,
        "하이브리드(RRF)": HybridIndex(keyword_index, dense_index),
    }

    results: dict[str, list[tuple[dict, int | None, float]]] = {}
    for name, index in indexes.items():
        results[name] = [
            (question, *_rank_of(index, question)) for question in questions
        ]

    header = f"{'id':>3} {'유형':<9}" + "".join(f"{name:>16}" for name in indexes)
    print(header)
    print("-" * len(header))
    for position, question in enumerate(questions):
        cells = ""
        for name in indexes:
            rank = results[name][position][1]
            text = "못 찾음" if rank is None else f"{rank}위"
            if rank is not None and rank <= DEFAULT_TOP_K:
                text += " O"
            else:
                text += " X"
            cells += f"{text:>16}"
        print(f"{question['id']:>3} {question['type']:<9}{cells}")
    print()

    print(f"{'검색 방식':<18}{'전체@4':>9}{'낱말형@4':>10}{'의미형@4':>10}{'전체@1':>9}{'평균ms':>9}")
    for name in indexes:
        rows = results[name]
        ranks = [rank for _, rank, _ in rows]
        keyword_ranks = [r for q, r, _ in rows if q["type"] == "keyword"]
        semantic_ranks = [r for q, r, _ in rows if q["type"] == "semantic"]
        avg_ms = sum(elapsed for _, _, elapsed in rows) / len(rows) * 1000
        print(
            f"{name:<18}{_recall_at(4, ranks):>9.2f}"
            f"{_recall_at(4, keyword_ranks):>10.2f}"
            f"{_recall_at(4, semantic_ranks):>10.2f}"
            f"{_recall_at(1, ranks):>9.2f}{avg_ms:>9.1f}"
        )
    print()
    for name in indexes:
        missed = [
            q["id"]
            for q, rank, _ in results[name]
            if rank is None or rank > DEFAULT_TOP_K
        ]
        print(f"{name:<18} 상위 4개 밖: {missed}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
