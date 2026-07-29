"""Ollama의 qwen3:4b로 GA:ON 조회·시뮬레이션 Tool Calling을 확인하는 CLI."""

from __future__ import annotations

import argparse
import json
import math
import sys
from collections.abc import Mapping
from typing import Any

from backend.llm_poc.chat_service import ChatProtocolError, ChatResult, run_chat
from backend.llm_poc.tools import ALLOWED_GRID_FIELDS


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


def _validate_expected_arguments(
    tool_name: str,
    tool_arguments: Mapping[str, Any],
    expected_arguments: Mapping[str, Any],
) -> None:
    if tool_arguments.get("grid_id") != expected_arguments.get("grid_id"):
        raise RuntimeError("Qwen이 질문의 grid_id를 정확히 전달하지 않았습니다.")

    if tool_name == "get_grid_data":
        if not set(tool_arguments).issubset({"grid_id", "fields"}):
            raise RuntimeError("get_grid_data에 예상하지 않은 인자가 전달되었습니다.")
        expected_fields = expected_arguments.get("fields")
        if expected_fields is not None:
            actual_fields = tool_arguments.get("fields")
            if actual_fields != expected_fields:
                raise RuntimeError(
                    "Qwen이 질문의 fields를 요청 순서대로 정확히 전달하지 않았습니다."
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
            raise RuntimeError(f"Qwen이 질문의 {name}을 정확히 전달하지 않았습니다.")


def _print_result(result: ChatResult, *, validated: bool = True) -> None:
    print(
        "라우터 추론 분리 상태: "
        f"{'있음' if result.first_thinking else '없음'}"
        f" ({len(result.first_thinking)}자)"
    )
    print(f"라우터 일반 본문 길이: {len(result.first_content)}자")

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
) -> str:
    """공용 서비스로 질문을 실행하고 도구 호출 추적과 최종 답변을 출력한다."""

    print(f"사용자 질문 길이: {len(question)}자")
    try:
        result = run_chat(question, client=client)
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
            raise RuntimeError("Qwen이 도구를 호출하지 않았습니다.")
        _validate_expected_arguments(
            result.used_tools[0],
            result.tool_arguments,
            expected_arguments,
        )
    if expected_tool_name is not None:
        print("검증 결과: Tool 선택·인자·반환값·최종 답변 검증 통과")
    return result.answer


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
        cases = ((args.question, None, None),)
    else:
        cases = DEFAULT_CASES

    failed_cases: list[tuple[int, str]] = []
    for index, (question, expected_tool_name, expected_arguments) in enumerate(
        cases,
        start=1,
    ):
        if len(cases) > 1:
            print(f"=== E2E 시나리오 {index}/{len(cases)} ===")
        try:
            run_tool_calling(
                question,
                expected_tool_name=expected_tool_name,
                expected_arguments=expected_arguments,
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

    exit_code = 1 if failed_cases else 0
    print(f"전체 종료 코드: {exit_code}")
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
