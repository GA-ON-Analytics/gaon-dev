"""clip 경고 문구가 사람이 읽을 수 있는지 확인한다."""
from backend.llm_poc.tools import run_simulation

CASES = [
    ("불투수 직접 -5%p", dict(impervious_ratio_delta=-0.05)),
    ("NDVI +0.3", dict(ndvi_delta=0.3)),
    ("알베도 +0.5", dict(albedo_delta=0.5)),
    ("녹지 -40%p (역방향)", dict(green_ratio_delta=-0.40)),
    ("녹지 +40%p", dict(green_ratio_delta=0.40)),
]

for name, kw in CASES:
    s = run_simulation(grid_id="11560_02332", **kw)
    print(f"\n--- {name}   delta_c={s['delta_c']:+.3f}")
    for w in s["warnings"]:
        print("   !", w)
    for n in s["policy_direction_notes"]:
        print("   *", n)
