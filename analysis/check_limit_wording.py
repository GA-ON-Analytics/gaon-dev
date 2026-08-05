"""녹지 한계선이 '무엇의 한계'인지 확인한다 — 녹지 자체인가, 연동된 불투수인가."""
from backend.llm_poc.tools import run_simulation
from backend.ml import predict_core

COUPLE = 0.65
_, feats, static, ranges = predict_core._load()
lo_i, hi_i = ranges["impervious_ratio"]
lo_g, hi_g = ranges["green_ratio"]
print(f"학습범위  녹지 {lo_g:.4f}~{hi_g:.4f}   불투수 {lo_i:.4f}~{hi_i:.4f}\n")

for grid, test_tick in (("11170_01069", 34), ("11560_02332", 10)):
    row = static[static["grid_id"] == grid].iloc[0]
    g, i = float(row["green_ratio"]), float(row["impervious_ratio"])
    own = (hi_g - g) * 100
    coupled = ((i - lo_i) / COUPLE) * 100
    limit = min(own, coupled)
    who = "연동(불투수)" if coupled < own else "녹지 자체"

    sim = run_simulation(grid_id=grid, green_ratio_delta=test_tick / 100)
    cf = sim["changed_features"]
    ga = (cf["green_ratio"]["after"] - cf["green_ratio"]["before"]) * 100
    ia = (cf["impervious_ratio"]["after"] - cf["impervious_ratio"]["before"]) * 100

    print(f"[{grid}]  녹지 {g:.4f}  불투수 {i:.4f}")
    print(f"  녹지 자체 여유      {own:>7.2f} 눈금")
    print(f"  연동 허용 폭        {coupled:>7.2f} 눈금")
    print(f"  → 한계 {limit:.2f} (표시 {int(limit)})  ·  묶는 쪽: {who}")
    print(f"  녹지 +{test_tick} 요청 → 녹지 {ga:+.2f}%p / 불투수 {ia:+.2f}%p "
          f"(기대 연동 {-COUPLE*test_tick:+.2f}%p)")
    print(f"  녹지는 {'온전히 적용' if abs(ga - test_tick) < 1e-6 else '잘림'}, "
          f"불투수는 {'온전' if abs(ia + COUPLE*test_tick) < 1e-6 else '잘림'}\n")
