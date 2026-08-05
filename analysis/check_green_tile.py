"""'녹지화 시 저감 가능'(green_delta_c) 타일이 격자끼리 비교 가능한 값인지 검증한다.

툴팁: "모든 격자에 같은 조건을 적용한 값이라, 격자끼리 '녹지화 여지'를 비교하는 용도예요."
그런데 학습범위 clip 때문에 일부 격자는 '같은 조건'을 다 받지 못한다. 덜 개입한 격자가
오히려 큰 저감으로 나오면 비교·순위가 뒤집힌다. 그게 실제로 일어나는지 잰다.
"""
import json
import random
from collections import Counter
from pathlib import Path

import numpy as np

from backend.llm_poc.tools import run_simulation

DASH = Path("public/dashboard/100m")
PER_DISTRICT = 20
SEED = 20260803
RECIPE = dict(green_ratio_delta=0.05, ndvi_delta=0.03, impervious_ratio_delta=-0.05)
REQUESTED = {"green_ratio": 0.05, "ndvi": 0.03, "impervious_ratio": -0.05}

random.seed(SEED)
rows = []
for path in sorted(DASH.glob("*.geojson")):
    with path.open(encoding="utf-8") as f:
        feats = json.load(f)["features"]
    have = [ft for ft in feats
            if ft["properties"].get("green_delta_c") is not None
            and ft["properties"].get("grid_id")]
    for ft in random.sample(have, min(PER_DISTRICT, len(have))):
        rows.append((str(ft["properties"]["grid_id"]), ft["properties"]))

clipped, clean = [], []
which = Counter()
shortfall = []

for grid_id, props in rows:
    sim = run_simulation(grid_id=grid_id, **RECIPE)
    if not sim.get("success"):
        continue
    cf = sim["changed_features"]
    # 요청량 대비 실제 반영량이 모자란 변수를 찾는다 (clip 경고가 없어도 잡힌다)
    partial = []
    for feature, want in REQUESTED.items():
        ba = cf.get(feature)
        got = (ba["after"] - ba["before"]) if ba else 0.0
        if abs(got) < abs(want) - 1e-9:
            partial.append(feature)
            shortfall.append((feature, abs(got) / abs(want)))
    bucket = clipped if partial else clean
    bucket.append(float(props["green_delta_c"]))
    for feature in partial:
        which[feature] += 1

c, k = np.array(clipped), np.array(clean)
total = len(c) + len(k)
print(f"\n표본 {total}격자 (25개 구 × 최대 {PER_DISTRICT})")
print(f"  요청대로 다 반영된 격자   {len(k):>4} ({len(k)/total*100:.0f}%)")
print(f"  일부만 반영된 격자(clip)  {len(c):>4} ({len(c)/total*100:.0f}%)")

print("\n어느 변수가 잘렸나")
for feature, n in which.most_common():
    print(f"  {feature:<20} {n}회")

print("\ngreen_delta_c 분포 비교  (음수 = 저감)")
for name, arr in (("전량 반영", k), ("일부만 반영", c)):
    print(f"  {name:<12} 중앙값 {np.median(arr):+.3f}   평균 {arr.mean():+.3f}"
          f"   최소 {arr.min():+.3f}   n={len(arr)}")

# 저감 상위 10%에 clip 격자가 얼마나 몰려 있나 = 순위 오염 정도
allv = np.concatenate([c, k])
flag = np.concatenate([np.ones_like(c), np.zeros_like(k)])
order = np.argsort(allv)              # 가장 큰 저감(음수)이 앞
for pct in (5, 10, 25):
    n = max(1, int(len(allv) * pct / 100))
    top = flag[order[:n]]
    print(f"\n저감 상위 {pct}% ({n}격자) 중 clip 격자: "
          f"{int(top.sum())}개 ({top.mean()*100:.0f}%)  ← 전체 평균 {len(c)/total*100:.0f}%")

if shortfall:
    by_feature = {}
    for feature, ratio in shortfall:
        by_feature.setdefault(feature, []).append(ratio)
    print("\n잘린 변수의 실제 반영 비율 (1.0 = 요청대로)")
    for feature, ratios in by_feature.items():
        r = np.array(ratios)
        print(f"  {feature:<20} 중앙값 {np.median(r):.2f}   최소 {r.min():.2f}   n={len(r)}")
