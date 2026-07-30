"""Ollama의 qwen3:4b로 GA:ON 조회·시뮬레이션 Tool Calling을 확인하는 CLI."""

from __future__ import annotations

import argparse
import json
import math
import sys
from collections.abc import Mapping
from typing import Any

from backend.llm_poc.chat_service import (
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
EXCLUDED_SCOPE_GREEN_QUESTION = "모든 데이터 말고 녹지율만 알려줘"
EXCLUDED_SCOPE_NDVI_QUESTION = "전체는 필요 없고 NDVI만 보여줘"
EXCLUDED_SCOPE_RATIOS_QUESTION = (
    "전부 말고 녹지율과 불투수율만 알려줘"
)
EXCLUDED_SCOPE_PARK_QUESTION = "다 보여주지 말고 공원면적만 알려줘"
GREEN_INCREASE_QUESTION = "녹지율을 5%p 올려줘"
IMPERVIOUS_DECREASE_QUESTION = "불투수율을 3%p 낮춰줘"
PARK_AREA_INCREASE_QUESTION = "공원 면적을 500㎡ 늘려줘"
COMBINED_RATIO_QUESTION = (
    "녹지율을 5%p 올리고 불투수율을 3%p 낮춰줘"
)
MULTILINE_SIMULATION_QUESTION = (
    "녹지율을 5%p 올려줘\n"
    "불투수율을 3%p 낮춰줘\n"
    "공원 면적을 500㎡ 늘려줘"
)
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
NO_TOOL_EXPECTED = "__no_tool__"
DEFAULT_QUESTION = LOOKUP_QUESTION
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
        {
            "grid_id": "11230_00001",
            "green_ratio_delta": 0.05,
            "impervious_ratio_delta": 0.0,
            "park_area_delta": 0.0,
        },
    ),
)
SELECTED_GRID_ROUTING_CASES = (
    (
        GREEN_INCREASE_QUESTION,
        "run_simulation",
        {
            "grid_id": ROUTING_GRID_ID,
            "green_ratio_delta": 0.05,
            "impervious_ratio_delta": 0.0,
            "park_area_delta": 0.0,
        },
        None,
    ),
    (
        IMPERVIOUS_DECREASE_QUESTION,
        "run_simulation",
        {
            "grid_id": ROUTING_GRID_ID,
            "green_ratio_delta": 0.0,
            "impervious_ratio_delta": -0.03,
            "park_area_delta": 0.0,
        },
        None,
    ),
    (
        PARK_AREA_INCREASE_QUESTION,
        "run_simulation",
        {
            "grid_id": ROUTING_GRID_ID,
            "green_ratio_delta": 0.0,
            "impervious_ratio_delta": 0.0,
            "park_area_delta": 500.0,
        },
        None,
    ),
    (
        COMBINED_RATIO_QUESTION,
        "run_simulation",
        {
            "grid_id": ROUTING_GRID_ID,
            "green_ratio_delta": 0.05,
            "impervious_ratio_delta": -0.03,
            "park_area_delta": 0.0,
        },
        None,
    ),
    (
        MULTILINE_SIMULATION_QUESTION,
        "run_simulation",
        {
            "grid_id": ROUTING_GRID_ID,
            "green_ratio_delta": 0.05,
            "impervious_ratio_delta": -0.03,
            "park_area_delta": 500.0,
        },
        None,
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
        {
            "grid_id": ROUTING_GRID_ID,
            "green_ratio_delta": 0.05,
            "impervious_ratio_delta": 0.0,
            "park_area_delta": 0.0,
        },
        "%p",
    ),
    (
        GREEN_PER_INCREASE_QUESTION,
        "run_simulation",
        {
            "grid_id": ROUTING_GRID_ID,
            "green_ratio_delta": 0.05,
            "impervious_ratio_delta": 0.0,
            "park_area_delta": 0.0,
        },
        "%p",
    ),
    (
        GREEN_PLUS_FIVE_INCREASE_QUESTION,
        "run_simulation",
        {
            "grid_id": ROUTING_GRID_ID,
            "green_ratio_delta": 0.05,
            "impervious_ratio_delta": 0.0,
            "park_area_delta": 0.0,
        },
        "%p",
    ),
    (
        IMPERVIOUS_PER_DECREASE_QUESTION,
        "run_simulation",
        {
            "grid_id": ROUTING_GRID_ID,
            "green_ratio_delta": 0.0,
            "impervious_ratio_delta": -0.03,
            "park_area_delta": 0.0,
        },
        "%p",
    ),
    (
        PARK_SQUARE_INCREASE_QUESTION,
        "run_simulation",
        {
            "grid_id": ROUTING_GRID_ID,
            "green_ratio_delta": 0.0,
            "impervious_ratio_delta": 0.0,
            "park_area_delta": 500.0,
        },
        None,
    ),
    (
        COMBINED_IMPLICIT_UNIT_QUESTION,
        "run_simulation",
        {
            "grid_id": ROUTING_GRID_ID,
            "green_ratio_delta": 0.05,
            "impervious_ratio_delta": -0.03,
            "park_area_delta": 0.0,
        },
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
        "park_area_delta",
    }
    if not set(tool_arguments).issubset(allowed_arguments):
        raise RuntimeError("run_simulation에 예상하지 않은 인자가 전달되었습니다.")

    for name in (
        "green_ratio_delta",
        "impervious_ratio_delta",
        "park_area_delta",
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
    expected_answer_contains: str | None = None,
    expected_lookup_all: bool | None = None,
    expected_excluded_scope: bool | None = None,
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

    ollama_call_count = result.metrics.get("ollama_call_count")
    if (
        isinstance(ollama_call_count, bool)
        or not isinstance(ollama_call_count, int)
        or ollama_call_count > 1
    ):
        raise RuntimeError(
            "질문 한 건에서 Ollama가 한 번보다 많이 호출되었습니다."
        )
    if result.used_tools and ollama_call_count != 1:
        raise RuntimeError("Tool Calling 질문의 Ollama 호출 횟수가 1이 아닙니다.")
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
    if (
        expected_answer_contains is not None
        and expected_answer_contains not in result.answer
    ):
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
        run_chat("전체 데이터 알려줘", client=client)
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
        cases = ((args.question, None, None, None, None, None, None),)
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
            )
            for (
                question,
                fields,
                lookup_all,
                excluded_scope,
            ) in LOOKUP_SCOPE_CASES
        )

    failed_cases: list[tuple[int, str]] = []
    for index, (
        question,
        selected_grid_id,
        expected_tool_name,
        expected_arguments,
        expected_answer_contains,
        expected_lookup_all,
        expected_excluded_scope,
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
