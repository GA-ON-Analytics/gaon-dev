"""슬라이더에 그린 한계선이 실제 clip 지점과 맞는지 검증한다.

프론트는 한계를 이렇게 계산한다.
  녹지(연동 ON): min(hi_green - now_green, (now_imp - lo_imp) / 0.65)
그 지점까지는 요청량이 그대로 들어가고, 넘어서면 잘려야 한다. 그게 사실인지 확인한다.
"""
from backend.llm_poc.tools import run_simulation
from backend.ml import predict_core

GRID = "11560_02332"
COUPLE = 0.65

_, feats, static, ranges = predict_core._load()
row = static[static["grid_id"] == GRID].iloc[0]
now_g, now_i = float(row["green_ratio"]), float(row["impervious_ratio"])
lo_i = ranges["impervious_ratio"][0]
hi_g = ranges["green_ratio"][1]

own = (hi_g - now_g) * 100
coupled = ((now_i - lo_i) / COUPLE) * 100
limit = min(own, coupled)
print(f"{GRID}  녹지 {now_g:.4f}  불투수 {now_i:.4f}")
print(f"  녹지 자체 여유    {own:.2f} 눈금")
print(f"  연동이 허용하는 폭 {coupled:.2f} 눈금")
print(f"  → 화면 한계선      {limit:.2f} 눈금 (표시는 {int(limit)})\n")

print(f"{'요청':>6}{'녹지 반영':>11}{'불투수 반영':>13}{'기대 연동':>11}  판정")
for tick in (1, 2, 3, 5, 10):
    sim = run_simulation(grid_id=GRID, green_ratio_delta=tick / 100)
    cf = sim["changed_features"]
    g = (cf["green_ratio"]["after"] - cf["green_ratio"]["before"]) * 100
    i = (cf["impervious_ratio"]["after"] - cf["impervious_ratio"]["before"]) * 100
    want_i = -COUPLE * tick
    full = abs(i - want_i) < 1e-6
    mark = "온전" if full else "잘림"
    side = "한계 이내" if tick <= limit else "한계 초과"
    print(f"{tick:>6}{g:>+11.2f}{i:>+13.2f}{want_i:>+11.2f}  {mark} ({side})")

print("\n한계 이내는 '온전', 초과는 '잘림'이어야 화면이 진실을 말하는 것이다.")
