"""Qwen에 제공할 GA:ON 격자 조회·정책 시뮬레이션 도구."""

from __future__ import annotations

import math
from collections.abc import Mapping
from functools import lru_cache
from numbers import Real
from typing import Any

from backend.ml import predict_core


INTERPRETATION_BASIS = (
    "before_anomaly와 after_anomaly는 절대온도가 아니라 "
    "머신러닝 모델이 반환한 예측 anomaly이며, "
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
        "semantic_definition": "격자 면적 중 건물 바닥이 실제로 땅을 덮은 비율",
        "unit": "%",
        "aliases": ("건물 비율", "건물밀도", "건폐율", "건물 줄이기"),
        "confusable_with": ("floor_area_ratio_proxy",),
        "examples": ("건물이 땅을 덮은 비율", "건물 바닥이 차지하는 정도"),
        "disambiguation_cues": ("땅을 덮", "바닥면적", "건폐"),
        "category": "건축",
        "is_ratio": True,
    },
    "avg_ground_floor_count": {
        "label": "평균 지상층수",
        "description": "건물들의 평균 층수",
        "semantic_definition": "격자 안 건물들의 지상층수를 평균한 값",
        "unit": "층",
        "aliases": ("평균 층수", "평균층수"),
        "confusable_with": ("max_ground_floor_count",),
        "examples": ("건물들이 평균적으로 몇 층", "보통 건물 높이"),
        "disambiguation_cues": ("평균적으로", "평균 층", "평균층"),
        "category": "건축",
        "is_ratio": False,
    },
    "max_ground_floor_count": {
        "label": "최대 지상층수",
        "description": "가장 높은 건물의 층수",
        "semantic_definition": "격자 안에서 지상층수가 가장 큰 건물의 층수",
        "unit": "층",
        "aliases": ("최고층", "최대높이"),
        "confusable_with": ("avg_ground_floor_count",),
        "examples": ("가장 높은 건물이 몇 층", "최고층 건물 높이"),
        "disambiguation_cues": ("가장 높은", "최고층", "최대 층"),
        "category": "건축",
        "is_ratio": False,
    },
    "floor_area_ratio_proxy": {
        "label": "연면적비 proxy",
        "description": "건물 총량 추정(용적률 유사)",
        "semantic_definition": "건물 총 연면적 수준을 추정하는 용적률 유사 지표",
        "unit": "",
        "aliases": ("용적률", "연면적", "개발밀도"),
        "confusable_with": ("building_ratio",),
        "examples": ("건물 총 연면적 수준", "용적률과 비슷한 지표"),
        "category": "건축",
        "is_ratio": False,
    },
    "road_ratio": {
        "label": "도로율",
        "description": "격자에서 도로가 차지하는 비율",
        "semantic_definition": "격자 전체 면적 중 도로가 차지하는 비율",
        "unit": "%",
        "aliases": ("도로", "포장도로"),
        "examples": ("도로가 전체 땅에서 차지하는 정도",),
        "category": "토지·용도",
        "is_ratio": True,
    },
    "zoning_residential_ratio": {
        "label": "주거지역 비율",
        "description": "주거 용도지역 비율",
        "semantic_definition": "격자 면적 중 용도지역상 주거지역으로 지정된 비율",
        "unit": "%",
        "aliases": ("주거지역", "주거"),
        "examples": ("주거 용도로 지정된 비율",),
        "category": "토지·용도",
        "is_ratio": True,
    },
    "zoning_commercial_ratio": {
        "label": "상업지역 비율",
        "description": "상업 용도지역 비율",
        "semantic_definition": "격자 면적 중 용도지역상 상업지역으로 지정된 비율",
        "unit": "%",
        "aliases": ("상업지역", "상업"),
        "examples": ("상업 용도로 지정된 비율",),
        "category": "토지·용도",
        "is_ratio": True,
    },
    "zoning_industrial_ratio": {
        "label": "공업지역 비율",
        "description": "공업 용도지역 비율",
        "semantic_definition": "격자 면적 중 용도지역상 공업지역으로 지정된 비율",
        "unit": "%",
        "aliases": ("공업지역", "공장"),
        "examples": ("공업 용도로 지정된 비율",),
        "category": "토지·용도",
        "is_ratio": True,
    },
    "zoning_green_ratio": {
        "label": "녹지지역 비율",
        "description": "용도지역상 녹지 비율",
        "semantic_definition": "격자 면적 중 용도지역상 녹지지역으로 지정된 비율",
        "unit": "%",
        "aliases": ("녹지지역", "그린벨트"),
        "confusable_with": ("green_ratio",),
        "examples": ("녹지지역으로 지정된 비율", "용도지역상 녹지 비중"),
        "category": "토지·용도",
        "is_ratio": True,
    },
    "ndvi": {
        "label": "식생지수",
        "description": "위성이 본 식물의 푸르름(나무·풀의 양/건강)",
        "semantic_definition": "식생의 푸르름·활력·상태를 나타내는 무단위 위성 지수",
        "unit": "",
        "aliases": (
            "NDVI",
            "식생지수",
        ),
        "examples": ("식생이 얼마나 푸른지", "식물의 활력 상태", "앤디브이아이"),
        "disambiguation_cues": ("푸른", "푸르", "활력", "건강"),
        "category": "녹지·피복",
        "is_ratio": False,
    },
    "green_ratio": {
        "label": "녹지율",
        "description": "녹지성 토지피복 비율(공원·잔디 등)",
        "semantic_definition": "격자 면적 중 실제 토지피복상 녹지가 차지하는 비율",
        "unit": "%",
        "aliases": ("녹지 비율", "녹지 비중", "녹지 확대"),
        "confusable_with": ("zoning_green_ratio",),
        "examples": ("녹지가 차지하는 비중", "초록 공간의 면적 비율"),
        "disambiguation_cues": ("차지하는 비율", "면적 비율", "면적 비중"),
        "category": "녹지·피복",
        "is_ratio": True,
    },
    "impervious_ratio": {
        "label": "불투수면 비율",
        "description": "물이 스미지 않는 포장면 비율",
        "semantic_definition": "격자 면적 중 물이 스며들지 않는 포장·인공 표면의 비율",
        "unit": "%",
        "aliases": (
            "불투수율",
            "불투수면",
            "포장면",
            "아스팔트",
            "콘크리트",
            "투수포장",
        ),
        "examples": ("물이 스며들지 않는 땅의 비율", "빗물이 안 스미는 면적"),
        "category": "녹지·피복",
        "is_ratio": True,
    },
    "nearest_park_distance_m": {
        "label": "최근접 공원거리(m)",
        "description": "가장 가까운 공원까지 거리",
        "semantic_definition": "격자에서 가장 가까운 공원까지의 직선거리",
        "unit": "m",
        "aliases": ("공원까지 거리", "공원 거리", "공원 접근성"),
        "confusable_with": ("park_area_within_500m",),
        "examples": ("가장 가까운 공원까지 거리",),
        "category": "공원·지형",
        "is_ratio": False,
    },
    "park_area_within_500m": {
        "label": "500m내 공원면적(㎡)",
        "description": "반경 500m 안 공원 총면적",
        "semantic_definition": "격자 중심 반경 500m 안에 포함된 공원의 총면적",
        "unit": "㎡",
        "aliases": ("500m 내 공원 면적", "주변 공원", "공원 면적"),
        "confusable_with": ("nearest_park_distance_m",),
        "examples": ("주변 500m 안 공원 총면적",),
        "category": "공원·지형",
        "is_ratio": False,
    },
    "nearest_stream_distance_m": {
        "label": "최근접 하천거리(m)",
        "description": "가장 가까운 하천까지 거리",
        "semantic_definition": "격자에서 가장 가까운 하천까지의 직선거리",
        "unit": "m",
        "aliases": ("하천까지 거리", "하천 거리", "물가"),
        "examples": ("가장 가까운 하천까지 거리",),
        "category": "공원·지형",
        "is_ratio": False,
    },
    "elevation_m": {
        "label": "표고(m)",
        "description": "해발 고도",
        "semantic_definition": "격자 지표면의 해수면 기준 높이",
        "unit": "m",
        "aliases": ("고도", "표고", "산"),
        "examples": ("해발 고도", "지대가 얼마나 높은지"),
        "category": "공원·지형",
        "is_ratio": False,
    },
    "slope_deg": {
        "label": "경사(도)",
        "description": "지형 경사",
        "semantic_definition": "격자 지표면이 기울어진 각도",
        "unit": "°",
        "aliases": ("경사도", "경사", "비탈"),
        "examples": ("땅이 얼마나 가파른지",),
        "category": "공원·지형",
        "is_ratio": False,
    },
    "albedo": {
        "label": "표면 반사율",
        "description": "표면이 빛을 반사하는 정도(밝을수록 덜 뜨거움)",
        "semantic_definition": "지표면이 들어오는 햇빛을 반사하는 정도를 나타내는 무단위 지수",
        "unit": "",
        "aliases": (
            "알베도",
            "반사율",
            "밝은 지붕",
            "차열도장",
            "쿨루프",
            "밝은 표면",
        ),
        "examples": ("표면이 햇빛을 얼마나 반사하는지", "햇빛 반사 정도"),
        "category": "녹지·피복",
        "is_ratio": False,
    },
}

# ── 지표 출처 ────────────────────────────────────────────────────────────────
#
# 챗봇이 "이 데이터 출처가 어디야?"에 답하려면 출처가 데이터로 있어야 한다.
# 지금까지 GRID_FIELD_SPECS에는 label·description·unit·category만 있어서,
# 출처 질문은 코퍼스 문서 검색(RAG)으로 흘렀다. 문서 수치는 작성 시점 값이라
# 출처처럼 변하지 않는 사실을 RAG로 답하게 두면 틀릴 여지만 는다.
#
# 아래 값은 전부 `GAON_ML_전과정_정리_ko.md` 4.2절 표에서 그대로 옮긴 것이다.
# ★ 추정해서 채우지 말 것. 새 필드가 생기면 그 문서를 먼저 갱신하고 여기 옮긴다.
_GEE_DW = "Google Earth Engine · Dynamic World"
_VWORLD = "국토교통부 VWorld"

_FIELD_SOURCES: dict[str, str] = {
    "building_ratio": f"{_VWORLD} 건물 `lt_c_spbd`",
    "avg_ground_floor_count": f"{_VWORLD} 건물 `lt_c_spbd`",
    "max_ground_floor_count": f"{_VWORLD} 건물 `lt_c_spbd`",
    "floor_area_ratio_proxy": f"파생 (바닥면적 × 층수 ÷ 격자면적, 원자료 {_VWORLD} 건물)",
    "road_ratio": f"{_VWORLD} 도로 `lt_c_upisuq151`",
    "zoning_residential_ratio": f"{_VWORLD} 용도지역 `lt_c_uq111`",
    "zoning_commercial_ratio": f"{_VWORLD} 용도지역",
    "zoning_industrial_ratio": f"{_VWORLD} 용도지역",
    "zoning_green_ratio": f"{_VWORLD} 용도지역",
    "ndvi": "Google Earth Engine · Sentinel-2",
    "green_ratio": _GEE_DW,
    "impervious_ratio": _GEE_DW,
    "nearest_park_distance_m": f"{_VWORLD} 공원 `lt_c_upisuq161`",
    "park_area_within_500m": f"{_VWORLD} 공원 `lt_c_upisuq161`",
    "nearest_stream_distance_m": f"{_VWORLD} 하천 `lt_c_wkmstrm`",
    "elevation_m": "Google Earth Engine · SRTM DEM",
    "slope_deg": "Google Earth Engine · SRTM DEM",
    "albedo": "Google Earth Engine · Landsat 표면반사율",
}

for _field, _spec in GRID_FIELD_SPECS.items():
    _spec["source"] = _FIELD_SOURCES[_field]

# 출처 없는 필드가 조용히 생기면 챗봇이 그 지표만 출처를 못 댄다.
# 필드를 추가하고 출처를 빠뜨리는 실수를 import 시점에 잡는다.
if _FIELD_SOURCES.keys() != GRID_FIELD_SPECS.keys():
    raise RuntimeError(
        "_FIELD_SOURCES가 GRID_FIELD_SPECS와 어긋났습니다. "
        f"출처 없음={sorted(GRID_FIELD_SPECS.keys() - _FIELD_SOURCES.keys())} "
        f"필드 없음={sorted(_FIELD_SOURCES.keys() - GRID_FIELD_SPECS.keys())}"
    )


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
            "특정 100m 격자에 녹지율, 불투수율, 식생지수(NDVI) 또는 "
            "표면 반사율(albedo)의 "
            "변경 시나리오를 적용하고 기존 머신러닝 모델을 다시 실행한다. "
            "정책 변경 후 모델 예측 anomaly와 모델 기준 예상 변화량을 묻는 경우에만 "
            "사용한다. 단순 현재 격자 데이터 조회에는 get_grid_data를 사용한다. "
            "비율 변화량은 0~1 단위의 부호 있는 delta이므로 5%p 증가는 0.05, "
            "5%p 감소는 -0.05이다. NDVI와 albedo 변화량은 퍼센트가 아닌 "
            "무단위 지수 delta이다."
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
                "ndvi_delta": {
                    "type": "number",
                    "default": 0,
                    "description": (
                        "현재 식생지수(NDVI)에 더할 부호 있는 무단위 변화량. "
                        "예: 0.05 증가는 0.05, 0.05 감소는 -0.05이다."
                    ),
                },
                "albedo_delta": {
                    "type": "number",
                    "default": 0,
                    "description": (
                        "현재 표면 반사율(albedo)에 더할 부호 있는 무단위 변화량. "
                        "예: 0.02 증가는 0.02, 0.02 감소는 -0.02이다."
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
        "auto_applied_changes": {},
        "changed_features": {},
        "before_anomaly": None,
        "after_anomaly": None,
        "delta_c": None,
        "uncertainty_std": None,
        "delta_std": None,
        "direction_confidence": None,
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
    ndvi_delta: float = 0,
    albedo_delta: float = 0,
    # 라우터는 이 레버를 직접 내주지 않는다. 정책 프리셋(도로 가로녹지화)이
    # 도로 비율을 줄이기 때문에 simulate_policy가 쓰려고 열어 둔 자리다.
    road_ratio_delta: float = 0,
) -> dict[str, Any]:
    """기존 ``predict_core.predict``로 100m 격자 정책 시나리오를 실행한다.

    녹지율·불투수율 변화량은 현재 0~1 원본 비율에 더할 부호 있는 delta이고,
    NDVI·albedo 변화량은 무단위 지수 delta이다. 학습 범위 clip, 녹지율 변경에
    따른 불투수율 자동 연동, 경고 생성은 기존 ``predict``에 그대로 위임한다.
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
        ("ndvi_delta", ndvi_delta),
        ("albedo_delta", albedo_delta),
        ("road_ratio_delta", road_ratio_delta),
    ):
        normalized_value, error = _validated_delta(name, value)
        if error is not None:
            result["error"] = error
            return result
        validated_deltas[name] = normalized_value

    requested_changes: dict[str, float] = {}
    if validated_deltas["green_ratio_delta"]:
        requested_changes["green_ratio"] = validated_deltas["green_ratio_delta"]
    if validated_deltas["impervious_ratio_delta"]:
        requested_changes["impervious_ratio"] = validated_deltas[
            "impervious_ratio_delta"
        ]
    if validated_deltas["ndvi_delta"]:
        requested_changes["ndvi"] = validated_deltas["ndvi_delta"]
    if validated_deltas["albedo_delta"]:
        requested_changes["albedo"] = validated_deltas["albedo_delta"]
    if validated_deltas["road_ratio_delta"]:
        requested_changes["road_ratio"] = validated_deltas["road_ratio_delta"]
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
    if validated_deltas["ndvi_delta"] < 0:
        policy_direction_notes.append(
            "식생지수(NDVI) 감소는 일반적인 열 저감 정책 방향과 반대인 시나리오입니다."
        )
    if validated_deltas["albedo_delta"] < 0:
        policy_direction_notes.append(
            "표면 반사율(albedo) 감소는 일반적인 열 저감 정책 방향과 반대인 시나리오입니다."
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
        prediction = predict_core.predict(
            normalized_grid_id,
            requested_changes,
            couple_land_cover=True,
        )
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
    direction_confidence = prediction.get("direction_confidence")
    if "direction_confidence" not in prediction:
        missing_fields.append("direction_confidence")
    elif direction_confidence is not None and (
        not _is_finite_number(direction_confidence)
        or not 0.0 <= float(direction_confidence) <= 1.0
    ):
        missing_fields.append("direction_confidence")
    invalid_fields = sorted(set(missing_fields + invalid_numeric_fields))
    if invalid_fields:
        result["missing_fields"] = invalid_fields
        result["error"] = "시뮬레이션 필수 반환값이 누락되었거나 올바르지 않습니다."
        return result

    applied_changes = dict(prediction["changed_features"])
    auto_applied_changes = {
        field: change
        for field, change in applied_changes.items()
        if field not in requested_changes
    }
    result.update(
        {
            "success": True,
            "grid_id": prediction["grid_id"],
            "gu_name": prediction["gu_name"],
            "applied_changes": applied_changes,
            "auto_applied_changes": auto_applied_changes,
            "before_anomaly": prediction["before_anomaly"],
            "after_anomaly": prediction["after_anomaly"],
            "delta_c": prediction["delta_c"],
            "uncertainty_std": prediction["uncertainty_std"],
            "delta_std": prediction["delta_std"],
            "direction_confidence": direction_confidence,
            "changed_features": applied_changes,
            "message": prediction.get("message"),
            "warnings": list(prediction["warnings"]),
        }
    )
    return result


# 정책 후보 4개. 시나리오 크기를 바꾸면 순위가 바뀌므로 답변에 반드시 명시해야
# 하는데, LLM이 지어내면 안 되므로 문구까지 Tool이 돌려준다.
# park_area_within_500m은 #18 역방향 학습으로 제외한다.
POLICY_SCENARIOS: tuple[dict[str, Any], ...] = (
    {
        "label": "녹지 확대",
        "feature": "green_ratio",
        "argument": "green_ratio_delta",
        "delta": 0.05,
        "scenario_note": "녹지 +5%p",
    },
    {
        "label": "식생 활력 개선",
        "feature": "ndvi",
        "argument": "ndvi_delta",
        "delta": 0.05,
        "scenario_note": "NDVI +0.05",
    },
    {
        "label": "불투수면 저감",
        "feature": "impervious_ratio",
        "argument": "impervious_ratio_delta",
        "delta": -0.05,
        "scenario_note": "불투수면 -5%p",
    },
    {
        "label": "쿨루프(알베도)",
        "feature": "albedo",
        "argument": "albedo_delta",
        "delta": 0.02,
        "scenario_note": "알베도 +0.02",
    },
)
# 데이터 부트스트랩 8회 재학습으로 얻은 추정오차다. 이보다 작은 차이는 순위로
# 구분할 수 없다. delta_std(트리 산포)는 실제 오차의 8.3배라 여기 쓰지 않는다.
TIE_BAND_C = 0.132
# 순위에 오른 정책. 냉각 방향이고 동률 밴드를 넘으며 시행 여지도 있는 경우.
POLICY_STATE_RANKED = "ranked"
# 밴드는 넘었는데 온도가 올라가는 정책. 60격자 측정에서는 0건이었으나 표본이
# 서울 전체가 아니다. 이 상태가 없으면 abs()로 정렬해 온난화 정책을 1위로
# 추천하면서 아무 경고도 내지 않는다.
POLICY_STATE_ADVERSE = "adverse"
POLICY_STATE_INDISTINGUISHABLE = "indistinguishable"
# clip이 나서 요청한 만큼 반영되지 않은 경우. "효과 없음"과 다르다.
POLICY_STATE_NO_ROOM = "no_room"
POLICY_STATE_UNRESPONSIVE = "unresponsive"
RANK_SCENARIO_NOTE = (
    " · ".join(scenario["scenario_note"] for scenario in POLICY_SCENARIOS) + " 기준"
)


# 직접 지정한 레버 자체가 요청량만큼 못 들어간 경우.
POLICY_CLIP_DIRECT = "direct"
# 레버는 다 들어갔는데 연동돼 따라가야 할 불투수면이 하한에 걸린 경우.
POLICY_CLIP_COUPLED = "coupled"


def _policy_clip_reason(sim: Mapping[str, Any]) -> str | None:
    """요청한 변경이 그대로 반영되지 않았다면 그 이유를 돌려준다.

    경고 문자열만 보면 녹지 연동 clip 9건 중 8건을 놓친다(240건 실측:
    직접 레버 1건 대 연동 8건 단독). 두 갈래를 모두 보되, 답변에서 둘을
    다르게 설명해야 하므로 bool이 아니라 이유를 돌려준다.
    """

    requested = sim.get("requested_changes") or {}
    changed = sim.get("changed_features") or {}

    # 갈래 1 — 직접 지정한 레버가 요청량만큼 움직였는가.
    # 경고 문자열 대조보다 이쪽이 낫다. predict_core가 경고 문구를 바꿔도
    # 조용히 깨지지 않는다.
    for field, wanted in requested.items():
        change = changed.get(field)
        if not isinstance(change, Mapping):
            return POLICY_CLIP_DIRECT
        applied = change["after"] - change["before"]
        if not math.isclose(applied, wanted, rel_tol=0.0, abs_tol=1e-4):
            return POLICY_CLIP_DIRECT

    # 갈래 2 — 녹지에 연동돼 자동으로 움직인 불투수면이 관측 기울기만큼
    # 따라왔는가. 녹지는 다 들어갔는데 불투수면이 하한에 걸리는 경우가 있다.
    green = changed.get("green_ratio")
    impervious = changed.get("impervious_ratio")
    if isinstance(green, Mapping) and isinstance(impervious, Mapping):
        applied = green["after"] - green["before"]
        expected = applied * predict_core.GREEN_TO_IMPERVIOUS
        actual = impervious["after"] - impervious["before"]
        if not math.isclose(actual, expected, rel_tol=0.0, abs_tol=1e-4):
            return POLICY_CLIP_COUPLED
    return None


def _policy_state(sim: Mapping[str, Any], clipped: bool) -> str:
    """정책 하나를 5상태로 분류한다. ★ 판정 순서가 곧 답이다.

    clip을 동률 밴드보다 먼저 보는 이유: clip이 났다는 건 요청한 만큼
    반영되지 않았다는 뜻이라 그때의 delta_c는 "효과가 작다"가 아니라
    "그만큼 못 넣었다"다. 순서를 뒤집으면 "녹지는 효과가 없습니다"라고
    답하게 되는데, 사실은 "이 격자엔 녹지를 더 넣을 땅이 없습니다"다.
    """

    if sim.get("direction_confidence") is None:
        return POLICY_STATE_UNRESPONSIVE
    if clipped:
        return POLICY_STATE_NO_ROOM
    delta_c = sim["delta_c"]
    if abs(delta_c) < TIE_BAND_C:
        return POLICY_STATE_INDISTINGUISHABLE
    if delta_c > 0:
        return POLICY_STATE_ADVERSE
    return POLICY_STATE_RANKED


def _empty_rank_result(grid_id: str | None) -> dict[str, Any]:
    return {
        "success": False,
        "grid_id": grid_id,
        "gu_name": None,
        "policies": [],
        "ranked_count": 0,
        "tie_band_c": TIE_BAND_C,
        "scenario_note": RANK_SCENARIO_NOTE,
        "interpretation_basis": INTERPRETATION_BASIS,
        "limitations": list(LIMITATIONS),
        "error": None,
    }


def rank_policies(grid_id: str) -> dict[str, Any]:
    """정책 4개를 그 격자에 적용해 5상태로 분류한다. LLM은 호출하지 않는다.

    순위는 냉각 방향(delta_c < 0)만 매기고 delta_c 오름차순으로 정렬한다.
    가장 많이 내려간 정책이 1위다.

    시뮬레이션은 predict_core.predict를 직접 부르지 않고 run_simulation에
    위임한다. 필수 반환값·유한성·direction_confidence 범위 검사를 복제하면
    반드시 갈라지기 때문이다.
    """

    normalized_grid_id = grid_id.strip() if isinstance(grid_id, str) else None
    result = _empty_rank_result(normalized_grid_id)
    if not normalized_grid_id:
        result["error"] = "grid_id가 필요합니다."
        return result

    policies: list[dict[str, Any]] = []
    gu_name: str | None = None
    for scenario in POLICY_SCENARIOS:
        sim = run_simulation(
            normalized_grid_id,
            **{scenario["argument"]: scenario["delta"]},
        )
        if not sim.get("success"):
            # 실패는 격자·모델 파일 단위라 정책 하나만 빠지는 일이 없다.
            # 부분 순위는 1위였을 정책이 빠진 채 나갈 수 있어 더 위험하다.
            result["gu_name"] = sim.get("gu_name")
            result["error"] = sim.get("error") or "정책 시뮬레이션에 실패했습니다."
            return result
        gu_name = sim["gu_name"]
        clip_reason = _policy_clip_reason(sim)
        policies.append(
            {
                "label": scenario["label"],
                "feature": scenario["feature"],
                "delta": scenario["delta"],
                "scenario_note": scenario["scenario_note"],
                "delta_c": sim["delta_c"],
                "state": _policy_state(sim, clip_reason is not None),
                "clipped": clip_reason is not None,
                "clip_reason": clip_reason,
                "applied": dict(sim["changed_features"]),
                "rank": None,
            }
        )

    ranked = sorted(
        (p for p in policies if p["state"] == POLICY_STATE_RANKED),
        key=lambda p: p["delta_c"],
    )
    for position, policy in enumerate(ranked, start=1):
        policy["rank"] = position
    # 순위에 오른 것을 앞으로, 나머지는 선언 순서를 유지한다.
    rest = [p for p in policies if p["state"] != POLICY_STATE_RANKED]

    result.update(
        {
            "success": True,
            "gu_name": gu_name,
            "policies": ranked + rest,
            "ranked_count": len(ranked),
        }
    )
    return result


@lru_cache(maxsize=1)
def _document_index() -> Any:
    """하이브리드 색인을 한 번만 만들어 재사용한다.

    임베딩은 ``corpus_embeddings.npz``에 캐시돼 있어 재계산하지 않는다.
    코퍼스가 바뀌면 지문이 달라져 자동으로 다시 만든다.
    """

    from backend.llm_poc import doc_search

    chunks = doc_search.load_corpus()
    keyword = doc_search.KeywordIndex(chunks)
    dense = doc_search.make_dense_index(chunks, doc_search.build_embeddings(chunks))
    return doc_search.HybridIndex(keyword, dense)


def search_docs(question: str) -> dict[str, Any]:
    """서비스·모델 문서에서 질문과 관련된 발췌를 찾는다. LLM은 호출하지 않는다.

    낱말 검색과 임베딩을 합친 하이브리드다. 질문 20개 실측 Recall@4는
    낱말만 0.65, 임베딩만 0.85, 합치면 0.90이다. 고유명사는 낱말 검색이,
    에두른 표현은 임베딩이 잡는다.
    """

    result: dict[str, Any] = {
        "success": False,
        "question": question if isinstance(question, str) else None,
        "hits": [],
        "retrieval": "hybrid(bm25+bge-m3, RRF)",
        # 비워 둔다. 예전에는 "문서 수치는 작성 시점 값" 면책을 항상 붙였는데,
        # "NDVI가 무슨 뜻이야?" 같은 정의 질문처럼 답에 수치가 하나도 없는
        # 경우에도 붙어서 본문보다 면책이 긴 답이 나왔다.
        # 문서 수치가 낡는다는 사실 자체는 그대로다(07.11 문서의 R² 0.832가
        # 지금 0.7861). 그건 "숫자는 RAG로 답하지 않는다"는 설계로 막고 있지,
        # 모든 답변에 붙이는 꼬리말로 막을 일이 아니다.
        "limitations": [],
        "error": None,
    }
    if not isinstance(question, str) or not question.strip():
        result["error"] = "질문이 필요합니다."
        return result

    from backend.llm_poc import doc_search

    try:
        index = _document_index()
    except Exception as exc:
        result["error"] = f"문서 색인을 준비하지 못했습니다: {exc}"
        return result

    try:
        hits = index.search(question.strip(), top_k=doc_search.DEFAULT_TOP_K)
    except Exception as exc:
        result["error"] = f"문서 검색에 실패했습니다: {exc}"
        return result

    # 개수가 아니라 글자 예산으로 끊는다. 표 청크가 1,200자를 넘어 "상위 4개"가
    # 컨텍스트를 넘기는 경우가 있다.
    kept = doc_search.fit_context_budget(hits)
    if not kept:
        result["success"] = True
        result["error"] = None
        return result

    result.update(
        {
            "success": True,
            "hits": [
                {
                    "doc": hit.chunk.doc,
                    "heading_path": hit.chunk.heading_path,
                    "text": hit.chunk.text,
                    "char_count": hit.chunk.char_count,
                    "score": round(float(hit.score), 6),
                }
                for hit in kept
            ],
        }
    )
    return result


_POLICY_DELTA_ARGUMENT = {
    "green_ratio": "green_ratio_delta",
    "impervious_ratio": "impervious_ratio_delta",
    "ndvi": "ndvi_delta",
    "albedo": "albedo_delta",
    "road_ratio": "road_ratio_delta",
}


def simulate_policy(grid_id: str, policy_id: str) -> dict[str, Any]:
    """정책 프리셋 하나를 격자에 적용한 결과를 돌려준다.

    변화량은 ``backend/policy_presets.py``에서 읽는다. 화면이 쓰는 정의와
    같은 원본이라 챗봇과 화면이 다른 숫자를 말할 수 없다.

    예측은 ``run_simulation``에 그대로 위임한다. 학습범위 clip, 경고 생성,
    반환값 검증이 전부 거기 있어서 여기서 다시 만들면 두 벌이 갈라진다.

    화면은 ``couple_land_cover=false``로 보내고 여기는 run_simulation의
    기본값(True)을 쓴다. 6종 모두 결과가 같다. 녹지를 바꾸는 세 정책은
    불투수면도 함께 명시해서 연동이 발동하지 않고, 나머지 셋은 녹지를
    건드리지 않는다.
    """

    from backend.policy_presets import POLICY_PRESET_BY_ID

    normalized_policy_id = policy_id.strip() if isinstance(policy_id, str) else ""
    preset = POLICY_PRESET_BY_ID.get(normalized_policy_id)
    if preset is None:
        result = _empty_simulation_result(
            grid_id.strip() if isinstance(grid_id, str) else None
        )
        result["error"] = f"지원하지 않는 정책입니다: {policy_id}"
        return result

    deltas = {
        _POLICY_DELTA_ARGUMENT[feature]: value
        for feature, value in preset["changes"].items()
        if feature in _POLICY_DELTA_ARGUMENT
    }
    unsupported = set(preset["changes"]) - set(_POLICY_DELTA_ARGUMENT)
    if unsupported:
        # 정책이 시뮬레이션할 수 없는 지표를 건드리면 조용히 빼지 않는다.
        # 일부만 적용한 결과는 그 정책의 효과가 아니다.
        result = _empty_simulation_result(
            grid_id.strip() if isinstance(grid_id, str) else None
        )
        result["error"] = (
            f"{preset['name']} 정책이 시뮬레이션할 수 없는 지표를 포함합니다: "
            f"{sorted(unsupported)}"
        )
        return result

    result = run_simulation(grid_id, **deltas)
    result["policy_id"] = preset["id"]
    result["policy_name"] = preset["name"]
    result["policy_description"] = preset["description"]
    result["policy_scenario_label"] = preset["scenario_label"]
    result["policy_source_url"] = preset["source_url"]
    if result.get("success") is True:
        result["limitations"] = [
            *result.get("limitations", []),
            *preset["assumptions"],
        ]
    return result


def get_field_source(fields: list[str] | None = None) -> dict[str, Any]:
    """지표의 데이터 출처를 GRID_FIELD_SPECS에서 그대로 읽어 온다.

    출처는 격자와 무관하다. 같은 지표면 어느 격자든 출처가 같으므로
    grid_id를 받지 않는다.

    예전에는 출처 질문이 문서 검색으로 흘렀다. 문서에는 학습 대상 변수의
    출처까지 섞여 있어서 "녹지율 출처"를 물으면 무더위쉼터·인구통계가
    함께 나왔다. 여기 있는 값이 그 지표의 실제 출처다.

    Args:
        fields: 출처를 알려줄 필드 목록. 생략하면 18개 전부.

    Returns:
        성공 여부와 필드별 라벨·출처 매핑. 실패 시에도 ``error``를 채운다.
    """

    result: dict[str, Any] = {
        "success": False,
        "requested_fields": [],
        "sources": {},
        "error": None,
    }
    if fields is None:
        requested_fields = list(ALLOWED_GRID_FIELDS)
    else:
        requested_fields, unsupported_fields, fields_error = (
            _normalize_grid_fields(fields)
        )
        if fields_error is not None:
            result["requested_fields"] = requested_fields
            result["error"] = fields_error
            result["available_fields"] = list(ALLOWED_GRID_FIELDS)
            if unsupported_fields:
                result["unsupported_fields"] = unsupported_fields
            return result

    result["requested_fields"] = requested_fields
    result["sources"] = {
        field: {
            "label": GRID_FIELD_SPECS[field]["label"],
            "source": GRID_FIELD_SPECS[field]["source"],
        }
        for field in requested_fields
    }
    result["success"] = True
    return result


TOOL_FUNCTIONS = {
    "get_grid_data": get_grid_data,
    "get_field_source": get_field_source,
    "run_simulation": run_simulation,
    "simulate_policy": simulate_policy,
    "rank_policies": rank_policies,
    "search_docs": search_docs,
}
