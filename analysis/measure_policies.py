"""정책 후보 4개의 delta_c를 여러 격자에서 재서 '순위를 매길 수 있는지' 판정한다.

③ 정책 추천을 설계하기 전에 반드시 확인해야 하는 것:
  - 각 정책의 효과가 추정오차(0.132℃)보다 큰 격자가 몇 %인가
  - 순위표에 실제로 올릴 수 있는 정책이 몇 개인가
"""
import json
import random
import sys
import time
from collections import Counter

import numpy as np
import pandas as pd

from backend.llm_poc.tools import run_simulation

CSV = "backend/data/processed/seoul_grid_dataset.csv"
TIE_BAND_C = 0.132          # 부트스트랩 8회로 측정한 delta_c 추정오차
MIN_DIRECTION_CONFIDENCE = 0.6
SAMPLE_SIZE = 60
SEED = 20260802

POLICIES = [
    ("녹지 확대",      "green_ratio_delta",      0.05),
    ("불투수면 저감",  "impervious_ratio_delta", -0.05),
    ("식생 활력 개선", "ndvi_delta",             0.05),
    ("쿨루프(알베도)", "albedo_delta",           0.02),
]

random.seed(SEED)
grid_ids = pd.read_csv(CSV, usecols=["grid_id"])["grid_id"].astype(str).tolist()
sample = random.sample(grid_ids, SAMPLE_SIZE)
print(f"격자 {len(grid_ids):,}개 중 {SAMPLE_SIZE}개 표본 (seed={SEED})\n")

records = []          # 격자ID + 결과 원본을 그대로 보관한다

started = time.perf_counter()
for grid_id in sample:
    for label, arg, value in POLICIES:
        sim = run_simulation(grid_id=grid_id, **{arg: value})
        records.append({"grid_id": grid_id, "policy": label,
                        "arg": arg, "value": value, "sim": sim})
elapsed = time.perf_counter() - started

OUT = "measure_policies_result.json"
with open(OUT, "w", encoding="utf-8") as f:
    json.dump({"seed": SEED, "sample_size": SAMPLE_SIZE,
               "tie_band_c": TIE_BAND_C, "policies": POLICIES,
               "elapsed_sec": round(elapsed, 1), "records": records},
              f, ensure_ascii=False, indent=2)
print(f"원본 {len(records)}건 저장 → {OUT}\n")


def ok(label):
    """해당 정책에서 성공한 결과만 (격자 순서 유지)"""
    return [r for r in records if r["policy"] == label and r["sim"].get("success")]

print(f"시뮬레이션 {SAMPLE_SIZE * len(POLICIES)}회 / {elapsed:.1f}초 "
      f"({elapsed / (SAMPLE_SIZE * len(POLICIES)) * 1000:.0f}ms per call)\n")

hdr = f"{'정책':<16}{'중앙값':>9}{'평균':>9}{'최소':>9}{'최대':>9}{'순위가능':>10}{'방향OK':>9}"
print(hdr)
print("-" * len(hdr.encode("utf-8")) // 2 * "-" if False else "-" * 72)

for label, arg, value in POLICIES:
    rows = ok(label)
    if not rows:
        print(f"{label:<16}  전부 실패")
        continue
    d = np.array([r["sim"]["delta_c"] for r in rows])
    rankable = np.mean(np.abs(d) >= TIE_BAND_C) * 100
    confs = [r["sim"]["direction_confidence"] for r in rows
             if r["sim"].get("direction_confidence") is not None]
    conf_ok = (np.mean(np.array(confs) >= MIN_DIRECTION_CONFIDENCE) * 100
               if confs else float("nan"))
    print(f"{label:<16}{np.median(d):>+9.3f}{d.mean():>+9.3f}"
          f"{d.min():>+9.3f}{d.max():>+9.3f}{rankable:>9.0f}%{conf_ok:>8.0f}%")

print(f"\n동률 밴드 {TIE_BAND_C}℃ 기준. '순위가능' = |delta_c| >= 밴드인 격자 비율")
print(f"'방향OK' = direction_confidence >= {MIN_DIRECTION_CONFIDENCE}인 격자 비율")

print("\n=== 격자별로 몇 개 정책이 순위에 오르나 ===")
counts = []
for grid_id in sample:
    n = sum(1 for r in records
            if r["grid_id"] == grid_id and r["sim"].get("success")
            and abs(r["sim"]["delta_c"]) >= TIE_BAND_C)
    counts.append(n)
for k in range(len(POLICIES) + 1):
    c = counts.count(k)
    print(f"  {k}개: {c:>3}격자 ({c / SAMPLE_SIZE * 100:>4.0f}%)")

fails = sum(1 for r in records if not r["sim"].get("success"))
warns = sum(1 for r in records if r["sim"].get("warnings"))
print(f"\n실패 {fails}회 / 경고 발생 {warns}회")

# ── 진단 A: 방향 확신도가 상수인가 ──────────────────────────
confs = sorted({r["sim"].get("direction_confidence")
                for r in records if r["sim"].get("success")},
               key=lambda x: (x is None, x))
print(f"\n=== direction_confidence 고윳값 ({len(confs)}종) ===\n  {confs}")

# ── 진단 B: 최대 냉각 격자에서 실제로 무엇이 바뀌었나 ──────────
print("\n=== 정책별 최대 냉각 격자 ===")
for label, _, _ in POLICIES:
    rows = ok(label)
    if not rows:
        continue
    best = min(rows, key=lambda r: r["sim"]["delta_c"])
    sim = best["sim"]
    print(f"\n[{label}] {best['grid_id']} ({sim.get('gu_name')})  "
          f"delta_c={sim['delta_c']:+.3f}")
    for feat, ba in sim["changed_features"].items():
        auto = " (자동연동)" if feat in sim.get("auto_applied_changes", {}) else ""
        print(f"     {feat:<22} {ba['before']:.4f} → {ba['after']:.4f}{auto}")
    for w in sim["warnings"]:
        print(f"     ! {w}")

# ── 진단 C: 경고인가 안내문인가 ────────────────────────────
def warn_kind(text):
    if "연동해" in text:          return "연동 안내(경고 아님)"
    if "연동이 반영되지" in text:  return "연동 미반영"
    if "clip" in text:            return "학습범위 clip"
    return text[:20]

print("\n=== 경고 문구 분류 ===")
for label, _, _ in POLICIES:
    kinds = Counter(warn_kind(w) for r in ok(label) for w in r["sim"]["warnings"])
    if not kinds:
        continue
    print(f"\n[{label}] 총 {sum(kinds.values())}건")
    for kind, n in kinds.most_common():
        print(f"     {n:>3}회  {kind}")
