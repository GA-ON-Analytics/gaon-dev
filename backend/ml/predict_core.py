"""예측 시뮬레이션 코어 로직 (API·CLI 공용).

사용자(또는 LLM)가 격자의 도시환경 변수를 바꿨을 때 여름철 LST anomaly가 어떻게 변하는지
compact 배포 모델(25MB)로 재예측한다. RandomForest는 비선형이라 가중치 곱셈으로 근사할 수
없고 실제 재예측이 필요하다.

핵심 안전장치 (LLM 임의 입력 대비):
  1) 각 변수를 학습 분포 범위로 clip (모델이 본 적 없는 값 = 환각 예측 방지)
  2) 예측 신뢰도: 입력이 학습 분포에서 얼마나 벗어났는지 out_of_range 플래그로 알림
"""
from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any

import joblib
import numpy as np
import pandas as pd

BACKEND_DIR = Path(__file__).resolve().parents[1]
MODEL_PATH = BACKEND_DIR / "models" / "seoul_grid_explain_model.joblib"
FEATURE_COLUMNS_PATH = BACKEND_DIR / "models" / "seoul_grid_feature_columns.json"
STATIC_PATH = BACKEND_DIR / "data" / "processed" / "seoul_grid_dataset.csv"
FEATURE_META_PATH = BACKEND_DIR / "models" / "feature_meta.json"

# 0~1 비율 변수
RATIO_FEATURES = {"building_ratio", "road_ratio", "green_ratio", "impervious_ratio",
                  "zoning_residential_ratio", "zoning_commercial_ratio",
                  "zoning_industrial_ratio", "zoning_green_ratio"}
REQUIRED_FILES = (
    MODEL_PATH,
    FEATURE_COLUMNS_PATH,
    FEATURE_META_PATH,
    STATIC_PATH,
)


def required_file_status() -> dict[str, bool]:
    return {
        "model": MODEL_PATH.exists(),
        "feature_columns": FEATURE_COLUMNS_PATH.exists(),
        "feature_meta": FEATURE_META_PATH.exists(),
        "dataset": STATIC_PATH.exists(),
    }


def missing_required_files() -> list[str]:
    return [str(path) for path in REQUIRED_FILES if not path.exists()]


def _load_feature_meta_map() -> dict[str, dict[str, Any]]:
    meta = json.loads(FEATURE_META_PATH.read_text(encoding="utf-8"))
    return {
        item["name"]: item
        for item in meta
        if isinstance(item, dict) and isinstance(item.get("name"), str)
    }


def _ranges_from_meta(feats: list[str], static: pd.DataFrame) -> dict[str, tuple[float, float]]:
    meta_by_name = _load_feature_meta_map()
    dataset_ranges = static[feats].describe().T[["min", "max"]]
    ranges: dict[str, tuple[float, float]] = {}

    for feature in feats:
        meta = meta_by_name.get(feature, {})
        min_value = meta.get("min")
        max_value = meta.get("max")

        if isinstance(min_value, (int, float)) and isinstance(max_value, (int, float)):
            ranges[feature] = (float(min_value), float(max_value))
        else:
            ranges[feature] = (
                float(dataset_ranges.loc[feature, "min"]),
                float(dataset_ranges.loc[feature, "max"]),
            )

    return ranges


@lru_cache(maxsize=1)
def _load():
    model = joblib.load(MODEL_PATH)
    feats = json.loads(FEATURE_COLUMNS_PATH.read_text(encoding="utf-8"))
    static = pd.read_csv(STATIC_PATH, encoding="utf-8-sig")
    static["gu_code"] = static["gu_code"].astype(str)
    missing_columns = [feature for feature in feats if feature not in static.columns]
    if missing_columns:
        raise ValueError(f"Missing model feature columns: {missing_columns}")
    if "grid_id" not in static.columns:
        raise ValueError("Missing required dataset column: grid_id")
    ranges = _ranges_from_meta(feats, static)
    return model, feats, static, ranges


def model_status() -> dict[str, bool]:
    status = required_file_status()
    ready = all(status.values())

    if ready:
        try:
            _load()
        except Exception:
            ready = False

    return {
        **status,
        "ready": ready,
    }


def feature_meta() -> list[dict]:
    """LLM/프론트가 '무엇을 바꿀 수 있나'를 알도록 변수 설명·범위 제공."""
    if FEATURE_META_PATH.exists():
        return json.loads(FEATURE_META_PATH.read_text(encoding="utf-8"))
    _, feats, _, ranges = _load()
    return [{"name": f, "min": ranges[f][0], "max": ranges[f][1],
             "is_ratio": f in RATIO_FEATURES} for f in feats]


def get_grid_features(grid_id: str) -> dict | None:
    _, feats, static, _ = _load()
    row = static[static["grid_id"] == grid_id]
    if row.empty:
        return None
    r = row.iloc[0]
    missing = [f for f in feats if f not in r.index or pd.isna(r[f])]
    if missing:
        return {
            "_gu_name": r.get("gu_name"),
            "_missing_features": missing,
        }
    out = {f: float(r[f]) for f in feats}
    out["_gu_name"] = r.get("gu_name")
    return out


def _clip_feature_value(feature: str, value: float, ranges: dict) -> float:
    lo, hi = ranges[feature]
    return min(max(value, lo), hi)


def _final_clip_scenario(scen: dict, feats: list, ranges: dict, warnings: list[str]) -> None:
    for feature in feats:
        value = float(scen[feature])
        clipped = _clip_feature_value(feature, value, ranges)
        if not np.isclose(value, clipped):
            lo, hi = ranges[feature]
            warnings.append(f"{feature}={value:.3f} 최종 학습범위[{lo:.2f},{hi:.2f}] 밖 → clip")
            scen[feature] = clipped


# 녹지를 늘리면 그만큼 다른 지표면이 줄어야 한다(이슈 #14). 다만 1:1은 근거가 없다.
# 서울 64,574격자에서 impervious_ratio를 green_ratio로 회귀한 기울기가 -0.655이고,
# green+impervious 합도 평균 0.715라 나머지 약 24%(물·나대지)가 존재한다.
# 즉 녹지 증가분이 전량 불투수면에서 나오지는 않으므로 관측 기울기를 계수로 쓴다.
GREEN_TO_IMPERVIOUS = -0.65


def _apply_land_cover_coupling(changes: dict, scen: dict, feats: list, ranges: dict,
                               applied_deltas: dict) -> list[str]:
    """green_ratio 변화를 impervious_ratio에 반영한다. scen을 제자리에서 고친다."""
    src, dst = "green_ratio", "impervious_ratio"
    if src not in applied_deltas or dst not in feats:
        return []
    if dst in changes:  # 사용자가 직접 지정했으면 그 값을 존중한다
        return []

    delta = applied_deltas[src] * GREEN_TO_IMPERVIOUS
    if np.isclose(delta, 0.0):
        return []

    before = float(scen[dst])
    scen[dst] = _clip_feature_value(dst, before + delta, ranges)
    moved = scen[dst] - before
    if np.isclose(moved, 0.0):
        return [f"{dst}가 이미 학습범위 끝이라 녹지 연동이 반영되지 않았습니다."]

    return [f"녹지 {applied_deltas[src] * 100:+.1f}%p에 연동해 불투수면을 "
            f"{moved * 100:+.1f}%p 조정했습니다 (관측 기울기 {GREEN_TO_IMPERVIOUS}). "
            f"불투수면을 직접 지정하면 연동하지 않습니다."]


def _direction_confidence(per_tree_delta: np.ndarray) -> float | None:
    """트리들이 변화 '방향'에 얼마나 동의하는지를 0~1로 반환한다.

    변화량이 정확히 0인 트리는 제외하고, 움직인 트리 중 다수파의 비율을 센다.
    RF 트리는 계단함수라 개입이 작으면 상당수가 delta=0을 내놓는데(녹지 5%p에서 약 40%),
    그건 '효과가 없다'가 아니라 그 트리의 분할 임계값을 넘지 못한 것이므로 판단에서 뺀다.

    delta_std를 오차막대로 쓰면 8배 과대평가되므로, 사용자에게는 이 값을 보여준다.
    """
    moved = per_tree_delta[np.abs(per_tree_delta) > 1e-9]
    if moved.size == 0:
        return None
    cooling = float((moved < 0).mean())
    return round(max(cooling, 1.0 - cooling), 3)


def _apply_and_constrain(base: dict, changes: dict, feats: list, ranges: dict,
                         couple: bool = True):
    """변화 적용 + 물리 제약. (제약 적용된 시나리오, 변경값, 경고 리스트) 반환.

    couple=True면 녹지↔불투수를 연동한다(이슈 #14). 사용자가 두 변수를 모두 명시하면
    그 값을 존중해 연동하지 않는다.
    """
    warnings = []
    scen = {f: base[f] for f in feats}
    changed_features: dict[str, dict[str, float]] = {}
    applied_deltas: dict[str, float] = {}

    for k, delta in changes.items():
        if k not in feats:
            warnings.append(f"알 수 없는 변수 무시: {k}")
            continue
        val = scen[k] + float(delta) if _is_delta(delta) else float(_strip(delta))
        lo, hi = ranges[k]
        if val < lo or val > hi:
            warnings.append(f"{k}={val:.3f} 학습범위[{lo:.2f},{hi:.2f}] 밖 → clip")
        scen[k] = _clip_feature_value(k, val, ranges)
        applied_deltas[k] = float(scen[k]) - float(base[k])  # clip 후 실제 반영량

    if couple:
        warnings.extend(_apply_land_cover_coupling(changes, scen, feats, ranges,
                                                   applied_deltas))

    _final_clip_scenario(scen, feats, ranges, warnings)

    for feature in feats:
        if not np.isclose(float(base[feature]), float(scen[feature])):
            changed_features[feature] = {
                "before": round(float(base[feature]), 6),
                "after": round(float(scen[feature]), 6),
            }

    return scen, changed_features, warnings


def _is_delta(v) -> bool:
    return isinstance(v, (int, float)) or (isinstance(v, str) and (v.startswith("+") or v.startswith("-")))


def _strip(v):
    return v.lstrip("=") if isinstance(v, str) else v


def predict(grid_id: str, changes: dict | None = None, top_k: int = 3,
            couple_land_cover: bool = True) -> dict:
    model, feats, static, ranges = _load()
    base = get_grid_features(grid_id)
    if base is None:
        return {"error": f"grid_id 없음: {grid_id}"}
    if "_missing_features" in base:
        return {
            "error": "필수 모델 입력값 누락",
            "grid_id": grid_id,
            "gu_name": base.get("_gu_name"),
            "missing_features": base["_missing_features"],
        }
    changes = changes or {}

    Xb = pd.DataFrame([[base[f] for f in feats]], columns=feats)
    baseline = float(model.predict(Xb)[0])

    scen, changed_features, warnings = _apply_and_constrain(
        base, changes, feats, ranges, couple=couple_land_cover)
    Xs = pd.DataFrame([[scen[f] for f in feats]], columns=feats)
    predicted = float(model.predict(Xs)[0])

    # 트리 산포 지표들.
    # uncertainty_std: 변경 후 절대 anomaly 예측의 트리 간 산포.
    # delta_std: 트리별 (변경 후 - 변경 전) 변화량의 산포. 두 예측이 같은 트리에서
    #            나와 강하게 상관되므로, 변화량의 불확실성은 반드시 짝지어 계산해야 한다.
    #            uncertainty_std를 변화량 오차로 쓰면 공분산 항이 빠져 크게 과대평가된다.
    #
    # ⚠️ delta_std를 '이 예측의 오차'로 화면에 쓰면 안 된다. 이 값은 '트리 하나를 뽑았을 때
    # 답이 얼마나 다른가'이고, 우리가 보여주는 값은 300개의 평균이다. RF는 배깅·피처
    # 무작위로 트리를 일부러 다르게 만들므로 트리 산포는 크게 나오는 게 정상이고, 그중
    # 상당수는 계단함수 양자화(변화량이 분할 임계값을 못 넘어 delta=0)라 평균에서 상쇄된다.
    # 데이터 부트스트랩 8회 재학습으로 잰 실제 추정오차는 delta_std의 약 1/8이었다.
    # 사용자에게는 delta_std 대신 direction_confidence(방향 확신도)를 보여준다.
    if hasattr(model, "estimators_"):
        Xb_arr = Xb.to_numpy()
        Xs_arr = Xs.to_numpy()
        per_tree_before = np.array([t.predict(Xb_arr)[0] for t in model.estimators_])
        per_tree_after = np.array([t.predict(Xs_arr)[0] for t in model.estimators_])
        per_tree_delta = per_tree_after - per_tree_before
        std = float(per_tree_after.std(ddof=1))
        delta_std = float(per_tree_delta.std(ddof=1))
        direction_confidence = _direction_confidence(per_tree_delta)
    else:
        std = 0.0
        delta_std = 0.0
        direction_confidence = None
        warnings.append("모델 estimator 분산을 계산할 수 없어 uncertainty_std=0.0 반환")

    result = {
        "grid_id": grid_id,
        "gu_name": base.get("_gu_name"),
        "before_anomaly": round(baseline, 3),
        "after_anomaly": round(predicted, 3),
        "delta_c": round(predicted - baseline, 3),
        "uncertainty_std": round(std, 3),
        "delta_std": round(delta_std, 3),
        "direction_confidence": direction_confidence,
        "changed_features": changed_features,
        "message": "ML simulation completed",
        "warnings": warnings,
    }
    return result
