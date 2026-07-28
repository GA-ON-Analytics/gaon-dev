"""예측 시뮬레이션 코어 로직 (API·CLI 공용).

사용자(또는 LLM)가 격자의 도시환경 변수를 바꿨을 때 여름철 LST anomaly가 어떻게 변하는지
compact 배포 모델(25MB)로 재예측한다. RandomForest는 비선형이라 가중치 곱셈으로 근사할 수
없고 실제 재예측이 필요하다.

핵심 안전장치 (LLM 임의 입력 대비):
  1) 각 변수를 학습 분포 범위로 clip (모델이 본 적 없는 값 = 환각 예측 방지)
  2) 파생 일관성: built_surface_ratio는 impervious_ratio 변화량과 함께 움직이게 한다
  3) 예측 신뢰도: 입력이 학습 분포에서 얼마나 벗어났는지 out_of_range 플래그로 알림
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
                  "built_surface_ratio", "zoning_residential_ratio", "zoning_commercial_ratio",
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


def _apply_and_constrain(base: dict, changes: dict, feats: list, ranges: dict):
    """변화 적용 + 물리 제약. (제약 적용된 시나리오, 변경값, 경고 리스트) 반환."""
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
        applied_deltas[k] = float(scen[k]) - float(base[k])

    # 파생 일관성: 불투수↔시가화는 같은 축 → 한쪽 변화량을 다른쪽에도 반영
    if (
        "impervious_ratio" in applied_deltas
        and "built_surface_ratio" in feats
        and "built_surface_ratio" not in changes
    ):
        next_value = float(base["built_surface_ratio"]) + applied_deltas["impervious_ratio"]
        scen["built_surface_ratio"] = _clip_feature_value("built_surface_ratio", next_value, ranges)
    if (
        "built_surface_ratio" in applied_deltas
        and "impervious_ratio" in feats
        and "impervious_ratio" not in changes
    ):
        next_value = float(base["impervious_ratio"]) + applied_deltas["built_surface_ratio"]
        scen["impervious_ratio"] = _clip_feature_value("impervious_ratio", next_value, ranges)

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


def predict(grid_id: str, changes: dict | None = None, top_k: int = 3) -> dict:
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

    scen, changed_features, warnings = _apply_and_constrain(base, changes, feats, ranges)
    Xs = pd.DataFrame([[scen[f] for f in feats]], columns=feats)
    predicted = float(model.predict(Xs)[0])

    # 트리 산포 두 가지를 나눠서 낸다.
    # uncertainty_std: 변경 후 절대 anomaly 예측의 트리 간 산포.
    # delta_std: 트리별 (변경 후 - 변경 전) 변화량의 산포. 두 예측이 같은 트리에서
    #            나와 강하게 상관되므로, 변화량의 불확실성은 반드시 짝지어 계산해야 한다.
    #            uncertainty_std를 변화량 오차로 쓰면 공분산 항이 빠져 크게 과대평가된다.
    if hasattr(model, "estimators_"):
        Xb_arr = Xb.to_numpy()
        Xs_arr = Xs.to_numpy()
        per_tree_before = np.array([t.predict(Xb_arr)[0] for t in model.estimators_])
        per_tree_after = np.array([t.predict(Xs_arr)[0] for t in model.estimators_])
        std = float(per_tree_after.std(ddof=1))
        delta_std = float((per_tree_after - per_tree_before).std(ddof=1))
    else:
        std = 0.0
        delta_std = 0.0
        warnings.append("모델 estimator 분산을 계산할 수 없어 uncertainty_std=0.0 반환")

    result = {
        "grid_id": grid_id,
        "gu_name": base.get("_gu_name"),
        "before_anomaly": round(baseline, 3),
        "after_anomaly": round(predicted, 3),
        "delta_c": round(predicted - baseline, 3),
        "uncertainty_std": round(std, 3),
        "delta_std": round(delta_std, 3),
        "changed_features": changed_features,
        "message": "ML simulation completed",
        "warnings": warnings,
    }
    return result
