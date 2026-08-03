"""GA:ON 서비스·모델 문서 검색용 청킹과 검색.

코퍼스는 ``backend/llm_poc/corpus/``의 마크다운 사본이다. 원본은 두 레포에
흩어져 있고 수시로 바뀌므로, 검색 대상이 조용히 움직이지 않도록 사본을 둔다.

청킹은 마크다운 헤딩 단위다. 헤딩이 곧 주제 경계이기 때문에 글자 수로만
자르면 문장 중간이 끊겨 뜻이 깨진다.
"""

from __future__ import annotations

import hashlib
import math
import os
import re
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any

CORPUS_DIR = Path(__file__).resolve().parent / "corpus"
# 청크 목표 크기. 아래는 붙이고 위는 나눈다.
MIN_CHUNK_CHARS = 300
MAX_CHUNK_CHARS = 600
# 발췌를 LLM에 넘길 때의 글자 예산.
#
# 개수로 끊으면 안 된다. 표 청크가 1,251자까지 나오므로 "상위 4개"가 4,115자가
# 되는 경우가 있고, 그러면 컨텍스트 4,096토큰 안에서 답변을 만들 자리가 없다.
MAX_CONTEXT_CHARS = 2400
DEFAULT_TOP_K = 4

_HEADING_PATTERN = re.compile(r"^(#{1,6})\s+(.*\S)\s*$")
_FENCE_PATTERN = re.compile(r"^\s*(```|~~~)")


@dataclass(frozen=True)
class DocChunk:
    """검색 단위 하나."""

    doc: str  # 파일 이름
    heading_path: str  # "문서 제목 > 절 > 소절"
    text: str  # 헤딩 경로를 포함한 본문
    char_count: int
    ordinal: int  # 문서 안에서 몇 번째 청크인가

    @property
    def chunk_id(self) -> str:
        # ★ ordinal이 없으면 유일하지 않다. 긴 절을 여러 조각으로 나누면 조각들이
        # 같은 heading_path를 공유해서, 112개 청크가 101개 id로 뭉개진다.
        # 검색 결과를 id로 합치는 곳(HybridIndex)에서 서로 다른 청크가 섞인다.
        return f"{self.doc}#{self.ordinal}#{self.heading_path}"


def _split_sections(markdown: str) -> list[tuple[str, str]]:
    """마크다운을 (헤딩 경로, 본문) 목록으로 나눈다.

    코드 펜스(```) 안의 ``#``은 헤딩이 아니라 주석이므로 무시한다.
    이걸 빼먹으면 파이썬 주석마다 새 절이 생긴다.
    """

    sections: list[tuple[str, str]] = []
    stack: list[str] = []  # 레벨별 헤딩 제목
    body: list[str] = []
    in_fence = False

    def flush() -> None:
        text = "\n".join(body).strip()
        if text:
            sections.append((" > ".join(stack), text))
        body.clear()

    for line in markdown.splitlines():
        if _FENCE_PATTERN.match(line):
            in_fence = not in_fence
            body.append(line)
            continue
        match = None if in_fence else _HEADING_PATTERN.match(line)
        if match is None:
            body.append(line)
            continue
        flush()
        level = len(match.group(1))
        title = match.group(2)
        del stack[level - 1 :]
        while len(stack) < level - 1:
            stack.append("")
        stack.append(title)
    flush()
    return [(path, text) for path, text in sections if text]


def _split_long_text(text: str, limit: int) -> list[str]:
    """긴 본문을 빈 줄 경계에서 나눈다. 한 문단이 통째로 크면 그대로 둔다."""

    paragraphs = [block for block in re.split(r"\n\s*\n", text) if block.strip()]
    pieces: list[str] = []
    buffer = ""
    for paragraph in paragraphs:
        candidate = f"{buffer}\n\n{paragraph}" if buffer else paragraph
        if len(candidate) <= limit or not buffer:
            buffer = candidate
        else:
            pieces.append(buffer)
            buffer = paragraph
    if buffer:
        pieces.append(buffer)
    return pieces


def chunk_markdown(doc_name: str, markdown: str) -> list[DocChunk]:
    """문서 하나를 청크 목록으로 만든다.

    작은 절은 뒤 절과 붙이고 큰 절은 문단 경계에서 나눈다. 붙일 때는 같은
    문서 안에서만 붙인다. 문서를 넘어 붙이면 검색 결과의 출처가 흐려진다.
    """

    chunks: list[DocChunk] = []
    pending_path: str | None = None
    pending_body = ""

    def emit(path: str, text: str) -> None:
        body = text.strip()
        if not body:
            return
        full = f"[{doc_name}] {path}\n{body}" if path else f"[{doc_name}]\n{body}"
        chunks.append(
            DocChunk(
                doc=doc_name,
                heading_path=path,
                text=full,
                char_count=len(full),
                ordinal=len(chunks),
            )
        )

    for path, body in _split_sections(markdown):
        if len(body) > MAX_CHUNK_CHARS:
            # 큰 절을 만나면 모아두던 작은 절부터 내보낸다.
            if pending_path is not None:
                emit(pending_path, pending_body)
                pending_path, pending_body = None, ""
            for piece in _split_long_text(body, MAX_CHUNK_CHARS):
                emit(path, piece)
            continue

        if pending_path is None:
            pending_path, pending_body = path, body
            continue

        merged = f"{pending_body}\n\n{body}"
        if len(merged) <= MAX_CHUNK_CHARS:
            # 경로는 먼저 나온 절 것을 유지한다. 합쳐진 청크의 대표 주제다.
            pending_body = merged
        else:
            emit(pending_path, pending_body)
            pending_path, pending_body = path, body

        if len(pending_body) >= MIN_CHUNK_CHARS:
            emit(pending_path, pending_body)
            pending_path, pending_body = None, ""

    if pending_path is not None:
        emit(pending_path, pending_body)
    return chunks


def load_corpus(corpus_dir: Path | None = None) -> list[DocChunk]:
    """코퍼스 전체를 청크 목록으로 읽는다."""

    directory = corpus_dir or CORPUS_DIR
    if not directory.is_dir():
        raise FileNotFoundError(f"코퍼스 디렉터리가 없습니다: {directory}")
    chunks: list[DocChunk] = []
    for path in sorted(directory.glob("*.md")):
        chunks.extend(
            chunk_markdown(path.name, path.read_text(encoding="utf-8"))
        )
    if not chunks:
        raise ValueError(f"코퍼스에서 청크를 만들지 못했습니다: {directory}")
    return chunks


# --- 1단계: 키워드 검색 (모델 0개, VRAM 0) ---------------------------------

_WORD_PATTERN = re.compile(r"[A-Za-z][A-Za-z0-9_]*|\d+(?:\.\d+)?|[가-힣]+")
# BM25 기본값. 문서가 111개뿐이라 튜닝 여지가 거의 없어 표준값을 쓴다.
_BM25_K1 = 1.5
_BM25_B = 0.75


def tokenize(text: str) -> list[str]:
    """한국어 형태소 분석기 없이 검색용 토큰을 만든다.

    한글은 조사가 붙어 ``녹지를``과 ``녹지가``가 다른 낱말이 된다. 형태소
    분석기를 넣으면 의존성이 늘어나므로, 한글 낱말은 **글자 2-gram**으로도
    쪼개 조사를 흡수한다.

        "녹지를"  →  녹지를, 녹지, 지를

    영문·숫자는 ``delta_c``·``NDVI``·``0.65``처럼 통째가 의미 단위라 그대로 둔다.
    """

    tokens: list[str] = []
    for word in _WORD_PATTERN.findall(text.lower()):
        tokens.append(word)
        if len(word) >= 2 and "가" <= word[0] <= "힣":
            tokens.extend(word[i : i + 2] for i in range(len(word) - 1))
    return tokens


@dataclass(frozen=True)
class SearchHit:
    chunk: DocChunk
    score: float


class KeywordIndex:
    """BM25 키워드 검색. 청크 111개라 전수 계산이 수 밀리초다."""

    def __init__(self, chunks: list[DocChunk]) -> None:
        if not chunks:
            raise ValueError("빈 코퍼스로 색인을 만들 수 없습니다.")
        self.chunks = chunks
        self._token_counts = [Counter(tokenize(chunk.text)) for chunk in chunks]
        self._lengths = [sum(counts.values()) for counts in self._token_counts]
        self._avg_length = sum(self._lengths) / len(self._lengths)
        document_frequency: Counter[str] = Counter()
        for counts in self._token_counts:
            document_frequency.update(counts.keys())
        total = len(chunks)
        # 흔한 토큰일수록 가중치를 낮춘다. "격자"는 거의 모든 청크에 있어
        # 변별력이 없고, "GREEN_TO_IMPERVIOUS"는 한두 청크에만 있어 결정적이다.
        self._idf = {
            token: math.log(1 + (total - freq + 0.5) / (freq + 0.5))
            for token, freq in document_frequency.items()
        }

    def search(self, query: str, top_k: int = DEFAULT_TOP_K) -> list[SearchHit]:
        query_tokens = tokenize(query)
        if not query_tokens:
            return []
        scored: list[SearchHit] = []
        for index, counts in enumerate(self._token_counts):
            length = self._lengths[index] or 1
            score = 0.0
            for token in query_tokens:
                frequency = counts.get(token)
                if not frequency:
                    continue
                idf = self._idf.get(token, 0.0)
                denominator = frequency + _BM25_K1 * (
                    1 - _BM25_B + _BM25_B * length / self._avg_length
                )
                score += idf * frequency * (_BM25_K1 + 1) / denominator
            if score > 0:
                scored.append(SearchHit(self.chunks[index], score))
        scored.sort(key=lambda hit: hit.score, reverse=True)
        return scored[:top_k]


# --- 3단계: 밀집 검색(임베딩) + 하이브리드 -------------------------------

EMBED_MODEL = os.getenv("GAON_EMBED_MODEL") or "bge-m3"
EMBEDDING_CACHE = Path(__file__).resolve().parent / "corpus_embeddings.npz"
# RRF(Reciprocal Rank Fusion) 상수. 원 논문 권장값이며 튜닝하지 않는다.
# 질문이 20개뿐이라 여기를 만지면 평가셋에 과적합된다.
_RRF_K = 60


def corpus_fingerprint(chunks: list[DocChunk]) -> str:
    """코퍼스 내용의 지문. 문서가 바뀌면 임베딩 캐시를 자동으로 무효화한다."""

    digest = hashlib.sha256()
    digest.update(EMBED_MODEL.encode("utf-8"))
    for chunk in chunks:
        digest.update(chunk.text.encode("utf-8"))
    return digest.hexdigest()


def _embed(texts: list[str], client: Any | None = None) -> Any:
    import numpy as np
    import ollama

    active = client or ollama.Client()
    vectors = active.embed(model=EMBED_MODEL, input=texts)["embeddings"]
    matrix = np.asarray(vectors, dtype="float32")
    # 코사인 유사도를 내적 한 번으로 끝내기 위해 미리 단위벡터로 만든다.
    norms = np.linalg.norm(matrix, axis=1, keepdims=True)
    norms[norms == 0] = 1.0
    return matrix / norms


def build_embeddings(
    chunks: list[DocChunk],
    client: Any | None = None,
    cache_path: Path | None = None,
) -> Any:
    """청크 임베딩을 만들고 ``.npz``로 캐시한다.

    벡터DB를 쓰지 않는 이유: 청크가 112개뿐이라 전수 코사인이 수십 마이크로초다.
    임베딩 호출(수십 밀리초)이 1000배 느리므로 검색 자료구조를 바꿔 얻을 게 없다.
    ANN이 이기는 건 10만~100만 벡터부터고, 그 아래에서는 근사라서 더 부정확하다.
    """

    import numpy as np

    path = cache_path or EMBEDDING_CACHE
    fingerprint = corpus_fingerprint(chunks)
    if path.exists():
        cached = np.load(path, allow_pickle=False)
        if str(cached["fingerprint"]) == fingerprint:
            return cached["vectors"]
    vectors = _embed([chunk.text for chunk in chunks], client=client)
    np.savez_compressed(path, vectors=vectors, fingerprint=fingerprint)
    return vectors


class DenseIndex:
    """임베딩 코사인 유사도 검색. 전수 계산이라 근사 오차가 없다."""

    def __init__(
        self,
        chunks: list[DocChunk],
        vectors: Any,
        client: Any | None = None,
    ) -> None:
        if len(chunks) != len(vectors):
            raise ValueError("청크 수와 임베딩 수가 다릅니다.")
        self.chunks = chunks
        self.vectors = vectors
        self._client = client

    def search(self, query: str, top_k: int = DEFAULT_TOP_K) -> list[SearchHit]:
        query_vector = _embed([query], client=self._client)[0]
        scores = self.vectors @ query_vector  # 단위벡터라 내적 = 코사인
        order = scores.argsort()[::-1][:top_k]
        return [SearchHit(self.chunks[i], float(scores[i])) for i in order]


class HybridIndex:
    """낱말 검색과 임베딩 검색을 RRF로 합친다.

    낱말 검색은 ``monotonic_cst``·``delta_std`` 같은 고유명사에 강하고, 임베딩은
    "구역 크기"와 "100m"처럼 글자가 다른 같은 뜻에 강하다. 서로 못 하는 쪽을
    메운다.

    점수를 그대로 더하지 않고 **순위**로 합치는 이유: BM25 점수는 0~35, 코사인은
    -1~1로 눈금이 전혀 달라 그냥 더하면 한쪽이 묻힌다. 정규화해서 가중합할 수도
    있지만 그 가중치를 질문 20개로 맞추면 평가셋에 과적합된다. RRF는 맞출
    파라미터가 없다.
    """

    def __init__(self, keyword: KeywordIndex, dense: DenseIndex) -> None:
        self.keyword = keyword
        self.dense = dense

    def search(self, query: str, top_k: int = DEFAULT_TOP_K) -> list[SearchHit]:
        pool = max(top_k * 5, 20)
        fused: dict[str, float] = {}
        chunk_by_id: dict[str, DocChunk] = {}
        for hits in (
            self.keyword.search(query, top_k=pool),
            self.dense.search(query, top_k=pool),
        ):
            for rank, hit in enumerate(hits, start=1):
                key = hit.chunk.chunk_id
                chunk_by_id[key] = hit.chunk
                fused[key] = fused.get(key, 0.0) + 1.0 / (_RRF_K + rank)
        ranked = sorted(fused.items(), key=lambda item: item[1], reverse=True)
        return [SearchHit(chunk_by_id[key], score) for key, score in ranked[:top_k]]


def fit_context_budget(
    hits: list[SearchHit],
    max_chars: int = MAX_CONTEXT_CHARS,
) -> list[SearchHit]:
    """글자 예산 안에 들어가는 발췌만 남긴다.

    "상위 4개"로 끊으면 표 청크가 걸렸을 때 4,115자가 되어 컨텍스트에서
    답변을 만들 자리가 사라진다. 개수가 아니라 글자 수로 끊는다.
    """

    kept: list[SearchHit] = []
    used = 0
    for hit in hits:
        if kept and used + hit.chunk.char_count > max_chars:
            break
        kept.append(hit)
        used += hit.chunk.char_count
    return kept
