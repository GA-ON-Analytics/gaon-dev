"""녹지 자체가 한계이면서 그 한계가 눈에 보이는(여유 5~35눈금) 격자 예시."""
import json
from pathlib import Path

from backend.llm_poc.tools import run_simulation
from backend.ml import predict_core

DASH = Path("public/dashboard/100m")
COUPLE, SLIDER_MAX = 0.65, 40
_, _, _, ranges = predict_core._load()
lo_i, hi_g = ranges["impervious_ratio"][0], ranges["green_ratio"][1]

picks = []
for path in sorted(DASH.glob("*.geojson")):
    with path.open(encoding="utf-8") as f:
        for ft in json.load(f)["features"]:
            p = ft["properties"]
            g, i, gid = p.get("green_ratio"), p.get("impervious_ratio"), p.get("grid_id")
            if g is None or i is None or not gid:
                continue
            own = (hi_g - float(g)) * 100
            coupled = ((float(i) - lo_i) / COUPLE) * 100
            if own < coupled and 5 <= own <= 35:
                picks.append((gid, p.get("gu_name"), float(g), float(i), own, coupled))

print(f"녹지 자체가 묶으면서 여유 5~35눈금인 격자: {len(picks):,}개\n")
picks.sort(key=lambda r: -r[4])
sample = picks[:3] + picks[len(picks) // 2:len(picks) // 2 + 3] + picks[-3:]

print(f"{'격자':<14}{'구':<8}{'녹지':>7}{'불투수':>8}{'녹지여유':>9}{'연동폭':>8}")
for gid, gu, g, i, own, coupled in sample:
    print(f"{gid:<14}{str(gu):<8}{g*100:>6.1f}%{i*100:>7.1f}%{own:>9.1f}{coupled:>8.1f}")

print("\n─ 실제 시뮬레이션으로 한계 확인 ─")
for gid, gu, g, i, own, coupled in sample[:2]:
    print(f"\n[{gid}] {gu}  녹지여유 {own:.1f}눈금 (연동폭 {coupled:.1f})")
    for tick in (int(own) - 1, int(own) + 1, int(own) + 5):
        if tick <= 0 or tick > SLIDER_MAX:
            continue
        sim = run_simulation(grid_id=gid, green_ratio_delta=tick / 100)
        cf = sim["changed_features"]
        ga = (cf["green_ratio"]["after"] - cf["green_ratio"]["before"]) * 100
        mark = "온전" if abs(ga - tick) < 1e-6 else "잘림"
        print(f"   녹지 +{tick:>2} 요청 → 실제 {ga:+.2f}%p  {mark}")
