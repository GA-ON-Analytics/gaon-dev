"""벡터 저장 방식 비교 — 지금 numpy 구조 vs FAISS vs Chroma.

두 가지를 잰다.
  1) 실제 코퍼스 112조각에서 정확도와 속도
  2) 규모를 키우면 언제 역전되는가

밀집 검색만 비교한다. 운영은 낱말+임베딩 하이브리드지만, 벡터 저장 방식이
바꾸는 것은 밀집 부분뿐이다.
"""

import json
import time
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
data = np.load(HERE / "bench_data.npz")
meta = json.loads((HERE / "bench_meta.json").read_text(encoding="utf-8"))
CHUNK_VECS = data["chunk_vecs"].astype("float32")
QUERY_VECS = data["query_vecs"].astype("float32")
CHUNKS = meta["chunks"]
QUESTIONS = meta["questions"]
TOP_K = 4


def is_correct(chunk, question):
    if chunk["doc"] != question["doc"]:
        return False
    return any(key in chunk["heading_path"] for key in question["heading_any"])


def recall_at(indices_per_question, k=TOP_K):
    hit = 0
    for question, indices in zip(QUESTIONS, indices_per_question):
        if any(is_correct(CHUNKS[i], question) for i in indices[:k]):
            hit += 1
    return hit / len(QUESTIONS)


def timed(fn, repeat=50):
    fn()  # 워밍업
    started = time.perf_counter()
    for _ in range(repeat):
        fn()
    return (time.perf_counter() - started) / repeat * 1000


print("=" * 78)
print("1) 실제 코퍼스 112조각 — 정확도와 속도")
print("=" * 78)

results = []

# --- numpy 전수 계산 (지금 구조) ---
build_started = time.perf_counter()
matrix = CHUNK_VECS  # 이미 단위벡터
build_ms = (time.perf_counter() - build_started) * 1000
idx_numpy = [(matrix @ q).argsort()[::-1][:TOP_K] for q in QUERY_VECS]
query_ms = timed(lambda: (matrix @ QUERY_VECS[0]).argsort()[::-1][:TOP_K])
results.append(("numpy 전수(현재)", build_ms, query_ms, recall_at(idx_numpy), 0))

# --- FAISS IndexFlatIP (정확) ---
import faiss

build_started = time.perf_counter()
flat = faiss.IndexFlatIP(CHUNK_VECS.shape[1])
flat.add(CHUNK_VECS)
build_ms = (time.perf_counter() - build_started) * 1000
idx_flat = [flat.search(q.reshape(1, -1), TOP_K)[1][0] for q in QUERY_VECS]
query_ms = timed(lambda: flat.search(QUERY_VECS[0].reshape(1, -1), TOP_K))
results.append(("FAISS Flat(정확)", build_ms, query_ms, recall_at(idx_flat), 1))

# --- FAISS HNSW (근사) ---
build_started = time.perf_counter()
hnsw = faiss.IndexHNSWFlat(CHUNK_VECS.shape[1], 32, faiss.METRIC_INNER_PRODUCT)
hnsw.add(CHUNK_VECS)
build_ms = (time.perf_counter() - build_started) * 1000
idx_hnsw = [hnsw.search(q.reshape(1, -1), TOP_K)[1][0] for q in QUERY_VECS]
query_ms = timed(lambda: hnsw.search(QUERY_VECS[0].reshape(1, -1), TOP_K))
results.append(("FAISS HNSW(근사)", build_ms, query_ms, recall_at(idx_hnsw), 1))

# --- Chroma (hnswlib 기반, 근사) ---
import chromadb

build_started = time.perf_counter()
client = chromadb.EphemeralClient()
col = client.create_collection("bench", metadata={"hnsw:space": "cosine"})
col.add(
    ids=[str(i) for i in range(len(CHUNKS))],
    embeddings=CHUNK_VECS.tolist(),
)
build_ms = (time.perf_counter() - build_started) * 1000
idx_chroma = [
    [int(x) for x in col.query(query_embeddings=[q.tolist()], n_results=TOP_K)["ids"][0]]
    for q in QUERY_VECS
]
query_ms = timed(
    lambda: col.query(query_embeddings=[QUERY_VECS[0].tolist()], n_results=TOP_K),
    repeat=20,
)
results.append(("Chroma(근사)", build_ms, query_ms, recall_at(idx_chroma), 1))

print(f"{'방식':<20}{'색인ms':>9}{'검색ms':>9}{'Recall@4':>10}{'새 의존성':>10}")
for name, b, q, r, dep in results:
    print(f"{name:<20}{b:>9.2f}{q:>9.3f}{r:>10.2f}{dep:>10}")

print()
print("상위 4개가 numpy와 완전히 같은가 (근사 손실 확인)")
for name, idx in (("FAISS Flat", idx_flat), ("FAISS HNSW", idx_hnsw), ("Chroma", idx_chroma)):
    same = sum(1 for a, b in zip(idx_numpy, idx) if list(a) == list(b))
    print(f"  {name:<12} {same}/20 질문에서 동일")

print()
print("=" * 78)
print("2) 규모를 키우면 언제 역전되는가  (임의 벡터, 1024차원)")
print("=" * 78)
print(f"{'벡터 수':>10}{'numpy ms':>11}{'HNSW ms':>10}{'색인 ms':>10}{'메모리MB':>10}  판정")

rng = np.random.default_rng(0)
for n in (112, 1_000, 10_000, 50_000, 200_000):
    vecs = rng.standard_normal((n, 1024), dtype="float32")
    vecs /= np.linalg.norm(vecs, axis=1, keepdims=True)
    q = vecs[0]
    numpy_ms = timed(lambda: (vecs @ q).argsort()[::-1][:TOP_K], repeat=10)
    started = time.perf_counter()
    index = faiss.IndexHNSWFlat(1024, 32, faiss.METRIC_INNER_PRODUCT)
    index.add(vecs)
    build_ms = (time.perf_counter() - started) * 1000
    hnsw_ms = timed(lambda: index.search(q.reshape(1, -1), TOP_K), repeat=10)
    mb = vecs.nbytes / 1e6
    verdict = "numpy 우세" if numpy_ms <= hnsw_ms else f"HNSW {numpy_ms / hnsw_ms:.0f}배 빠름"
    print(f"{n:>10,}{numpy_ms:>11.2f}{hnsw_ms:>10.3f}{build_ms:>10.0f}{mb:>10.0f}  {verdict}")
    del vecs, index
