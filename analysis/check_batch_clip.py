"""구·250m·500m 선택 시 쓰이는 batch 시뮬레이션이 clip에 얼마나 흔들리는지 잰다.

main.py:392 는 구성 100m 셀의 delta_c를 그냥 평균한다. clip된 셀은 요청보다 적은 개입을
받았는데도 저감이 크게 나오므로(문서 2-2 참고) 평균을 끌어내린다. 화면에는
'구성 100m 셀 N개 평균'만 뜨고 clip 정보가 하나도 없다.
"""
import json
import random
from pathlib import Path

import numpy as np

from backend.llm_poc.tools import run_simulation

DASH = Path("public/dashboard/100m")
SAMPLE = 200
SEED = 20260803
SCENARIO = dict(impervious_ratio_delta=-0.05)
WANT = -0.05

TARGETS = ["11620_관악구", "11305_강북구", "11560_영등포구", "11680_강남구"]

random.seed(SEED)
print(f"시나리오: 불투수면 -5%p · 구별 {SAMPLE}셀 표본\n")
print(f"{'구':<10}{'clip셀':>8}{'전체평균':>10}{'clip제외':>10}{'차이':>9}{'clip셀평균':>11}")
print("-" * 60)

for stem in TARGETS:
    path = DASH / f"{stem}.geojson"
    if not path.exists():
        print(f"{stem}: 파일 없음")
        continue
    with path.open(encoding="utf-8") as f:
        feats = [ft for ft in json.load(f)["features"] if ft["properties"].get("grid_id")]
    picked = random.sample(feats, min(SAMPLE, len(feats)))

    clip_d, clean_d = [], []
    for ft in picked:
        sim = run_simulation(grid_id=str(ft["properties"]["grid_id"]), **SCENARIO)
        if not sim.get("success"):
            continue
        ba = sim["changed_features"].get("impervious_ratio")
        got = (ba["after"] - ba["before"]) if ba else 0.0
        (clip_d if abs(got) < abs(WANT) - 1e-9 else clean_d).append(sim["delta_c"])

    allv = np.array(clip_d + clean_d)
    clean = np.array(clean_d)
    gu = stem.split("_")[1]
    if len(clean) == 0:
        print(f"{gu:<10} 전부 clip")
        continue
    print(f"{gu:<10}{len(clip_d):>6}/{len(allv):<3}"
          f"{allv.mean():>+10.3f}{clean.mean():>+10.3f}"
          f"{allv.mean() - clean.mean():>+9.3f}"
          f"{(np.mean(clip_d) if clip_d else float('nan')):>+11.3f}")

print("\n'차이' = 화면에 뜨는 평균 − clip 셀을 뺀 평균. 음수면 저감을 과대평가하는 쪽.")
print("동률밴드 0.132℃와 견줘 볼 것.")
