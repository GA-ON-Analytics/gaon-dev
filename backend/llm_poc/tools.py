"""Qwen에 제공할 GA:ON 격자 조회·정책 시뮬레이션 도구."""

from __future__ import annotations

import math
from collections.abc import Mapping
from numbers import Real
from typing import Any

from backend.ml import predict_core


INTERPRETATION_BASIS = (
    "before_anomaly와 after_anomaly는 절대온도가 아니라 "
    "predict_core.predict()가 반환한 모델 예측 anomaly이며, "
    "delta_c는 두 모델 예측의 차이인 모델 기준 예상 변화량입니다."
)
LIMITATIONS = [
    "모델 기반 시나리오 예측이므로 실제 정책의 인과효과로 단정할 수 없습니다.",
    "비용, 토지, 공사기간, 행정 가능성은 반영하지 않았습니다.",
]

GET_GRID_DATA_TOOL = {
    "type": "function",
    "function": {
        "name": "get_grid_data",
        "description": (
            "GA:ON 데이터셋에서 grid_id에 해당하는 자치구명, 녹지율, "
            "불투수율을 조회한다. 비율은 0~1 원본값으로 반환한다. "
            "정책 변경 후 모델 결과가 아니라 단순 현재 데이터 조회에만 사용한다."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "grid_id": {
                    "type": "string",
                    "description": "조회할 서울 격자 ID. 예: 11230_00001",
                }
            },
            "required": ["grid_id"],
            "additionalProperties": False,
        },
    },
}


RUN_SIMULATION_TOOL = {
    "type": "function",
    "function": {
        "name": "run_simulation",
        "description": (
            "특정 100m 격자에 녹지율, 불투수율 또는 반경 500m 내 공원 면적의 "
            "변경 시나리오를 적용하고 기존 머신러닝 모델을 다시 실행한다. "
            "정책 변경 후 모델 예측 anomaly와 모델 기준 예상 변화량을 묻는 경우에만 "
            "사용한다. 단순 현재 녹지율·불투수율 조회에는 get_grid_data를 사용한다. "
            "비율 변화량은 0~1 단위의 부호 있는 delta이므로 5%p 증가는 0.05, "
            "5%p 감소는 -0.05이다. 공원 면적 변화량은 ㎡ 단위이며 음수는 허용하지 않는다."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "grid_id": {
                    "type": "string",
                    "description": "시뮬레이션할 서울 100m 격자 ID. 예: 11230_00001",
                },
                "green_ratio_delta": {
                    "type": "number",
                    "default": 0,
                    "description": (
                        "현재 녹지율에 더할 0~1 단위의 부호 있는 변화량. "
                        "5%p 증가는 0.05, 5%p 감소는 -0.05이다."
                    ),
                },
                "impervious_ratio_delta": {
                    "type": "number",
                    "default": 0,
                    "description": (
                        "현재 불투수율에 더할 0~1 단위의 부호 있는 변화량. "
                        "5%p 감소는 -0.05, 5%p 증가는 0.05이다."
                    ),
                },
                "park_area_delta": {
                    "type": "number",
                    "minimum": 0,
                    "default": 0,
                    "description": (
                        "현재 반경 500m 내 공원 면적에 더할 증가량(㎡). "
                        "예: 1,000㎡ 증가는 1000이다."
                    ),
                },
            },
            "required": ["grid_id"],
            "additionalProperties": False,
        },
    },
}


def _empty_result(grid_id: str | None) -> dict[str, Any]:
    return {
        "success": False,
        "grid_id": grid_id,
        "gu_name": None,
        "green_ratio": None,
        "impervious_ratio": None,
    }


def _is_missing(value: Any) -> bool:
    if value is None:
        return True
    if isinstance(value, str):
        return not value.strip()
    if isinstance(value, (int, float)):
        return math.isnan(float(value))
    return False


def _is_finite_number(value: Any) -> bool:
    return (
        isinstance(value, Real)
        and not isinstance(value, bool)
        and math.isfinite(float(value))
    )


def _empty_simulation_result(
    grid_id: str | None,
    requested_changes: Mapping[str, float] | None = None,
) -> dict[str, Any]:
    return {
        "success": False,
        "grid_id": grid_id,
        "gu_name": None,
        "requested_changes": dict(requested_changes or {}),
        "applied_changes": {},
        "before_anomaly": None,
        "after_anomaly": None,
        "delta_c": None,
        "uncertainty_std": None,
        "warnings": [],
        "policy_direction_notes": [],
        "interpretation_basis": INTERPRETATION_BASIS,
        "limitations": list(LIMITATIONS),
    }


def _validated_delta(name: str, value: Any) -> tuple[float | None, str | None]:
    if not _is_finite_number(value):
        return None, f"{name}는 NaN이나 무한대가 아닌 숫자여야 합니다."
    return float(value), None


def get_grid_data(grid_id: str) -> dict[str, Any]:
    """기존 ML 조회 함수를 이용해 격자의 필수 환경 데이터를 반환한다.

    Args:
        grid_id: 조회할 서울 격자 ID.

    Returns:
        성공 여부, 격자 ID, 자치구명, 0~1 원본 녹지율과 불투수율.
        실패 시 같은 필드들과 명확한 ``error``를 반환한다.
    """

    normalized_grid_id = grid_id.strip() if isinstance(grid_id, str) else None
    result = _empty_result(normalized_grid_id)

    if not normalized_grid_id:
        result["error"] = "grid_id가 필요합니다."
        return result

    try:
        features = predict_core.get_grid_features(normalized_grid_id)
    except Exception as exc:
        result["error"] = f"격자 데이터 조회 실패: {exc}"
        return result

    if features is None:
        result["error"] = f"grid_id를 찾을 수 없습니다: {normalized_grid_id}"
        return result

    if "_missing_features" in features:
        result["gu_name"] = features.get("_gu_name")
        result["missing_fields"] = list(features["_missing_features"])
        result["error"] = "필수 격자 데이터가 누락되었습니다."
        return result

    result.update(
        {
            "gu_name": features.get("_gu_name"),
            "green_ratio": features.get("green_ratio"),
            "impervious_ratio": features.get("impervious_ratio"),
        }
    )
    missing_fields = [
        field
        for field in ("gu_name", "green_ratio", "impervious_ratio")
        if _is_missing(result[field])
    ]
    if missing_fields:
        result["missing_fields"] = missing_fields
        result["error"] = "필수 격자 데이터가 누락되었습니다."
        return result

    result["success"] = True
    return result


def run_simulation(
    grid_id: str,
    green_ratio_delta: float = 0,
    impervious_ratio_delta: float = 0,
    park_area_delta: float = 0,
) -> dict[str, Any]:
    """기존 ``predict_core.predict``로 100m 격자 정책 시나리오를 실행한다.

    비율 변화량은 현재 0~1 원본 비율에 더할 부호 있는 delta이고, 공원 면적
    변화량은 ㎡ 단위이다. 학습 범위 clip과 경고 생성은 기존 ``predict``에
    그대로 위임한다.
    """

    normalized_grid_id = grid_id.strip() if isinstance(grid_id, str) else None
    result = _empty_simulation_result(normalized_grid_id)
    if not normalized_grid_id:
        result["error"] = "grid_id가 필요합니다."
        return result

    validated_deltas: dict[str, float] = {}
    for name, value in (
        ("green_ratio_delta", green_ratio_delta),
        ("impervious_ratio_delta", impervious_ratio_delta),
        ("park_area_delta", park_area_delta),
    ):
        normalized_value, error = _validated_delta(name, value)
        if error is not None:
            result["error"] = error
            return result
        validated_deltas[name] = normalized_value

    if validated_deltas["park_area_delta"] < 0:
        result["error"] = "park_area_delta는 0 이상이어야 합니다."
        return result

    requested_changes: dict[str, float] = {}
    if validated_deltas["green_ratio_delta"]:
        requested_changes["green_ratio"] = validated_deltas["green_ratio_delta"]
    if validated_deltas["impervious_ratio_delta"]:
        requested_changes["impervious_ratio"] = validated_deltas[
            "impervious_ratio_delta"
        ]
    if validated_deltas["park_area_delta"]:
        requested_changes["park_area_within_500m"] = validated_deltas[
            "park_area_delta"
        ]
    result["requested_changes"] = requested_changes

    policy_direction_notes: list[str] = []
    if validated_deltas["green_ratio_delta"] < 0:
        policy_direction_notes.append(
            "녹지율 감소는 일반적인 열 저감 정책 방향과 반대인 시나리오입니다."
        )
    if validated_deltas["impervious_ratio_delta"] > 0:
        policy_direction_notes.append(
            "불투수율 증가는 일반적인 열 저감 정책 방향과 반대인 시나리오입니다."
        )
    result["policy_direction_notes"] = policy_direction_notes

    try:
        missing_files = predict_core.missing_required_files()
    except Exception as exc:
        result["error"] = f"모델·데이터 파일 상태 확인 실패: {exc}"
        return result
    if missing_files:
        result["missing_files"] = missing_files
        result["error"] = "시뮬레이션에 필요한 모델 또는 데이터 파일이 없습니다."
        return result

    try:
        prediction = predict_core.predict(normalized_grid_id, requested_changes)
    except Exception as exc:
        result["error"] = f"시뮬레이션 실행 실패: {exc}"
        return result

    if not isinstance(prediction, Mapping):
        result["error"] = "시뮬레이션 함수가 올바른 객체를 반환하지 않았습니다."
        return result
    if "error" in prediction:
        result["gu_name"] = prediction.get("gu_name")
        if "missing_features" in prediction:
            result["missing_features"] = prediction["missing_features"]
        result["error"] = str(prediction["error"])
        return result

    required_fields = (
        "grid_id",
        "gu_name",
        "before_anomaly",
        "after_anomaly",
        "delta_c",
        "uncertainty_std",
        "changed_features",
        "warnings",
    )
    missing_fields = [
        field
        for field in required_fields
        if field not in prediction or _is_missing(prediction[field])
    ]
    numeric_fields = (
        "before_anomaly",
        "after_anomaly",
        "delta_c",
        "uncertainty_std",
    )
    invalid_numeric_fields = [
        field
        for field in numeric_fields
        if field in prediction and not _is_finite_number(prediction[field])
    ]
    if not isinstance(prediction.get("changed_features"), Mapping):
        missing_fields.append("changed_features")
    if not isinstance(prediction.get("warnings"), list):
        missing_fields.append("warnings")
    invalid_fields = sorted(set(missing_fields + invalid_numeric_fields))
    if invalid_fields:
        result["missing_fields"] = invalid_fields
        result["error"] = "시뮬레이션 필수 반환값이 누락되었거나 올바르지 않습니다."
        return result

    applied_changes = dict(prediction["changed_features"])
    result.update(
        {
            "success": True,
            "grid_id": prediction["grid_id"],
            "gu_name": prediction["gu_name"],
            "applied_changes": applied_changes,
            "before_anomaly": prediction["before_anomaly"],
            "after_anomaly": prediction["after_anomaly"],
            "delta_c": prediction["delta_c"],
            "uncertainty_std": prediction["uncertainty_std"],
            "changed_features": applied_changes,
            "message": prediction.get("message"),
            "warnings": list(prediction["warnings"]),
        }
    )
    return result


TOOL_FUNCTIONS = {
    "get_grid_data": get_grid_data,
    "run_simulation": run_simulation,
}
