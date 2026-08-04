"""measure_policies_result.json을 다시 읽어 clip 영향을 분리한다. 재측정 불필요."""
import json
from pathlib import Path

import numpy as np

TIE_BAND_C = 0.132
GREEN_TO_IMPERVIOUS = -0.65

with open(Path(__file__).resolve().parent / "measure_policies_result.json", encoding="utf-8") as f:
    data = json.load(f)
records = data["records"]
labels = [p[0] for p in data["policies"]]


def clipped(sim):
    """clip 경고 + 연동이 조용히 삼킨 clip까지 잡는다."""
    if any("clip" in w for w in sim["warnings"]):
        return True
    cf = sim.get("changed_features", {})
    if "green_ratio" in cf and "impervious_ratio" in cf:
        applied = cf["green_ratio"]["after"] - cf["green_ratio"]["before"]
        expected = applied * GREEN_TO_IMPERVIOUS
        actual = cf["impervious_ratio"]["after"] - cf["impervious_ratio"]["before"]
        return not np.isclose(actual, expected, atol=1e-4)
    return False


for label in labels:
    rows = [r for r in records if r["policy"] == label and r["sim"].get("success")]
    cl = [r for r in rows if clipped(r["sim"])]
    clean = [r for r in rows if not clipped(r["sim"])]

    def rank(rs):
        return sum(1 for r in rs if abs(r["sim"]["delta_c"]) >= TIE_BAND_C)

    none_n = sum(1 for r in rows if r["sim"]["direction_confidence"] is None)
    print(f"\n[{label}]  {len(rows)}격자")
    print(f"    clip 걸린 격자      {len(cl):>3}  ({len(cl)/len(rows)*100:.0f}%)")
    print(f"    순위가능 (전체)     {rank(rows):>3}  ({rank(rows)/len(rows)*100:.0f}%)")
    print(f"    └ 그중 clip 격자    {rank(cl):>3}")
    print(f"    순위가능 (clip제외) {rank(clean):>3} / {len(clean)}"
          f"  ({rank(clean)/max(len(clean),1)*100:.0f}%)   ← 진짜 비율")
    print(f"    conf=None(무반응)   {none_n:>3}")
    if clean:
        d = np.array([r["sim"]["delta_c"] for r in clean])
        print(f"    clip제외 중앙값 {np.median(d):+.3f}  최소 {d.min():+.3f}")
    if cl:
        d = np.array([r["sim"]["delta_c"] for r in cl])
        print(f"    clip격자 중앙값 {np.median(d):+.3f}  최소 {d.min():+.3f}")

print("\n=== 격자별 순위 개수 (clip 제외) ===")
grids = sorted({r["grid_id"] for r in records})
counts = []
for g in grids:
    n = sum(1 for r in records
            if r["grid_id"] == g and r["sim"].get("success")
            and not clipped(r["sim"])
            and abs(r["sim"]["delta_c"]) >= TIE_BAND_C)
    counts.append(n)
for k in range(len(labels) + 1):
    c = counts.count(k)
    print(f"  {k}개: {c:>3}격자 ({c / len(grids) * 100:>4.0f}%)")
    