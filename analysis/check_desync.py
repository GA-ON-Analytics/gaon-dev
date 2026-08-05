"""연동이 clip에 걸릴 때 녹지가 역산돼 줄어드는지 확인한다."""
from backend.llm_poc.tools import run_simulation

GRID = "11560_02332"   # 영등포구. impervious 0.0379, 학습범위 하한 0.0237

for green_pp in (5, 10, 20, 40):
    sim = run_simulation(grid_id=GRID, green_ratio_delta=green_pp / 100)
    cf = sim["changed_features"]
    g = cf.get("green_ratio")
    i = cf.get("impervious_ratio")
    g_applied = (g["after"] - g["before"]) * 100 if g else 0.0
    i_applied = (i["after"] - i["before"]) * 100 if i else 0.0
    expected_i = -0.65 * green_pp
    print(f"\n요청 녹지 +{green_pp}%p  (연동 기대치 불투수 {expected_i:+.2f}%p)")
    print(f"  녹지   {g['before']:.4f} -> {g['after']:.4f}   실제 {g_applied:+.2f}%p")
    if i:
        print(f"  불투수 {i['before']:.4f} -> {i['after']:.4f}   실제 {i_applied:+.2f}%p")
    print(f"  delta_c {sim['delta_c']:+.3f}")
    for w in sim["warnings"]:
        print(f"  ! {w}")
