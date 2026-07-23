"""Qwen에 제공할 GA:ON 격자 조회 도구."""

from __future__ import annotations

import math
from typing import Any

from backend.ml import predict_core


GET_GRID_DATA_TOOL = {
    "type": "function",
    "function": {
        "name": "get_grid_data",
        "description": (
            "GA:ON 데이터셋에서 grid_id에 해당하는 자치구명, 녹지율, "
            "불투수율을 조회한다. 비율은 0~1 원본값으로 반환한다."
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


TOOL_FUNCTIONS = {
    "get_grid_data": get_grid_data,
}
