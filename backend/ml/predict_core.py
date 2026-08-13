"""예측 시뮬레이션 코어 로직 (API·CLI 공용).

사용자(또는 LLM)가 격자의 도시환경 변수를 바꿨을 때 여름철 LST anomaly가 어떻게 변하는지
compact 배포 모델(25MB)로 재예측한다. RandomForest는 비선형이라 가중치 곱셈으로 근사할 수
없고 실제 재예측이 필요하다.

핵심 안전장치 (LLM 임의 입력 대비):
  1) 각 변수를 학습 분포 범위로 clip (모델이 본 적 없는 값 = 환각 예측 방지)
  2) 예측 신뢰도: 입력이 학습 분포에서 얼마나 벗어났는지 out_of_range 플래그로 알림

건물/불투수/녹지의 합을 1로 정규화하지 않는 이유:
  세 지표는 출처가 다르고(건물 도형 / 위성 불투수 / NDVI 식생) 물리적으로 겹칠 수 있다.
  실제로 전체 격자의 24.6%가 이미 합>1이다(최대 1.79). 배타적 자원이 아니다.
  여기서 정규화하면 "녹지만 +0.05" 요청에 건물이 -0.28 깎이는 등, 사용자가 건드리지도 않은
  변수를 바꿔 냉각 효과를 허위로 부풀린다. 범위 clip만으로 충분하다.

  ※ 아래 녹지↔불투수 연동(GREEN_TO_IMPERVIOUS, 이슈 #14)은 이 정규화와 다른 것이다.
    헷갈리기 쉬워 구분해 둔다.
      정규화       합=1이라는 '가정' · 건물 포함 3개 전부 · 요청량을 전부 흡수 · 고지 없음
      연동 #14     관측 회귀 기울기 -0.65 · 불투수 하나만 · 부분만 조정 · 경고 문구 + 끄기 가능
    연동은 "녹지 +5%p면 실제로 불투수가 평균 3.25%p 낮더라"는 관측을 반영하는 것이지,
    비율 합을 억지로 맞추는 게 아니다. 정규화를 되살리려는 시도는 위 이유로 막아야 한다.
"""
from __future__ import annotations

import json
import logging
from functools import lru_cache
from pathlib import Path
from typing import Any

import joblib
import numpy as np
import pandas as pd

LOGGER = logging.getLogger(__name__)

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


def dataset_ranges(feats: list[str], static: pd.DataFrame) -> dict[str, tuple[float, float]]:
    """데이터셋에서 직접 잰 범위.

    feature_meta.json을 **만드는** 쪽(build_feature_meta)이 쓴다. 거기서 _load()의 ranges를
    쓰면 meta가 자기 자신을 되먹여, 데이터셋이 바뀌어도 옛 범위가 그대로 굳는다.
    """
    measured = static[feats].describe().T[["min", "max"]]
    return {
        feature: (
            float(measured.loc[feature, "min"]),
            float(measured.loc[feature, "max"]),
        )
        for feature in feats
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


@lru_cache(maxsize=1)
def _grid_rows_by_id() -> pd.DataFrame:
    """Warm prediction에서 64k행 boolean scan을 반복하지 않는 read-only index."""
    _, _, static, _ = _load()
    return static.set_index("grid_id", drop=False)


def _grid_features_from_row(row: pd.Series, feats: list[str]) -> dict:
    missing = [
        feature
        for feature in feats
        if feature not in row.index or pd.isna(row[feature])
    ]
    if missing:
        return {
            "_gu_name": row.get("gu_name"),
            "_missing_features": missing,
        }
    out = {feature: float(row[feature]) for feature in feats}
    out["_gu_name"] = row.get("gu_name")
    return out


def get_grid_features(grid_id: str) -> dict | None:
    _, feats, _, _ = _load()
    try:
        row = _grid_rows_by_id().loc[grid_id]
    except KeyError:
        return None
    # 현재 dataset의 grid_id는 unique이다. 향후 중복이 생겨도 기존 iloc[0]
    # semantics를 유지해 단일 row만 사용한다.
    if isinstance(row, pd.DataFrame):
        row = row.iloc[0]
    return _grid_features_from_row(row, feats)


# 경고 문구는 대시보드 초록 안내창과 챗봇 답변에 그대로 노출된다. 변수명을 영어로 두면
# 사용자가 무엇이 보정됐는지 알 수 없어 한글 라벨로 바꿔 말한다.
FEATURE_LABEL = {
    "green_ratio": "녹지율",
    "impervious_ratio": "불투수면 비율",
    "ndvi": "식생 활력도(NDVI)",
    "albedo": "표면 반사율(albedo)",
}

# 비율 변수는 %로 말해야 슬라이더(%p)와 같은 단위가 된다. NDVI·albedo는 무단위 지수라 원값.
_RATIO_FEATURES = {"green_ratio", "impervious_ratio"}


def _fmt_value(feature: str, value: float) -> str:
    if feature in _RATIO_FEATURES:
        return f"{value * 100:.1f}%"
    return f"{value:.3f}"


def _clip_feature_value(feature: str, value: float, ranges: dict) -> float:
    lo, hi = ranges[feature]
    return min(max(value, lo), hi)


def _clip_warning(feature: str, requested: float, applied: float, ranges: dict) -> str:
    """왜 요청값이 그대로 안 들어갔는지를 사람 말로 설명한다.

    'clip'이라는 낱말과 '학습범위'는 chat_service의 clip 감지 조건이라 반드시 남긴다
    (_validate_final_answer / _format_tool_answer에서 이 두 단어로 clip 여부를 판정한다).
    """
    lo, hi = ranges[feature]
    label = FEATURE_LABEL.get(feature, feature)
    bound = "하한" if requested < lo else "상한"
    return (
        f"{label}을 {_fmt_value(feature, requested)}로 요청했지만 모델 학습범위"
        f"({_fmt_value(feature, lo)}~{_fmt_value(feature, hi)})의 {bound}을 벗어나 "
        f"{_fmt_value(feature, applied)}로 보정(clip)했습니다. "
        f"모델이 학습 때 본 적 없는 값은 예측 근거가 없어 경계에서 멈춥니다."
    )


def _final_clip_scenario(scen: dict, feats: list, ranges: dict, warnings: list[str]) -> None:
    for feature in feats:
        value = float(scen[feature])
        clipped = _clip_feature_value(feature, value, ranges)
        if not np.isclose(value, clipped):
            warnings.append(_clip_warning(feature, value, clipped, ranges))
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
    lo, hi = ranges[dst]
    green_pp = applied_deltas[src] * 100

    if np.isclose(moved, 0.0):
        bound = "하한" if delta < 0 else "상한"
        return [f"불투수면 비율이 이미 학습범위 {bound}({_fmt_value(dst, lo if delta < 0 else hi)})"
                f"이라 녹지 연동을 반영하지 못했습니다(clip). 이 격자에서는 녹지를 늘려도 "
                f"불투수면을 더 줄일 여지가 없습니다."]

    # 연동분이 clip에 걸려 일부만 반영된 경우. 기울기만 알려주면 사용자가 곱셈으로 검산했을 때
    # 숫자가 안 맞아 "왜 -0.65가 아니지?"로 읽힌다. 기대값·실제값·멈춘 이유를 함께 말한다.
    # '학습범위'와 'clip'은 chat_service의 clip 감지 키워드라 반드시 포함한다.
    if abs(moved) < abs(delta) - 1e-9:
        bound = "하한" if delta < 0 else "상한"
        return [f"녹지 {green_pp:+.1f}%p면 관측 기울기 {GREEN_TO_IMPERVIOUS}에 따라 불투수면이 "
                f"{delta * 100:+.1f}%p 바뀌어야 하지만, 이 격자의 불투수면 비율이 "
                f"{_fmt_value(dst, before)}에서 학습범위 {bound}"
                f"({_fmt_value(dst, lo if delta < 0 else hi)})에 닿아 "
                f"{moved * 100:+.1f}%p까지만 반영(clip)했습니다. "
                f"모델이 학습 때 본 적 없는 값은 예측 근거가 없어 경계에서 멈춥니다. "
                f"불투수면을 직접 지정하면 연동하지 않습니다."]

    return [f"녹지 {green_pp:+.1f}%p에 연동해 불투수면을 "
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
            warnings.append(_clip_warning(k, val, _clip_feature_value(k, val, ranges), ranges))
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


def _prepare_prediction(
    grid_id: str,
    changes: dict,
    feats: list[str],
    ranges: dict[str, tuple[float, float]],
    couple_land_cover: bool,
) -> tuple[dict | None, dict | None, dict | None, list[str] | None]:
    """Single/batch가 같은 lookup·clip·coupling·warning 규칙을 쓰게 한다."""
    base = get_grid_features(grid_id)
    if base is None:
        return None, None, {"error": f"grid_id 없음: {grid_id}"}, None
    if "_missing_features" in base:
        return None, None, {
            "error": "필수 모델 입력값 누락",
            "grid_id": grid_id,
            "gu_name": base.get("_gu_name"),
            "missing_features": base["_missing_features"],
        }, None

    scen, changed_features, warnings = _apply_and_constrain(
        base,
        changes,
        feats,
        ranges,
        couple=couple_land_cover,
    )
    return base, scen, changed_features, warnings


def _build_prediction_result(
    grid_id: str,
    base: dict,
    baseline: float,
    predicted: float,
    uncertainty_std: float,
    delta_std: float,
    direction_confidence: float | None,
    changed_features: dict,
    warnings: list[str],
) -> dict:
    return {
        "grid_id": grid_id,
        "gu_name": base.get("_gu_name"),
        "before_anomaly": round(float(baseline), 3),
        "after_anomaly": round(float(predicted), 3),
        "delta_c": round(float(predicted) - float(baseline), 3),
        "uncertainty_std": round(float(uncertainty_std), 3),
        "delta_std": round(float(delta_std), 3),
        "direction_confidence": direction_confidence,
        "changed_features": changed_features,
        "message": "ML simulation completed",
        "warnings": warnings,
    }


def predict(grid_id: str, changes: dict | None = None, top_k: int = 3,
            couple_land_cover: bool = True) -> dict:
    del top_k  # 기존 public signature 호환성을 유지한다.
    model, feats, _, ranges = _load()
    changes = changes or {}
    base, scen, changed_features, warnings = _prepare_prediction(
        grid_id,
        changes,
        feats,
        ranges,
        couple_land_cover,
    )
    if base is None or scen is None:
        return changed_features or {"grid_id": grid_id, "error": "prediction failed"}

    Xb = pd.DataFrame([[base[f] for f in feats]], columns=feats)
    baseline = float(model.predict(Xb)[0])
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

    return _build_prediction_result(
        grid_id,
        base,
        baseline,
        predicted,
        std,
        delta_std,
        direction_confidence,
        changed_features,
        warnings,
    )


def predict_batch(
    grid_ids: list[str],
    changes: dict | None = None,
    top_k: int = 3,
    couple_land_cover: bool = True,
) -> list[dict]:
    """N개 격자를 독립 행으로 유지한 sklearn matrix batch prediction.

    전처리에서 실패한 grid는 제자리에 error result로 남고, 정상 grid만
    (N, F) matrix에 넣는다. 모델/tree 호출 횟수만 줄이며 single semantics는 공유한다.
    """
    del top_k  # 기존 predict signature와 동일한 호출 형태를 지원한다.
    if not grid_ids:
        return []

    model, feats, _, ranges = _load()
    changes = changes or {}
    results: list[dict | None] = [None] * len(grid_ids)
    valid_indexes: list[int] = []
    bases: list[dict] = []
    scenarios: list[dict] = []
    changed_by_grid: list[dict] = []
    warnings_by_grid: list[list[str]] = []

    for index, grid_id in enumerate(grid_ids):
        try:
            base, scen, changed_features, warnings = _prepare_prediction(
                grid_id,
                changes,
                feats,
                ranges,
                couple_land_cover,
            )
        except Exception:
            LOGGER.exception("Batch preprocessing failed for grid_id=%s", grid_id)
            results[index] = {"grid_id": grid_id, "error": "prediction failed"}
            continue

        if base is None or scen is None:
            results[index] = changed_features or {
                "grid_id": grid_id,
                "error": "prediction failed",
            }
            continue
        valid_indexes.append(index)
        bases.append(base)
        scenarios.append(scen)
        changed_by_grid.append(changed_features or {})
        warnings_by_grid.append(warnings or [])

    if not valid_indexes:
        return [
            result or {"grid_id": grid_ids[index], "error": "prediction failed"}
            for index, result in enumerate(results)
        ]

    Xb = pd.DataFrame(
        [[base[feature] for feature in feats] for base in bases],
        columns=feats,
    )
    Xs = pd.DataFrame(
        [[scen[feature] for feature in feats] for scen in scenarios],
        columns=feats,
    )
    baselines = np.asarray(model.predict(Xb), dtype=float)
    predictions = np.asarray(model.predict(Xs), dtype=float)

    if hasattr(model, "estimators_"):
        Xb_arr = Xb.to_numpy()
        Xs_arr = Xs.to_numpy()
        # 300 trees x 5,000 rows x float64 = matrix당 약 12MB. before matrix를
        # delta로 제자리 변환해 세 번째 대형 matrix를 만들지 않는다.
        per_tree_before = np.asarray(
            [tree.predict(Xb_arr) for tree in model.estimators_],
            dtype=float,
        )
        per_tree_after = np.asarray(
            [tree.predict(Xs_arr) for tree in model.estimators_],
            dtype=float,
        )
        uncertainty_stds = per_tree_after.std(axis=0, ddof=1)
        np.subtract(per_tree_after, per_tree_before, out=per_tree_before)
        delta_stds = per_tree_before.std(axis=0, ddof=1)
        direction_confidences = [
            _direction_confidence(per_tree_before[:, column])
            for column in range(len(valid_indexes))
        ]
    else:
        uncertainty_stds = np.zeros(len(valid_indexes), dtype=float)
        delta_stds = np.zeros(len(valid_indexes), dtype=float)
        direction_confidences = [None] * len(valid_indexes)
        for warnings in warnings_by_grid:
            warnings.append("모델 estimator 분산을 계산할 수 없어 uncertainty_std=0.0 반환")

    for column, result_index in enumerate(valid_indexes):
        results[result_index] = _build_prediction_result(
            grid_ids[result_index],
            bases[column],
            baselines[column],
            predictions[column],
            uncertainty_stds[column],
            delta_stds[column],
            direction_confidences[column],
            changed_by_grid[column],
            warnings_by_grid[column],
        )

    return [
        result or {"grid_id": grid_ids[index], "error": "prediction failed"}
        for index, result in enumerate(results)
    ]
