"""녹지율 '자체'가 한계인 격자를 찾는다.

슬라이더 한계는 두 가지 중 작은 쪽이다.
  own     = (녹지 학습범위 상한 0.8681 - 현재 녹지) * 100
  coupled = (현재 불투수 - 불투수 하한 0.0237) / 0.65 * 100
지금까지 본 격자는 전부 coupled가 묶는 쪽이었다. own이 묶으려면
녹지가 이미 높고(상한 가까움) 불투수는 넉넉해야 한다 — 둘이 반대 방향이라 드물다.
"""
import json
from pathlib import Path

from backend.ml import predict_core

DASH = Path("public/dashboard/100m")
COUPLE = 0.65
SLIDER_MAX = 40

_, _, _, ranges = predict_core._load()
lo_i = ranges["impervious_ratio"][0]
hi_g = ranges["green_ratio"][1]
print(f"녹지 상한 {hi_g:.4f} · 불투수 하한 {lo_i:.4f} · 슬라이더 max {SLIDER_MAX}\n")

own_bound, coupled_bound, no_limit, total = [], 0, 0, 0
for path in sorted(DASH.glob("*.geojson")):
    with path.open(encoding="utf-8") as f:
        for ft in json.load(f)["features"]:
            p = ft["properties"]
            g, i = p.get("green_ratio"), p.get("impervious_ratio")
            gid = p.get("grid_id")
            if g is None or i is None or not gid:
                continue
            total += 1
            own = (hi_g - float(g)) * 100
            coupled = ((float(i) - lo_i) / COUPLE) * 100
            limit = min(own, coupled)
            if limit >= SLIDER_MAX:
                no_limit += 1
            elif own < coupled:
                own_bound.append((gid, p.get("gu_name"), float(g), float(i), own, coupled))
            else:
                coupled_bound += 1

print(f"전체 {total:,}격자")
print(f"  한계 없음(둘 다 40 이상)   {no_limit:>7,} ({no_limit/total*100:.1f}%)")
print(f"  연동(불투수)이 묶음        {coupled_bound:>7,} ({coupled_bound/total*100:.1f}%)")
print(f"  ★ 녹지 자체가 묶음         {len(own_bound):>7,} ({len(own_bound)/total*100:.1f}%)")

if own_bound:
    own_bound.sort(key=lambda r: r[4])
    print(f"\n녹지 자체가 한계인 격자 — 여유가 가장 적은 12개")
    print(f"{'격자':<14}{'구':<8}{'녹지':>8}{'불투수':>8}{'녹지여유':>9}{'연동폭':>9}")
    for gid, gu, g, i, own, coupled in own_bound[:12]:
        print(f"{gid:<14}{str(gu):<8}{g*100:>7.1f}%{i*100:>7.1f}%"
              f"{own:>9.1f}{coupled:>9.1f}")
