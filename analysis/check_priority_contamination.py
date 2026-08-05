"""clip으로 오염된 green_delta_c가 '개선 우선순위'(priority_score)를 얼마나 흔드는지 잰다.

build_seoul_dashboard.py:118 에서 green_delta_c는 이렇게 계산된다.
    Xs[c] = (Xs[c] + d).clip(0, 1) if c in RATIO_COLS else Xs[c] + d
즉 불투수율이 5%p보다 작은 격자는 -5%p를 다 받지 못하고 0에서 멈춘다.
그 격자들은 '같은 조건'을 적용받지 않았는데도 같은 잣대로 비교·순위된다.

priority_score(build_seoul_dashboard.py:189)는
    normalized_rank(-green_delta_c) * W_COOLING
으로 green_delta_c를 그대로 쓴다. 오염이 지도 기본 레이어까지 전파되는지 확인한다.
"""
import json
from pathlib import Path

import numpy as np

DASH = Path("public/dashboard/100m")
GREEN_D = 0.05          # GREEN_DELTAS의 green_ratio 증가분
IMP_D = -0.05           # 불투수율 감소분

rows = []
for path in sorted(DASH.glob("*.geojson")):
    with path.open(encoding="utf-8") as f:
        for ft in json.load(f)["features"]:
            p = ft["properties"]
            if p.get("green_delta_c") is None or p.get("impervious_ratio") is None:
                continue
            rows.append((
                p["gu_name"],
                float(p["impervious_ratio"]),
                float(p.get("green_ratio") or 0.0),
                float(p["green_delta_c"]),
                float(p["priority_score"]) if p.get("priority_score") is not None else np.nan,
            ))

gu = np.array([r[0] for r in rows])
imp = np.array([r[1] for r in rows])
grn = np.array([r[2] for r in rows])
gdc = np.array([r[3] for r in rows])
pri = np.array([r[4] for r in rows])

# green_delta_c 계산 시점(ML 배치)의 clip 조건: clip(0,1) 경계를 넘는가
truncated = (imp + IMP_D < 0) | (grn + GREEN_D > 1)
applied_ratio = np.where(imp + IMP_D < 0, imp / abs(IMP_D), 1.0)

print(f"전체 격자 {len(rows):,}개")
print(f"  개입이 잘린 격자   {truncated.sum():,} ({truncated.mean()*100:.1f}%)")
print(f"  그중 불투수 때문   {int((imp + IMP_D < 0).sum()):,}")
print(f"  그중 녹지 때문     {int((grn + GREEN_D > 1).sum()):,}")
print(f"  잘린 격자의 불투수 실제 반영 비율: 중앙값 "
      f"{np.median(applied_ratio[truncated]):.2f}  최소 {applied_ratio[truncated].min():.2f}")

print("\ngreen_delta_c (음수 = 저감)")
for name, m in (("전량 반영", ~truncated), ("잘린 격자", truncated)):
    v = gdc[m]
    print(f"  {name:<10} 중앙값 {np.median(v):+.3f}  평균 {v.mean():+.3f}  최소 {v.min():+.3f}  n={m.sum():,}")

order = np.argsort(gdc)   # 저감 큰 순
print("\n'녹지화 여지' 상위권의 clip 격자 비율")
for pct in (1, 5, 10, 25):
    n = max(1, int(len(gdc) * pct / 100))
    share = truncated[order[:n]].mean() * 100
    print(f"  상위 {pct:>2}% ({n:>5,}격자)  clip 비율 {share:>5.1f}%   "
          f"(전체 {truncated.mean()*100:.1f}% 대비 {share/(truncated.mean()*100):.1f}배)")

ok = ~np.isnan(pri)
porder = np.argsort(-pri[ok])
tr_ok = truncated[ok]
print("\n'개선 우선순위' 상위권의 clip 격자 비율")
for pct in (1, 5, 10, 25):
    n = max(1, int(ok.sum() * pct / 100))
    share = tr_ok[porder[:n]].mean() * 100
    print(f"  상위 {pct:>2}% ({n:>5,}격자)  clip 비율 {share:>5.1f}%   "
          f"(전체 {tr_ok.mean()*100:.1f}% 대비 {share/(tr_ok.mean()*100):.1f}배)")

print("\n구별 clip 격자 비율 (상위 8)")
per = [(g, truncated[gu == g].mean() * 100, int((gu == g).sum())) for g in np.unique(gu)]
for g, share, n in sorted(per, key=lambda r: -r[1])[:8]:
    print(f"  {g:<8} {share:>5.1f}%   ({n:,}격자)")
