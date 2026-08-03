"""Ollama의 qwen3:4b로 GA:ON 조회·시뮬레이션 Tool Calling을 확인하는 CLI."""

from __future__ import annotations

import argparse
import json
import math
import sys
from collections.abc import Mapping
from typing import Any

from backend.llm_poc.chat_service import (
    DOC_ANSWER_MAX_ATTEMPTS,
    ChatInputError,
    ChatProtocolError,
    ChatResult,
    run_chat,
)
from backend.llm_poc.tools import (
    ALLOWED_GRID_FIELDS,
    format_grid_field_value,
)


LOOKUP_QUESTION = "11230_00001 격자의 녹지율과 불투수율을 알려줘."
NDVI_QUESTION = "11230_00001 격자의 NDVI를 알려줘."
NDVI_ALBEDO_QUESTION = "11230_00001 격자의 식생지수와 알베도를 알려줘."
ROAD_QUESTION = "11230_00001 격자의 도로율을 알려줘."
PARK_QUESTION = (
    "11230_00001 격자의 공원까지 거리와 500m 내 공원 면적을 알려줘."
)
TERRAIN_QUESTION = "11230_00001 격자의 고도와 경사도를 알려줘."
ALL_DATA_QUESTION = "11230_00001 격자의 전체 데이터를 알려줘."
UNSUPPORTED_QUESTION = "11230_00001 격자의 인구밀도를 알려줘."
SIMULATION_QUESTION = (
    "11230_00001 격자의 녹지율을 5%p 높이면 "
    "모델 기준 예상 변화량이 어떻게 돼?"
)
ROUTING_GRID_ID = "11110_00909"
FULL_SCOPE_ALL_QUESTION = "이 격자 데이터 모두 알려줘"
FULL_SCOPE_ENTIRE_QUESTION = "이 격자의 전체 데이터 알려줘"
FULL_SCOPE_EVERY_QUESTION = "모든 데이터 보여줘"
FULL_SCOPE_INFORMATION_QUESTION = "이 격자 정보 전부 알려줘"
FULL_SCOPE_SHORT_QUESTION = "다 보여줘"
GENERIC_DATA_GIVE_QUESTION = "데이터줘봐"
GENERIC_DATA_TELL_QUESTION = "데이터 알려줘"
GENERIC_CURRENT_DATA_QUESTION = "현재 데이터"
GENERIC_GRID_INFORMATION_QUESTION = "이 격자 정보 보여줘"
GENERIC_CURRENT_VALUE_QUESTION = "현재값 보여줘"
GENERIC_STATUS_QUESTION = "이곳 현황 보여줘"
MODEL_EXPLANATION_QUESTION = "데이터 말고 모델 설명해줘"
SUPPORTED_DATA_QUESTION = "어떤 데이터를 지원해?"
CAPABILITY_QUESTION = "뭘 할 수 있어?"
DATA_SOURCE_QUESTION = "데이터의 출처가 뭐야?"
MODEL_DATA_EXPLANATION_QUESTION = "모델 데이터 설명해줘"
NEGATED_LOOKUP_SIMULATION_QUESTION = (
    "현재 데이터가 아니라 녹지율을 5% 올려줘"
)
EXCLUDED_SCOPE_GREEN_QUESTION = "모든 데이터 말고 녹지율만 알려줘"
EXCLUDED_SCOPE_NDVI_QUESTION = "전체는 필요 없고 NDVI만 보여줘"
EXCLUDED_SCOPE_RATIOS_QUESTION = (
    "전부 말고 녹지율과 불투수율만 알려줘"
)
EXCLUDED_SCOPE_PARK_QUESTION = "다 보여주지 말고 공원면적만 알려줘"
GREEN_INCREASE_QUESTION = "녹지율을 5%p 올려줘"
IMPERVIOUS_DECREASE_QUESTION = "불투수율을 3%p 낮춰줘"
PARK_AREA_INCREASE_QUESTION = "공원 면적을 500㎡ 늘려줘"
NDVI_INCREASE_QUESTION = "NDVI를 0.05 높여줘"
ALBEDO_INCREASE_QUESTION = "알베도를 0.02 높여줘"
GREEN_NDVI_QUESTION = "녹지율을 5%p 올리고 NDVI를 0.05 높여줘"
COMBINED_RATIO_QUESTION = (
    "녹지율을 5%p 올리고 불투수율을 3%p 낮춰줘"
)
MULTILINE_SIMULATION_QUESTION = (
    "녹지율을 5%p 올려줘\n"
    "불투수율을 3%p 낮춰줘\n"
    "NDVI를 0.05 높여줘\n"
    "알베도를 0.02 높여줘"
)
DIRECTIONLESS_COMBINED_QUESTION = "녹지율 5%p, NDVI 0.05"
CURRENT_RATIO_QUESTION = "현재 녹지율과 불투수율을 알려줘"
MISSING_DELTA_QUESTION = "녹지율을 올려줘"
GREEN_PRO_INCREASE_QUESTION = "녹지율 5프로 올려줘"
GREEN_PER_INCREASE_QUESTION = "녹지 비중을 5퍼 정도 높여봐"
GREEN_PLUS_FIVE_INCREASE_QUESTION = "녹지율 5플오 증가"
IMPERVIOUS_PER_DECREASE_QUESTION = "불투수를 3퍼 내려"
PARK_SQUARE_INCREASE_QUESTION = "공원 500제곱 추가"
COMBINED_IMPLICIT_UNIT_QUESTION = (
    "녹지 한 5 정도 높이고 불투수는 3 낮춰봐"
)
AMBIGUOUS_PLUS_FIVE_QUESTION = "그거 5플오 해줘"
SEMANTIC_GREEN_LOOKUP_QUESTION = "녹지가 차지하는 비중 알려줘"
SEMANTIC_AVG_FLOOR_QUESTION = "건물들이 평균적으로 몇 층이야"
SEMANTIC_MAX_FLOOR_QUESTION = "가장 높은 건물은 몇 층이야"
SEMANTIC_ROAD_QUESTION = "도로가 전체 면적에서 차지하는 정도"
SEMANTIC_IMPERVIOUS_QUESTION = "물이 안 스며드는 땅의 비율"
SEMANTIC_NDVI_QUESTION = "식생이 얼마나 푸른지"
SEMANTIC_ALBEDO_QUESTION = "햇빛을 얼마나 반사하는 표면이야"
AMBIGUOUS_PLANT_QUESTION = "식물이 얼마나 많은지 알려줘"
AMBIGUOUS_BUILDING_HEIGHT_QUESTION = "건물 높이 알려줘"
SEMANTIC_GREEN_SIMULATION_QUESTION = "녹지가 차지하는 비율을 5%p 높여줘"
SEMANTIC_IMPERVIOUS_SIMULATION_QUESTION = (
    "물이 안 스며드는 면적을 3%p 낮춰줘"
)
NO_TOOL_EXPECTED = "__no_tool__"
DEFAULT_QUESTION = LOOKUP_QUESTION


def _simulation_arguments(
    grid_id: str,
    *,
    green_ratio_delta: float = 0.0,
    impervious_ratio_delta: float = 0.0,
    ndvi_delta: float = 0.0,
    albedo_delta: float = 0.0,
) -> dict[str, Any]:
    return {
        "grid_id": grid_id,
        "green_ratio_delta": green_ratio_delta,
        "impervious_ratio_delta": impervious_ratio_delta,
        "ndvi_delta": ndvi_delta,
        "albedo_delta": albedo_delta,
    }


_SIMULATION_FEATURE_BY_ARGUMENT = {
    "green_ratio_delta": "green_ratio",
    "impervious_ratio_delta": "impervious_ratio",
    "ndvi_delta": "ndvi",
    "albedo_delta": "albedo",
}


DEFAULT_CASES = (
    (
        LOOKUP_QUESTION,
        "get_grid_data",
        {
            "grid_id": "11230_00001",
            "fields": ["green_ratio", "impervious_ratio"],
        },
    ),
    (
        NDVI_QUESTION,
        "get_grid_data",
        {"grid_id": "11230_00001", "fields": ["ndvi"]},
    ),
    (
        NDVI_ALBEDO_QUESTION,
        "get_grid_data",
        {"grid_id": "11230_00001", "fields": ["ndvi", "albedo"]},
    ),
    (
        ROAD_QUESTION,
        "get_grid_data",
        {"grid_id": "11230_00001", "fields": ["road_ratio"]},
    ),
    (
        PARK_QUESTION,
        "get_grid_data",
        {
            "grid_id": "11230_00001",
            "fields": [
                "nearest_park_distance_m",
                "park_area_within_500m",
            ],
        },
    ),
    (
        TERRAIN_QUESTION,
        "get_grid_data",
        {
            "grid_id": "11230_00001",
            "fields": ["elevation_m", "slope_deg"],
        },
    ),
    (
        ALL_DATA_QUESTION,
        "get_grid_data",
        {
            "grid_id": "11230_00001",
            "fields": list(ALLOWED_GRID_FIELDS),
        },
    ),
    (
        UNSUPPORTED_QUESTION,
        NO_TOOL_EXPECTED,
        None,
    ),
    (
        SIMULATION_QUESTION,
        "run_simulation",
        _simulation_arguments("11230_00001", green_ratio_delta=0.05),
    ),
)
SELECTED_GRID_ROUTING_CASES = (
    (
        GREEN_INCREASE_QUESTION,
        "run_simulation",
        _simulation_arguments(ROUTING_GRID_ID, green_ratio_delta=0.05),
        None,
    ),
    (
        IMPERVIOUS_DECREASE_QUESTION,
        "run_simulation",
        _simulation_arguments(
            ROUTING_GRID_ID,
            impervious_ratio_delta=-0.03,
        ),
        None,
    ),
    (
        NDVI_INCREASE_QUESTION,
        "run_simulation",
        _simulation_arguments(ROUTING_GRID_ID, ndvi_delta=0.05),
        None,
    ),
    (
        ALBEDO_INCREASE_QUESTION,
        "run_simulation",
        _simulation_arguments(ROUTING_GRID_ID, albedo_delta=0.02),
        None,
    ),
    (
        GREEN_NDVI_QUESTION,
        "run_simulation",
        _simulation_arguments(
            ROUTING_GRID_ID,
            green_ratio_delta=0.05,
            ndvi_delta=0.05,
        ),
        None,
    ),
    (
        COMBINED_RATIO_QUESTION,
        "run_simulation",
        _simulation_arguments(
            ROUTING_GRID_ID,
            green_ratio_delta=0.05,
            impervious_ratio_delta=-0.03,
        ),
        None,
    ),
    (
        MULTILINE_SIMULATION_QUESTION,
        "run_simulation",
        _simulation_arguments(
            ROUTING_GRID_ID,
            green_ratio_delta=0.05,
            impervious_ratio_delta=-0.03,
            ndvi_delta=0.05,
            albedo_delta=0.02,
        ),
        None,
    ),
    (
        PARK_AREA_INCREASE_QUESTION,
        NO_TOOL_EXPECTED,
        None,
        None,
    ),
    (
        PARK_SQUARE_INCREASE_QUESTION,
        NO_TOOL_EXPECTED,
        None,
        None,
    ),
    (
        DIRECTIONLESS_COMBINED_QUESTION,
        NO_TOOL_EXPECTED,
        None,
        "증가 또는 감소 방향",
    ),
    (
        CURRENT_RATIO_QUESTION,
        "get_grid_data",
        {
            "grid_id": ROUTING_GRID_ID,
            "fields": ["green_ratio", "impervious_ratio"],
        },
        None,
    ),
    (
        MISSING_DELTA_QUESTION,
        NO_TOOL_EXPECTED,
        None,
        "변경량을 숫자로",
    ),
    (
        GREEN_PRO_INCREASE_QUESTION,
        "run_simulation",
        _simulation_arguments(ROUTING_GRID_ID, green_ratio_delta=0.05),
        "%p",
    ),
    (
        GREEN_PER_INCREASE_QUESTION,
        "run_simulation",
        _simulation_arguments(ROUTING_GRID_ID, green_ratio_delta=0.05),
        "%p",
    ),
    (
        GREEN_PLUS_FIVE_INCREASE_QUESTION,
        "run_simulation",
        _simulation_arguments(ROUTING_GRID_ID, green_ratio_delta=0.05),
        "%p",
    ),
    (
        IMPERVIOUS_PER_DECREASE_QUESTION,
        "run_simulation",
        _simulation_arguments(
            ROUTING_GRID_ID,
            impervious_ratio_delta=-0.03,
        ),
        "%p",
    ),
    (
        COMBINED_IMPLICIT_UNIT_QUESTION,
        "run_simulation",
        _simulation_arguments(
            ROUTING_GRID_ID,
            green_ratio_delta=0.05,
            impervious_ratio_delta=-0.03,
        ),
        "%p",
    ),
    (
        AMBIGUOUS_PLUS_FIVE_QUESTION,
        NO_TOOL_EXPECTED,
        None,
        "다음 내용만 확인해 주세요",
    ),
)
LOOKUP_SCOPE_CASES = (
    (FULL_SCOPE_ALL_QUESTION, list(ALLOWED_GRID_FIELDS), True, False),
    (FULL_SCOPE_ENTIRE_QUESTION, list(ALLOWED_GRID_FIELDS), True, False),
    (FULL_SCOPE_EVERY_QUESTION, list(ALLOWED_GRID_FIELDS), True, False),
    (FULL_SCOPE_INFORMATION_QUESTION, list(ALLOWED_GRID_FIELDS), True, False),
    (FULL_SCOPE_SHORT_QUESTION, list(ALLOWED_GRID_FIELDS), True, False),
    (EXCLUDED_SCOPE_GREEN_QUESTION, ["green_ratio"], False, True),
    (EXCLUDED_SCOPE_NDVI_QUESTION, ["ndvi"], False, True),
    (
        EXCLUDED_SCOPE_RATIOS_QUESTION,
        ["green_ratio", "impervious_ratio"],
        False,
        True,
    ),
    (
        EXCLUDED_SCOPE_PARK_QUESTION,
        ["park_area_within_500m"],
        False,
        True,
    ),
)
GENERAL_LOOKUP_ROUTING_CASES = (
    (
        GENERIC_DATA_GIVE_QUESTION,
        "get_grid_data",
        {
            "grid_id": ROUTING_GRID_ID,
            "fields": list(ALLOWED_GRID_FIELDS),
        },
        None,
        True,
        False,
    ),
    (
        GENERIC_DATA_TELL_QUESTION,
        "get_grid_data",
        {
            "grid_id": ROUTING_GRID_ID,
            "fields": list(ALLOWED_GRID_FIELDS),
        },
        None,
        True,
        False,
    ),
    (
        GENERIC_CURRENT_DATA_QUESTION,
        "get_grid_data",
        {
            "grid_id": ROUTING_GRID_ID,
            "fields": list(ALLOWED_GRID_FIELDS),
        },
        None,
        True,
        False,
    ),
    (
        GENERIC_GRID_INFORMATION_QUESTION,
        "get_grid_data",
        {
            "grid_id": ROUTING_GRID_ID,
            "fields": list(ALLOWED_GRID_FIELDS),
        },
        None,
        True,
        False,
    ),
    (
        GENERIC_CURRENT_VALUE_QUESTION,
        "get_grid_data",
        {
            "grid_id": ROUTING_GRID_ID,
            "fields": list(ALLOWED_GRID_FIELDS),
        },
        None,
        True,
        False,
    ),
    (
        GENERIC_STATUS_QUESTION,
        "get_grid_data",
        {
            "grid_id": ROUTING_GRID_ID,
            "fields": list(ALLOWED_GRID_FIELDS),
        },
        None,
        True,
        False,
    ),
    (
        "녹지율 알려줘",
        "get_grid_data",
        {
            "grid_id": ROUTING_GRID_ID,
            "fields": ["green_ratio"],
        },
        None,
        False,
        False,
    ),
    (
        # ④ 도입 전에는 unsupported였다. 문서 검색이 생겨 이제 답할 수 있다.
        MODEL_EXPLANATION_QUESTION,
        "search_docs",
        None,
        "출처:",
        False,
        False,
    ),
    (
        SUPPORTED_DATA_QUESTION,
        NO_TOOL_EXPECTED,
        None,
        None,
        False,
        False,
    ),
    (
        CAPABILITY_QUESTION,
        NO_TOOL_EXPECTED,
        None,
        None,
        False,
        False,
    ),
    (
        # ④ 도입 전에는 unsupported였다. 문서 검색이 생겨 이제 답할 수 있다.
        DATA_SOURCE_QUESTION,
        "search_docs",
        None,
        "출처:",
        False,
        False,
    ),
    (
        # ④ 도입 전에는 unsupported였다. 문서 검색이 생겨 이제 답할 수 있다.
        MODEL_DATA_EXPLANATION_QUESTION,
        "search_docs",
        None,
        "출처:",
        False,
        False,
    ),
    (
        NEGATED_LOOKUP_SIMULATION_QUESTION,
        "run_simulation",
        _simulation_arguments(
            ROUTING_GRID_ID,
            green_ratio_delta=0.05,
        ),
        None,
        False,
        False,
    ),
)

SEMANTIC_ROUTING_CASES = (
    (
        SEMANTIC_GREEN_LOOKUP_QUESTION,
        "get_grid_data",
        {"grid_id": ROUTING_GRID_ID, "fields": ["green_ratio"]},
        None,
        "resolved",
        (),
    ),
    (
        SEMANTIC_AVG_FLOOR_QUESTION,
        "get_grid_data",
        {"grid_id": ROUTING_GRID_ID, "fields": ["avg_ground_floor_count"]},
        None,
        "resolved",
        (),
    ),
    (
        SEMANTIC_MAX_FLOOR_QUESTION,
        "get_grid_data",
        {"grid_id": ROUTING_GRID_ID, "fields": ["max_ground_floor_count"]},
        None,
        "resolved",
        (),
    ),
    (
        SEMANTIC_ROAD_QUESTION,
        "get_grid_data",
        {"grid_id": ROUTING_GRID_ID, "fields": ["road_ratio"]},
        None,
        "resolved",
        (),
    ),
    (
        SEMANTIC_IMPERVIOUS_QUESTION,
        "get_grid_data",
        {"grid_id": ROUTING_GRID_ID, "fields": ["impervious_ratio"]},
        None,
        "resolved",
        (),
    ),
    (
        SEMANTIC_NDVI_QUESTION,
        "get_grid_data",
        {"grid_id": ROUTING_GRID_ID, "fields": ["ndvi"]},
        None,
        "resolved",
        (),
    ),
    (
        SEMANTIC_ALBEDO_QUESTION,
        "get_grid_data",
        {"grid_id": ROUTING_GRID_ID, "fields": ["albedo"]},
        None,
        "resolved",
        (),
    ),
    (
        "녹 지율 알려줘",
        "get_grid_data",
        {"grid_id": ROUTING_GRID_ID, "fields": ["green_ratio"]},
        None,
        "resolved",
        (),
    ),
    (
        "N D V I 알려줘",
        "get_grid_data",
        {"grid_id": ROUTING_GRID_ID, "fields": ["ndvi"]},
        None,
        "resolved",
        (),
    ),
    (
        "앤디브이아이 알려줘",
        "get_grid_data",
        {"grid_id": ROUTING_GRID_ID, "fields": ["ndvi"]},
        None,
        "resolved",
        (),
    ),
    (
        "평균 지상 층수 알려줘",
        "get_grid_data",
        {"grid_id": ROUTING_GRID_ID, "fields": ["avg_ground_floor_count"]},
        None,
        "resolved",
        (),
    ),
    (
        AMBIGUOUS_PLANT_QUESTION,
        NO_TOOL_EXPECTED,
        None,
        ("녹지율", "식생지수"),
        "ambiguous",
        ("green_ratio", "ndvi"),
    ),
    (
        AMBIGUOUS_BUILDING_HEIGHT_QUESTION,
        NO_TOOL_EXPECTED,
        None,
        ("평균 지상층수", "최대 지상층수"),
        "ambiguous",
        ("avg_ground_floor_count", "max_ground_floor_count"),
    ),
    (
        "인구밀도 알려줘",
        NO_TOOL_EXPECTED,
        None,
        "현재 요청은 아직 지원하지 않습니다.",
        "unsupported",
        (),
    ),
    (
        # ④ 도입 전에는 unsupported였다. 문서 검색이 생겨 이제 답할 수 있다.
        "NDVI가 무슨 뜻이야?",
        "search_docs",
        None,
        "출처:",
        "resolved",
        (),
    ),
    (
        # ④ 도입 전에는 unsupported였다. 문서 검색이 생겨 이제 답할 수 있다.
        "녹지율 데이터 출처가 어디야?",
        "search_docs",
        None,
        "출처:",
        "resolved",
        (),
    ),
    (
        SEMANTIC_GREEN_SIMULATION_QUESTION,
        "run_simulation",
        _simulation_arguments(ROUTING_GRID_ID, green_ratio_delta=0.05),
        None,
        "resolved",
        (),
    ),
    (
        SEMANTIC_IMPERVIOUS_SIMULATION_QUESTION,
        "run_simulation",
        _simulation_arguments(
            ROUTING_GRID_ID,
            impervious_ratio_delta=-0.03,
        ),
        None,
        "resolved",
        (),
    ),
)


def _validate_expected_arguments(
    tool_name: str,
    tool_arguments: Mapping[str, Any],
    expected_arguments: Mapping[str, Any],
) -> None:
    if tool_arguments.get("grid_id") != expected_arguments.get("grid_id"):
        raise RuntimeError("서비스가 확정된 grid_id를 정확히 전달하지 않았습니다.")

    if tool_name == "get_grid_data":
        if not set(tool_arguments).issubset({"grid_id", "fields"}):
            raise RuntimeError("get_grid_data에 예상하지 않은 인자가 전달되었습니다.")
        expected_fields = expected_arguments.get("fields")
        if expected_fields is not None:
            actual_fields = tool_arguments.get("fields")
            if actual_fields != expected_fields:
                raise RuntimeError(
                    "구조화 조회 결과가 fields를 요청 순서대로 확정하지 않았습니다."
                )
        return

    if tool_name != "run_simulation":
        raise RuntimeError(f"인자 검증을 지원하지 않는 도구입니다: {tool_name}")

    allowed_arguments = {
        "grid_id",
        "green_ratio_delta",
        "impervious_ratio_delta",
        "ndvi_delta",
        "albedo_delta",
    }
    if set(tool_arguments) != allowed_arguments:
        raise RuntimeError(
            "run_simulation에 grid_id와 네 변경 인자가 정확히 전달되지 않았습니다."
        )

    for name in (
        "green_ratio_delta",
        "impervious_ratio_delta",
        "ndvi_delta",
        "albedo_delta",
    ):
        actual = tool_arguments.get(name, 0)
        expected = expected_arguments.get(name, 0)
        if (
            isinstance(actual, bool)
            or not isinstance(actual, (int, float))
            or not math.isfinite(float(actual))
            or not math.isclose(
                float(actual),
                float(expected),
                rel_tol=0,
                abs_tol=1e-12,
            )
        ):
            raise RuntimeError(
                f"검증된 구조에서 계산한 {name}이 기대값과 다릅니다."
            )


def _validate_grid_lookup_result(result: ChatResult) -> None:
    if result.tool_data.get("success") is not True:
        raise RuntimeError("get_grid_data가 성공 결과를 반환하지 않았습니다.")

    requested_fields = result.tool_data.get("requested_fields")
    values = result.tool_data.get("values")
    if (
        not isinstance(requested_fields, list)
        or not requested_fields
        or not isinstance(values, Mapping)
        or list(values) != requested_fields
    ):
        raise RuntimeError("조회 필드와 실제 values가 정확히 일치하지 않습니다.")

    grid_id = result.tool_data.get("grid_id")
    gu_name = result.tool_data.get("gu_name")
    if (
        not isinstance(grid_id, str)
        or grid_id not in result.answer
        or not isinstance(gu_name, str)
        or gu_name not in result.answer
    ):
        raise RuntimeError("최종 답변에 grid_id 또는 지역명이 없습니다.")

    for field in requested_fields:
        raw_value = values.get(field)
        if (
            isinstance(raw_value, bool)
            or not isinstance(raw_value, (int, float))
            or not math.isfinite(float(raw_value))
        ):
            raise RuntimeError(f"{field} 조회값이 유한 숫자가 아닙니다.")
        display_value = format_grid_field_value(field, float(raw_value))
        if display_value not in result.answer:
            raise RuntimeError(
                f"최종 답변에 {field}의 실제 표시값이 없습니다."
            )


def _validated_changed_features(
    value: Any,
    field_name: str,
) -> dict[str, tuple[float, float]]:
    if not isinstance(value, Mapping):
        raise RuntimeError(f"Tool 결과의 {field_name}가 객체가 아닙니다.")

    validated: dict[str, tuple[float, float]] = {}
    for feature, raw_change in value.items():
        if not isinstance(feature, str) or not isinstance(raw_change, Mapping):
            raise RuntimeError(f"Tool 결과의 {field_name} 항목이 올바르지 않습니다.")
        before = raw_change.get("before")
        after = raw_change.get("after")
        if (
            isinstance(before, bool)
            or not isinstance(before, (int, float))
            or not math.isfinite(float(before))
            or isinstance(after, bool)
            or not isinstance(after, (int, float))
            or not math.isfinite(float(after))
        ):
            raise RuntimeError(
                f"Tool 결과의 {field_name}.{feature} 전후 값이 유한 숫자가 아닙니다."
            )
        validated[feature] = (float(before), float(after))
    return validated


def _validate_delta_std_language(answer: str) -> None:
    normalized = (
        answer.replace("\n", ".")
        .replace("!", ".")
        .replace("?", ".")
    )
    for sentence in normalized.split("."):
        compact = "".join(sentence.split())
        if not any(term in compact for term in ("오차범위", "신뢰구간")):
            continue
        if not any(
            term in compact
            for term in (
                "아니",
                "아닙",
                "해석하지",
                "사용하지",
                "쓰지",
                "보지",
                "간주하지",
            )
        ):
            raise RuntimeError(
                "delta_std를 오차범위 또는 신뢰구간처럼 표현했습니다."
            )


def _validate_simulation_result(
    result: ChatResult,
    expected_arguments: Mapping[str, Any] | None,
) -> None:
    data = result.tool_data
    if data.get("success") is not True:
        raise RuntimeError("run_simulation이 성공 결과를 반환하지 않았습니다.")

    requested_changes = data.get("requested_changes")
    if not isinstance(requested_changes, Mapping):
        raise RuntimeError("Tool 결과의 requested_changes가 객체가 아닙니다.")
    for feature, raw_delta in requested_changes.items():
        if (
            not isinstance(feature, str)
            or isinstance(raw_delta, bool)
            or not isinstance(raw_delta, (int, float))
            or not math.isfinite(float(raw_delta))
        ):
            raise RuntimeError("requested_changes에 유한 숫자가 아닌 값이 있습니다.")

    expected_requested_changes: dict[str, float] | None = None
    if expected_arguments is not None:
        expected_requested_changes = {
            feature: float(expected_arguments[argument])
            for argument, feature in _SIMULATION_FEATURE_BY_ARGUMENT.items()
            if not math.isclose(
                float(expected_arguments[argument]),
                0.0,
                rel_tol=0,
                abs_tol=1e-12,
            )
        }
        if set(requested_changes) != set(expected_requested_changes):
            raise RuntimeError(
                "Tool 결과의 requested_changes가 검증된 네 변경 인자와 다릅니다."
            )
        for feature, expected_delta in expected_requested_changes.items():
            if not math.isclose(
                float(requested_changes[feature]),
                expected_delta,
                rel_tol=0,
                abs_tol=1e-12,
            ):
                raise RuntimeError(
                    f"Tool 결과의 requested_changes.{feature}가 기대값과 다릅니다."
                )

    applied_changes = _validated_changed_features(
        data.get("applied_changes"),
        "applied_changes",
    )
    auto_applied_changes = _validated_changed_features(
        data.get("auto_applied_changes"),
        "auto_applied_changes",
    )

    for feature, change in auto_applied_changes.items():
        if feature not in applied_changes or applied_changes[feature] != change:
            raise RuntimeError(
                "auto_applied_changes가 applied_changes의 부분집합이 아닙니다."
            )

    if expected_requested_changes is not None:
        expected_auto_fields = set(applied_changes) - set(expected_requested_changes)
        if set(auto_applied_changes) != expected_auto_fields:
            raise RuntimeError(
                "명시 요청하지 않은 실제 적용값이 auto_applied_changes와 다릅니다."
            )
        for feature, expected_delta in expected_requested_changes.items():
            if feature not in applied_changes:
                raise RuntimeError(
                    f"명시 요청한 {feature}가 applied_changes에 없습니다."
                )
            before, after = applied_changes[feature]
            if not math.isclose(
                after - before,
                expected_delta,
                rel_tol=0,
                abs_tol=2e-6,
            ):
                raise RuntimeError(
                    f"{feature}의 실제 적용 전후 차이가 요청 delta와 다릅니다."
                )

    if "direction_confidence" not in data:
        raise RuntimeError("Tool 결과에 direction_confidence가 없습니다.")
    direction_confidence = data.get("direction_confidence")
    if direction_confidence is not None:
        if (
            isinstance(direction_confidence, bool)
            or not isinstance(direction_confidence, (int, float))
            or not math.isfinite(float(direction_confidence))
            or not 0.0 <= float(direction_confidence) <= 1.0
        ):
            raise RuntimeError(
                "direction_confidence는 0~1 유한 숫자 또는 None이어야 합니다."
            )
        if float(direction_confidence) < 0.6:
            if (
                "방향" not in result.answer
                or "판단하기 어렵" not in result.answer
            ):
                raise RuntimeError(
                    "낮은 direction_confidence가 최종 답변에 설명되지 않았습니다."
                )
        else:
            display_confidence = (
                f"{float(direction_confidence) * 100:.1f}%"
            )
            if (
                display_confidence not in result.answer
                or "방향 동의율" not in result.answer
            ):
                raise RuntimeError(
                    "direction_confidence가 최종 답변에 정확히 표시되지 않았습니다."
                )
    elif (
        "direction_confidence" not in result.answer
        or "산정되지 않았" not in result.answer
    ):
        raise RuntimeError(
            "산정되지 않은 direction_confidence 설명이 최종 답변에 없습니다."
        )

    warnings = data.get("warnings")
    if not isinstance(warnings, list) or not all(
        isinstance(warning, str) and warning.strip()
        for warning in warnings
    ):
        raise RuntimeError("Tool 결과의 warnings가 문자열 배열이 아닙니다.")
    for warning in warnings:
        if warning not in result.answer:
            raise RuntimeError("Tool 경고가 최종 답변에 빠졌습니다.")

    if (
        expected_requested_changes is not None
        and set(expected_requested_changes) == {"green_ratio"}
    ):
        if "impervious_ratio" not in auto_applied_changes:
            raise RuntimeError(
                "녹지율 단독 요청에 불투수율 자동 연동이 적용되지 않았습니다."
            )
        coupling_warnings = [
            warning
            for warning in warnings
            if "연동" in warning and "불투수" in warning
        ]
        if not coupling_warnings:
            raise RuntimeError(
                "녹지율 단독 요청의 자동 연동 경고가 없습니다."
            )
        if not all(warning in result.answer for warning in coupling_warnings):
            raise RuntimeError(
                "자동 연동 경고가 최종 답변에 표시되지 않았습니다."
            )

    _validate_delta_std_language(result.answer)


def _print_result(result: ChatResult, *, validated: bool = True) -> None:
    print(
        "라우터 추론 분리 상태: "
        f"{'있음' if result.first_thinking else '없음'}"
        f" ({len(result.first_thinking)}자)"
    )
    print(f"라우터 구조화 JSON 길이: {len(result.first_content)}자")

    if result.used_tools:
        print(f"호출된 도구명: {result.used_tools[0]}")
        print("도구 인자:")
        print(json.dumps(result.tool_arguments, ensure_ascii=False, indent=2))
        print("도구 반환값:")
        print(json.dumps(result.tool_data, ensure_ascii=False, indent=2))
    else:
        print("호출된 도구명: (없음)")
        print("도구 인자:")
        print("{}")
        print("도구 반환값:")
        print("{}")

    print("성능 지표:")
    print(json.dumps(result.metrics, ensure_ascii=False, indent=2))
    print("최종 답변:" if validated else "최종 답변 후보(검증 실패):")
    print(result.answer)


def run_tool_calling(
    question: str,
    client: Any | None = None,
    expected_tool_name: str | None = None,
    expected_arguments: Mapping[str, Any] | None = None,
    selected_grid_id: str | None = None,
    expected_answer_contains: str | tuple[str, ...] | None = None,
    expected_lookup_all: bool | None = None,
    expected_excluded_scope: bool | None = None,
    expected_resolution: str | None = None,
    expected_candidate_fields: tuple[str, ...] | None = None,
) -> ChatResult:
    """공용 서비스로 질문을 실행하고 도구 호출 추적과 최종 답변을 출력한다."""

    print(f"사용자 질문 길이: {len(question)}자")
    try:
        result = run_chat(
            question,
            selected_grid_id=selected_grid_id,
            client=client,
        )
    except ChatProtocolError as exc:
        if exc.result is not None:
            _print_result(exc.result, validated=False)
        raise
    _print_result(result)

    # 라우터 1회가 기본이다. 문서 검색(④)만 발췌를 사람 말로 풀어야 해서
    # 답변 생성에 한 번 더 부르고, 생성이 확률적이라 최대 1회 다시 뽑는다.
    # 그 외 intent가 2회를 부르면 설계 위반이다.
    if result.metrics.get("intent") == "doc_search":
        allowed_calls = range(2, 2 + DOC_ANSWER_MAX_ATTEMPTS)
    else:
        allowed_calls = range(1, 2)
    ollama_call_count = result.metrics.get("ollama_call_count")
    if (
        isinstance(ollama_call_count, bool)
        or not isinstance(ollama_call_count, int)
        or ollama_call_count not in allowed_calls
    ):
        raise RuntimeError(
            "질문 한 건의 Ollama 호출 횟수가 허용 범위"
            f"({allowed_calls.start}~{allowed_calls.stop - 1}회)를 벗어났습니다"
            f" (실제 {ollama_call_count}회, intent={result.metrics.get('intent')})."
        )
    if result.final_thinking:
        raise RuntimeError("Python formatter 이후 별도 LLM 추론이 존재합니다.")

    if expected_tool_name is not None:
        expected_tools = (
            [] if expected_tool_name == NO_TOOL_EXPECTED else [expected_tool_name]
        )
        if result.used_tools != expected_tools:
            actual = result.used_tools[0] if result.used_tools else "(없음)"
            raise RuntimeError(
                f"Qwen이 {expected_tool_name} 대신 {actual} 도구를 선택했습니다."
            )
        if expected_tool_name == NO_TOOL_EXPECTED and (
            result.tool_arguments
            or result.tool_data
            or result.metrics.get("tool_name") is not None
            or result.metrics.get("actual_used_tools") != []
        ):
            raise RuntimeError(
                "Tool 미실행 요청에서 도구 인자·결과 또는 실행 기록이 남았습니다."
            )
    if expected_arguments is not None:
        if not result.used_tools:
            raise RuntimeError("서비스가 기대한 도구를 실행하지 않았습니다.")
        _validate_expected_arguments(
            result.used_tools[0],
            result.tool_arguments,
            expected_arguments,
        )
    if result.used_tools == ["get_grid_data"]:
        _validate_grid_lookup_result(result)
    elif result.used_tools == ["run_simulation"]:
        _validate_simulation_result(result, expected_arguments)
    if (
        expected_lookup_all is not None
        and result.metrics.get("lookup_all") is not expected_lookup_all
    ):
        raise RuntimeError("Python 검증 결과의 lookup_all이 기대값과 다릅니다.")
    if (
        expected_excluded_scope is not None
        and result.metrics.get("excluded_scope") is not expected_excluded_scope
    ):
        raise RuntimeError(
            "Python 검증 결과의 excluded_scope가 기대값과 다릅니다."
        )
    if (
        expected_resolution is not None
        and result.metrics.get("resolution") != expected_resolution
    ):
        raise RuntimeError(
            "Python 검증 결과의 resolution이 기대값과 다릅니다."
        )
    if expected_candidate_fields is not None:
        actual_candidates = result.metrics.get("candidate_fields")
        resolved_candidates_match_fields = (
            expected_resolution == "resolved"
            and expected_candidate_fields == ()
            and isinstance(actual_candidates, list)
            and set(actual_candidates)
            == set(result.metrics.get("requested_fields", []))
        )
        if not resolved_candidates_match_fields and (
            not isinstance(actual_candidates, list)
            or set(actual_candidates) != set(expected_candidate_fields)
        ):
            raise RuntimeError(
                "Python 검증 결과의 candidate_fields가 기대값과 다릅니다."
            )
    if (
        expected_lookup_all is not None
        and expected_arguments is not None
        and expected_tool_name == "get_grid_data"
    ):
        expected_fields = expected_arguments.get("fields")
        if result.metrics.get("requested_fields") != (
            [] if expected_lookup_all else expected_fields
        ):
            raise RuntimeError(
                "Python 검증 결과의 requested_fields가 기대값과 다릅니다."
            )
    if expected_answer_contains is not None:
        required_answers = (
            (expected_answer_contains,)
            if isinstance(expected_answer_contains, str)
            else expected_answer_contains
        )
        if any(item not in result.answer for item in required_answers):
            raise RuntimeError(
                "최종 답변에 필요한 재질문 안내가 포함되지 않았습니다."
            )
    if expected_tool_name is not None:
        print("검증 결과: Tool 선택·인자·반환값·최종 답변 검증 통과")
    return result


def _validate_missing_grid_context() -> None:
    class _NoCallClient:
        call_count = 0

        def chat(self, **_: Any) -> None:
            self.call_count += 1
            raise AssertionError("격자 문맥 없이 Ollama를 호출했습니다.")

    client = _NoCallClient()
    try:
        run_chat(GENERIC_DATA_TELL_QUESTION, client=client)
    except ChatInputError as exc:
        if "격자" not in str(exc):
            raise RuntimeError("격자 선택 안내가 명확하지 않습니다.") from exc
    else:
        raise RuntimeError("선택된 grid_id가 없는데 요청이 실행되었습니다.")

    if client.call_count != 0:
        raise RuntimeError("선택된 grid_id 없이 Ollama가 호출되었습니다.")
    print("선택 격자 없음 검증: Tool 실행 0회, used_tools=[], Ollama 0회")


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="GA:ON Ollama + Qwen 조회·시뮬레이션 Tool Calling PoC"
    )
    parser.add_argument(
        "question",
        nargs="?",
        help=(
            "Qwen에 전달할 단일 질문. 생략하면 범용 조회·미지원 지표·시뮬레이션 "
            "기본 E2E를 모두 실행합니다."
        ),
    )
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    if args.question is not None:
        cases = (
            (args.question, None, None, None, None, None, None, None, None),
        )
    else:
        cases = tuple(
            (
                question,
                None,
                expected_tool_name,
                expected_arguments,
                None,
                None,
                None,
                None,
                None,
            )
            for question, expected_tool_name, expected_arguments in DEFAULT_CASES
        ) + tuple(
            (
                question,
                ROUTING_GRID_ID,
                expected_tool_name,
                expected_arguments,
                expected_answer_contains,
                None,
                None,
                None,
                None,
            )
            for (
                question,
                expected_tool_name,
                expected_arguments,
                expected_answer_contains,
            ) in SELECTED_GRID_ROUTING_CASES
        ) + tuple(
            (
                question,
                ROUTING_GRID_ID,
                "get_grid_data",
                {
                    "grid_id": ROUTING_GRID_ID,
                    "fields": fields,
                },
                None,
                lookup_all,
                excluded_scope,
                None,
                None,
            )
            for (
                question,
                fields,
                lookup_all,
                excluded_scope,
            ) in LOOKUP_SCOPE_CASES
        ) + tuple(
            (
                question,
                ROUTING_GRID_ID,
                expected_tool_name,
                expected_arguments,
                expected_answer_contains,
                expected_lookup_all,
                expected_excluded_scope,
                None,
                None,
            )
            for (
                question,
                expected_tool_name,
                expected_arguments,
                expected_answer_contains,
                expected_lookup_all,
                expected_excluded_scope,
            ) in GENERAL_LOOKUP_ROUTING_CASES
        ) + tuple(
            (
                question,
                ROUTING_GRID_ID,
                expected_tool_name,
                expected_arguments,
                expected_answer_contains,
                None,
                None,
                expected_resolution,
                expected_candidate_fields,
            )
            for (
                question,
                expected_tool_name,
                expected_arguments,
                expected_answer_contains,
                expected_resolution,
                expected_candidate_fields,
            ) in SEMANTIC_ROUTING_CASES
        )
        legacy_case_count = (
            len(DEFAULT_CASES)
            + len(SELECTED_GRID_ROUTING_CASES)
            + len(LOOKUP_SCOPE_CASES)
            + len(GENERAL_LOOKUP_ROUTING_CASES)
        )
        if legacy_case_count != 49:
            raise RuntimeError(
                "기존 qwen3:4b E2E 49개 시나리오 수가 변경되었습니다."
            )
        if len(SEMANTIC_ROUTING_CASES) != 18:
            raise RuntimeError("신규 의미 기반 E2E 18개 시나리오 수가 변경되었습니다.")

    failed_cases: list[tuple[int, str]] = []
    for index, (
        question,
        selected_grid_id,
        expected_tool_name,
        expected_arguments,
        expected_answer_contains,
        expected_lookup_all,
        expected_excluded_scope,
        expected_resolution,
        expected_candidate_fields,
    ) in enumerate(cases, start=1):
        if len(cases) > 1:
            print(f"=== E2E 시나리오 {index}/{len(cases)} ===")
        try:
            run_tool_calling(
                question,
                selected_grid_id=selected_grid_id,
                expected_tool_name=expected_tool_name,
                expected_arguments=expected_arguments,
                expected_answer_contains=expected_answer_contains,
                expected_lookup_all=expected_lookup_all,
                expected_excluded_scope=expected_excluded_scope,
                expected_resolution=expected_resolution,
                expected_candidate_fields=expected_candidate_fields,
            )
        except RuntimeError as exc:
            message = f"실행 오류: {exc}"
            failed_cases.append((index, message))
            print(message, file=sys.stderr)
            print(f"E2E 시나리오 {index} 종료 상태: 1", file=sys.stderr)
        except Exception as exc:
            message = f"Ollama 호출 실패: {exc}"
            failed_cases.append((index, message))
            print(message, file=sys.stderr)
            print(f"E2E 시나리오 {index} 종료 상태: 1", file=sys.stderr)
        else:
            print(f"E2E 시나리오 {index} 종료 상태: 0")
        if index < len(cases):
            print()

    if args.question is None:
        print()
        try:
            _validate_missing_grid_context()
        except RuntimeError as exc:
            failed_cases.append((len(cases) + 1, str(exc)))
            print(f"선택 격자 없음 검증 오류: {exc}", file=sys.stderr)

    exit_code = 1 if failed_cases else 0
    print(f"전체 종료 코드: {exit_code}")
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
