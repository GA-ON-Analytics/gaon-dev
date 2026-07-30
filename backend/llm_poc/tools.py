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

# backend/models/feature_meta.json의 현재 모델 입력 필드를 조회용으로
# 정리한 중앙 메타데이터다. feature_meta에 단위가 명시되지 않은 지표는
# 임의로 추정하지 않고 빈 문자열로 둔다.
GRID_FIELD_SPECS: dict[str, dict[str, Any]] = {
    "building_ratio": {
        "label": "건물 바닥면적 비율",
        "description": "격자에서 건물이 땅을 덮은 비율",
        "unit": "%",
        "aliases": ("건물", "건물 비율", "건물밀도", "건폐율", "건물 줄이기"),
        "is_ratio": True,
    },
    "avg_ground_floor_count": {
        "label": "평균 지상층수",
        "description": "건물들의 평균 층수",
        "unit": "층",
        "aliases": ("층수", "고층", "저층"),
        "is_ratio": False,
    },
    "max_ground_floor_count": {
        "label": "최대 지상층수",
        "description": "가장 높은 건물의 층수",
        "unit": "층",
        "aliases": ("최고층", "최대높이"),
        "is_ratio": False,
    },
    "floor_area_ratio_proxy": {
        "label": "연면적비 proxy",
        "description": "건물 총량 추정(용적률 유사)",
        "unit": "",
        "aliases": ("용적률", "연면적", "개발밀도"),
        "is_ratio": False,
    },
    "road_ratio": {
        "label": "도로율",
        "description": "격자에서 도로가 차지하는 비율",
        "unit": "%",
        "aliases": ("도로", "포장도로"),
        "is_ratio": True,
    },
    "zoning_residential_ratio": {
        "label": "주거지역 비율",
        "description": "주거 용도지역 비율",
        "unit": "%",
        "aliases": ("주거지역", "주거"),
        "is_ratio": True,
    },
    "zoning_commercial_ratio": {
        "label": "상업지역 비율",
        "description": "상업 용도지역 비율",
        "unit": "%",
        "aliases": ("상업지역", "상업"),
        "is_ratio": True,
    },
    "zoning_industrial_ratio": {
        "label": "공업지역 비율",
        "description": "공업 용도지역 비율",
        "unit": "%",
        "aliases": ("공업지역", "공장"),
        "is_ratio": True,
    },
    "zoning_green_ratio": {
        "label": "녹지지역 비율",
        "description": "용도지역상 녹지 비율",
        "unit": "%",
        "aliases": ("녹지지역", "그린벨트"),
        "is_ratio": True,
    },
    "ndvi": {
        "label": "식생지수",
        "description": "위성이 본 식물의 푸르름(나무·풀의 양/건강)",
        "unit": "",
        "aliases": (
            "NDVI",
            "식생지수",
            "나무",
            "식생",
            "수목",
            "가로수",
            "나무 심기",
            "녹화",
        ),
        "is_ratio": False,
    },
    "green_ratio": {
        "label": "녹지율",
        "description": "녹지성 토지피복 비율(공원·잔디 등)",
        "unit": "%",
        "aliases": ("녹지", "공원", "잔디", "녹지 확대"),
        "is_ratio": True,
    },
    "impervious_ratio": {
        "label": "불투수면 비율",
        "description": "물이 스미지 않는 포장면 비율",
        "unit": "%",
        "aliases": (
            "불투수율",
            "불투수면",
            "포장면",
            "아스팔트",
            "콘크리트",
            "투수포장",
        ),
        "is_ratio": True,
    },
    "built_surface_ratio": {
        "label": "시가화면 비율",
        "description": "건조물·인공표면 비율(불투수와 동반)",
        "unit": "%",
        "aliases": ("시가화", "인공표면"),
        "is_ratio": True,
    },
    "nearest_park_distance_m": {
        "label": "최근접 공원거리(m)",
        "description": "가장 가까운 공원까지 거리",
        "unit": "m",
        "aliases": ("공원까지 거리", "공원 거리", "공원 접근성"),
        "is_ratio": False,
    },
    "park_area_within_500m": {
        "label": "500m내 공원면적(㎡)",
        "description": "반경 500m 안 공원 총면적",
        "unit": "㎡",
        "aliases": ("500m 내 공원 면적", "주변 공원", "공원 면적"),
        "is_ratio": False,
    },
    "nearest_stream_distance_m": {
        "label": "최근접 하천거리(m)",
        "description": "가장 가까운 하천까지 거리",
        "unit": "m",
        "aliases": ("하천까지 거리", "하천 거리", "물가"),
        "is_ratio": False,
    },
    "elevation_m": {
        "label": "표고(m)",
        "description": "해발 고도",
        "unit": "m",
        "aliases": ("고도", "표고", "산"),
        "is_ratio": False,
    },
    "slope_deg": {
        "label": "경사(도)",
        "description": "지형 경사",
        "unit": "°",
        "aliases": ("경사도", "경사", "비탈"),
        "is_ratio": False,
    },
    "albedo": {
        "label": "표면 반사율",
        "description": "표면이 빛을 반사하는 정도(밝을수록 덜 뜨거움)",
        "unit": "",
        "aliases": (
            "알베도",
            "반사율",
            "밝은 지붕",
            "차열도장",
            "쿨루프",
            "밝은 표면",
        ),
        "is_ratio": False,
    },
}
def _model_grid_fields() -> tuple[str, ...]:
    """현재 predict_core가 공개한 모델 입력 필드만 조회 대상으로 삼는다."""

    try:
        feature_meta = predict_core.feature_meta()
    except Exception:
        return tuple(GRID_FIELD_SPECS)

    model_fields = {
        item.get("name")
        for item in feature_meta
        if isinstance(item, Mapping) and isinstance(item.get("name"), str)
    }
    return tuple(field for field in GRID_FIELD_SPECS if field in model_fields)


ALLOWED_GRID_FIELDS = _model_grid_fields()
DEFAULT_GRID_FIELDS = ("green_ratio", "impervious_ratio")
GRID_FIELD_DISPLAY_DECIMALS = {
    "building_ratio": 2,
    "avg_ground_floor_count": 2,
    "max_ground_floor_count": 0,
    "floor_area_ratio_proxy": 4,
    "road_ratio": 2,
    "zoning_residential_ratio": 2,
    "zoning_commercial_ratio": 2,
    "zoning_industrial_ratio": 2,
    "zoning_green_ratio": 2,
    "ndvi": 4,
    "green_ratio": 2,
    "impervious_ratio": 2,
    "built_surface_ratio": 2,
    "nearest_park_distance_m": 2,
    "park_area_within_500m": 2,
    "nearest_stream_distance_m": 2,
    "elevation_m": 2,
    "slope_deg": 2,
    "albedo": 4,
}
for _field, _decimals in GRID_FIELD_DISPLAY_DECIMALS.items():
    GRID_FIELD_SPECS[_field]["display_decimals"] = _decimals


def format_grid_field_value(field: str, value: float) -> str:
    """중앙 표시 규칙에 따라 원본 격자 값을 사용자용 문자열로 변환한다."""

    spec = GRID_FIELD_SPECS[field]
    number = float(value)
    decimals = int(spec["display_decimals"])
    if spec["is_ratio"]:
        return f"{number * 100:.{decimals}f}%"

    if math.isclose(number, 0.0, rel_tol=0, abs_tol=0.5 * (10 ** -decimals)):
        number = 0.0
    use_grouping = field == "park_area_within_500m"
    formatted = (
        f"{number:,.{decimals}f}"
        if use_grouping
        else f"{number:.{decimals}f}"
    )
    if "." in formatted:
        formatted = formatted.rstrip("0").rstrip(".")
    return f"{formatted}{spec['unit']}"

GET_GRID_DATA_TOOL = {
    "type": "function",
    "function": {
        "name": "get_grid_data",
        "description": (
            "GA:ON 데이터셋에서 grid_id에 해당하는 자치구명과 요청한 도시환경 "
            "필드를 조회한다. fields를 생략하면 녹지율과 불투수율을 조회하며, "
            "비율 필드는 0~1 원본값으로 반환한다. "
            "정책 변경 후 모델 결과가 아니라 단순 현재 데이터 조회에만 사용한다."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "grid_id": {
                    "type": "string",
                    "description": "조회할 서울 격자 ID. 예: 11230_00001",
                },
                "fields": {
                    "type": "array",
                    "description": (
                        "조회할 도시환경 필드 목록. 생략하면 green_ratio와 "
                        "impervious_ratio를 조회한다."
                    ),
                    "items": {
                        "type": "string",
                        "enum": list(ALLOWED_GRID_FIELDS),
                    },
                    "minItems": 1,
                    "uniqueItems": True,
                    "default": list(DEFAULT_GRID_FIELDS),
                },
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
            "사용한다. 단순 현재 격자 데이터 조회에는 get_grid_data를 사용한다. "
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


def _empty_result(
    grid_id: str | None,
    requested_fields: list[str] | None = None,
) -> dict[str, Any]:
    return {
        "success": False,
        "grid_id": grid_id,
        "gu_name": None,
        "requested_fields": list(requested_fields or []),
        "values": {},
        "field_metadata": {},
        "answer_prefix": None,
        "answer_template": None,
        "error": None,
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
        "delta_std": None,
        "warnings": [],
        "policy_direction_notes": [],
        "interpretation_basis": INTERPRETATION_BASIS,
        "limitations": list(LIMITATIONS),
    }


def _validated_delta(name: str, value: Any) -> tuple[float | None, str | None]:
    if not _is_finite_number(value):
        return None, f"{name}는 NaN이나 무한대가 아닌 숫자여야 합니다."
    return float(value), None


def _normalize_grid_fields(
    fields: list[str] | None,
) -> tuple[list[str], list[str], str | None]:
    if fields is None:
        return list(DEFAULT_GRID_FIELDS), [], None
    if not isinstance(fields, list):
        return [], [], "fields는 필드명 문자열 배열이어야 합니다."
    if not fields:
        return [], [], "fields는 하나 이상의 필드명을 포함해야 합니다."

    requested_fields: list[str] = []
    unsupported_fields: list[str] = []
    seen: set[str] = set()
    for field in fields:
        if not isinstance(field, str):
            unsupported_fields.append(str(field))
            continue
        if field in seen:
            continue
        seen.add(field)
        requested_fields.append(field)
        if field not in ALLOWED_GRID_FIELDS:
            unsupported_fields.append(field)

    if unsupported_fields:
        return (
            requested_fields,
            unsupported_fields,
            "지원하지 않는 조회 필드가 포함되어 있습니다.",
        )
    return requested_fields, [], None


def get_grid_data(
    grid_id: str,
    fields: list[str] | None = None,
) -> dict[str, Any]:
    """기존 ML 조회 함수로 요청한 도시환경 필드들을 반환한다.

    Args:
        grid_id: 조회할 서울 격자 ID.
        fields: 조회할 모델 입력 필드 목록. 생략하면 녹지율·불투수율.

    Returns:
        성공 여부, 격자 ID, 자치구명, 요청 필드와 원본 값 매핑.
        실패 시에도 같은 기본 필드들과 명확한 ``error``를 반환한다.
    """

    normalized_grid_id = grid_id.strip() if isinstance(grid_id, str) else None
    requested_fields, unsupported_fields, fields_error = _normalize_grid_fields(
        fields
    )
    result = _empty_result(normalized_grid_id, requested_fields)

    if fields_error is not None:
        result["error"] = fields_error
        result["available_fields"] = list(ALLOWED_GRID_FIELDS)
        if unsupported_fields:
            result["unsupported_fields"] = unsupported_fields
        return result

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

    result["gu_name"] = features.get("_gu_name")
    values = {field: features.get(field) for field in requested_fields}
    missing_fields = [
        field
        for field, value in values.items()
        if _is_missing(value) or not _is_finite_number(value)
    ]
    if _is_missing(result["gu_name"]):
        missing_fields.insert(0, "gu_name")
    if missing_fields:
        result["missing_fields"] = missing_fields
        result["error"] = "필수 격자 데이터가 누락되었습니다."
        return result

    result["values"] = {field: float(values[field]) for field in requested_fields}
    result["field_metadata"] = {
        field: {
            "label": GRID_FIELD_SPECS[field]["label"],
            "unit": GRID_FIELD_SPECS[field]["unit"],
            "is_ratio": GRID_FIELD_SPECS[field]["is_ratio"],
            "display_value": format_grid_field_value(field, float(values[field])),
        }
        for field in requested_fields
    }
    result["answer_prefix"] = (
        f"{normalized_grid_id} 격자({result['gu_name']})의"
    )
    result["answer_template"] = (
        f"{result['answer_prefix']} 현재 데이터입니다.\n"
        + "\n".join(
            f"- {result['field_metadata'][field]['label']}: "
            f"{result['field_metadata'][field]['display_value']}"
            for field in requested_fields
        )
    )
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
        "delta_std",
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
        "delta_std",
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
            "delta_std": prediction["delta_std"],
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
