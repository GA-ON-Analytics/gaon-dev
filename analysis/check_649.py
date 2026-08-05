"""11170_00649 — 한계 표기(내림)와 실제 적용 표기(반올림)가 어긋나는지 확인."""
from backend.llm_poc.tools import run_simulation
from backend.ml import predict_core

GRID = "11170_00649"
COUPLE = 0.65
_, _, static, ranges = predict_core._load()
row = static[static["grid_id"] == GRID].iloc[0]
g, i = float(row["green_ratio"]), float(row["impervious_ratio"])
lo_i, hi_g = ranges["impervious_ratio"][0], ranges["green_ratio"][1]

own = (hi_g - g) * 100
coupled = ((i - lo_i) / COUPLE) * 100
print(f"녹지 {g:.6f}  불투수 {i:.6f}")
print(f"  녹지 자체 여유 {own:.4f} 눈금   연동 폭 {coupled:.4f} 눈금")
print(f"  한계 = {min(own, coupled):.4f}  ({'녹지 자체' if own < coupled else '연동'})")
print(f"  한계 표기(내림 1자리)   +{int(min(own, coupled) * 10) / 10:.1f}%p   ← 현재 화면")
print(f"  한계 표기(반올림 1자리) +{min(own, coupled):.1f}%p   ← 실제 적용과 같은 방식")

print(f"\n불투수 슬라이더: 여유 {(i - lo_i) * 100:.2f}눈금 (max 30) → "
      f"{'한계 있음' if (i - lo_i) * 100 < 30 else '한계 없음'}")

for tick in (37, 38, 40):
    sim = run_simulation(grid_id=GRID, green_ratio_delta=tick / 100)
    cf = sim["changed_features"]
    ga = (cf["green_ratio"]["after"] - cf["green_ratio"]["before"]) * 100
    print(f"  녹지 +{tick} 요청 → 실제 {ga:.4f}%p   화면 표기 {ga:+.1f}%p")
