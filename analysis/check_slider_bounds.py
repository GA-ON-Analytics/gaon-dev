"""슬라이더 최대값에서 몇 %의 격자가 clip에 걸리는지 잰다.

GridDetailSidePanel.tsx의 SIM_SLIDERS 주석은 max를 '포화' 기준으로 정했다고 밝히면서
  imp  효과는 계속 크지만 30을 넘으면 격자 절반 이상이 하한(0.024)에 걸린다
라고 적어 뒀다. 그 판단이 지금도 맞는지, 다른 슬라이더는 어떤지 실측한다.
학습범위는 서빙 경로(predict_core)의 ranges를 그대로 쓴다.
"""
import json
from pathlib import Path

import numpy as np

from backend.ml import predict_core

DASH = Path("public/dashboard/100m")
# (라벨, 피처, 슬라이더 최대 눈금, 눈금→값 배율, 부호)
SLIDERS = [
    ("녹지율 늘리기", "green_ratio", 40, 0.01, +1),
    ("불투수면 줄이기", "impervious_ratio", 30, 0.01, -1),
    ("식생 활력도", "ndvi", 30, 0.01, +1),
    ("표면 반사율", "albedo", 5, 0.01, +1),
]

_, feats, static, ranges = predict_core._load()
print("모델 학습범위")
for _, feature, *_ in SLIDERS:
    lo, hi = ranges[feature]
    print(f"  {feature:<18} {lo:.4f} ~ {hi:.4f}")

vals = {}
for _, feature, *_ in SLIDERS:
    vals[feature] = static[feature].astype(float).to_numpy()
n = len(next(iter(vals.values())))
print(f"\n격자 {n:,}개 기준, 슬라이더 눈금별 clip 발생 비율")

for label, feature, max_tick, scale, sign in SLIDERS:
    lo, hi = ranges[feature]
    base = vals[feature]
    print(f"\n[{label}]  max={max_tick}  현재값 중앙 {np.median(base):.4f}")
    for tick in sorted({5, 10, 20, 30, 40, max_tick}):
        if tick > max_tick:
            continue
        target = base + sign * tick * scale
        clipped = (target < lo) | (target > hi)
        # clip된 격자에서 실제로 반영되는 비율
        applied = np.clip(target, lo, hi) - base
        want = sign * tick * scale
        ratio = np.where(clipped, np.abs(applied) / abs(want), 1.0)
        share = clipped.mean() * 100
        med = np.median(ratio[clipped]) if clipped.any() else 1.0
        print(f"    눈금 {tick:>2}  clip {share:>5.1f}%   "
              f"잘린 격자의 실제 반영 비율 중앙값 {med:.2f}")
