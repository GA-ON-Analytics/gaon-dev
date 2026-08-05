"""대시보드에 미리 계산돼 들어간 green_delta_c와 지금 시뮬레이터의 답이 같은지 검증한다.

상세 패널의 '녹지화 시 저감 가능' 타일은 GeoJSON에 구워진 green_delta_c를 보여주고,
바로 아래 '직접 시뮬레이션'은 같은 모델을 실시간 호출한다. 두 값이 다르면 사용자는
한 화면에서 모순된 숫자를 본다. 툴팁이 밝힌 레시피(녹지 +5%p · NDVI +0.03 · 불투수 -5%p)를
그대로 재현해 대조한다.
"""
import json
import random
import sys
from pathlib import Path

import numpy as np

from backend.llm_poc.tools import run_simulation

DASH = Path("public/dashboard/100m")
TIE_BAND_C = 0.132
PER_DISTRICT = 12
SEED = 20260803

# 툴팁이 밝힌 레시피. 세 변수를 모두 명시하므로 녹지↔불투수 자동 연동은 발동하지 않는다.
RECIPE = dict(green_ratio_delta=0.05, ndvi_delta=0.03, impervious_ratio_delta=-0.05)

files = sorted(DASH.glob("*.geojson"))
if not files:
    sys.exit(f"{DASH}에 100m geojson이 없다")

random.seed(SEED)
diffs, clipped, missing, fail = [], 0, 0, 0
worst = None
per_district = []

for path in files:
    with path.open(encoding="utf-8") as f:
        feats = json.load(f)["features"]
    have = [
        ft for ft in feats
        if ft["properties"].get("green_delta_c") is not None
        and ft["properties"].get("grid_id")
    ]
    missing += len(feats) - len(have)
    if not have:
        continue
    picked = random.sample(have, min(PER_DISTRICT, len(have)))
    local = []
    for ft in picked:
        props = ft["properties"]
        grid_id = str(props["grid_id"])
        stored = float(props["green_delta_c"])
        sim = run_simulation(grid_id=grid_id, **RECIPE)
        if not sim.get("success"):
            fail += 1
            continue
        d = sim["delta_c"] - stored
        diffs.append(d)
        local.append(d)
        if any("clip" in w for w in sim["warnings"]):
            clipped += 1
        if worst is None or abs(d) > abs(worst[3]):
            worst = (grid_id, stored, sim["delta_c"], d)
    if local:
        per_district.append((path.stem, float(np.median(local)), float(np.max(np.abs(local)))))

a = np.array(diffs)
print(f"\n비교 {len(a)}격자 · {len(files)}개 구 (실패 {fail} · green_delta_c 없는 격자 {missing})")
print(f"  차이 중앙값   {np.median(a):+.4f}℃")
print(f"  차이 평균     {a.mean():+.4f}℃")
print(f"  절대차 최대   {np.abs(a).max():.4f}℃")
print(f"  표준편차      {a.std(ddof=1):.4f}")
n_big = int((np.abs(a) >= TIE_BAND_C).sum())
print(f"  |차이| >= 동률밴드({TIE_BAND_C}℃): {n_big} / {len(a)} ({n_big / len(a) * 100:.0f}%)")
print(f"  clip 발생: {clipped} / {len(a)}")
print(f"  최대 불일치 격자: {worst[0]}  저장 {worst[1]:+.3f} vs 시뮬 {worst[2]:+.3f}"
      f"  (차이 {worst[3]:+.3f})")

print("\n구별 중앙값 차이 (상위 8)")
for name, med, mx in sorted(per_district, key=lambda r: -abs(r[1]))[:8]:
    print(f"  {name:<20} 중앙값 {med:+.4f}   절대최대 {mx:.4f}")
