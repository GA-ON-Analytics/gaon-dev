"""Ollama의 qwen3:4b로 GA:ON 조회·시뮬레이션 Tool Calling을 확인하는 CLI."""

from __future__ import annotations

import argparse
import json
import math
import re
import sys
from collections.abc import Mapping
from numbers import Real
from typing import Any

from backend.llm_poc.tools import (
    GET_GRID_DATA_TOOL,
    RUN_SIMULATION_TOOL,
    TOOL_FUNCTIONS,
)


MODEL_NAME = "qwen3:4b"
LOOKUP_QUESTION = "11230_00001 격자의 녹지율과 불투수율을 알려줘."
SIMULATION_QUESTION = (
    "11230_00001 격자의 녹지율을 5%p 높이면 "
    "모델 기준 예상 변화량이 어떻게 돼?"
)
DEFAULT_QUESTION = LOOKUP_QUESTION
DEFAULT_CASES = (
    (
        LOOKUP_QUESTION,
        "get_grid_data",
        {"grid_id": "11230_00001"},
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
TOOL_SCHEMAS = [GET_GRID_DATA_TOOL, RUN_SIMULATION_TOOL]
SYSTEM_PROMPT = """당신은 GA:ON 100m 격자 데이터 조회·정책 시뮬레이션 도우미다.

다음 규칙을 반드시 지켜라.
1. 현재 녹지율·불투수율의 단순 조회에는 반드시 get_grid_data를 호출한다.
2. 정책 변경 후 모델 예측 결과나 예상 변화량 질문에는 반드시 run_simulation을 호출한다.
3. 비율 변화량은 0~1 단위의 부호 있는 delta다. 5%p 증가는 0.05이고 5%p 감소는 -0.05다.
4. 공원 면적 변화량은 ㎡ 단위이며 park_area_delta로 전달한다.
5. success가 false이면 값을 추측하지 말고 error 문자열만 그대로 최종 답변으로 출력한다.
6. success가 true이면 도구 반환값에 없는 수치·효과·통계·온도·원인을 만들거나 보충하지 않는다.
7. 답변 본문에는 사용자에게 보여줄 최종 한국어 답변만 작성하고 내부 추론 과정은 출력하지 않는다. JSON, 객체, 키-값 구조, 코드 블록으로 감싸지 말고 자연어 문장만 출력한다.

get_grid_data 성공 답변 규칙:
8. green_ratio와 impervious_ratio에 100을 곱해 소수점 이하 두 자리 퍼센트로 표현한다.
9. "{grid_id} 격자({gu_name})의 녹지율은 XX.XX%, 불투수율은 YY.YY%입니다." 형식만 사용한다.

run_simulation 성공 답변 규칙:
10. 첫 문장은 "{grid_id} 격자({gu_name})의 모델 예측 anomaly는 A에서 B로 변하며, 모델 기준 예상 변화량(delta_c)은 D℃로 방향문구." 형식으로 쓴다.
11. A와 B는 before_anomaly와 after_anomaly를 소수점 이하 세 자리로, D는 delta_c를 소수점 이하 세 자리로 쓴다. 양수 D의 + 기호는 붙이거나 생략할 수 있고 0에는 붙이지 않는다.
12. delta_c가 음수이면 방향문구는 "감소했습니다", 양수이면 "증가했습니다", 0이면 "변화가 없습니다"로 쓴다.
13. policy_direction_notes가 있으면 각 문자열을 원문 순서대로 별도 문장으로 그대로 덧붙인다.
14. warnings가 있으면 "경고: " 뒤에 각 문자열을 원문 순서대로 " | "로 연결하고, 이어서 "학습 범위 밖 입력은 predict_core에서 내부적으로 보정되었으며, 입력값 그대로가 아니라 도구의 applied_changes가 실제 반영값입니다."라고 설명한다.
15. interpretation_basis 문자열 전체를 반드시 그대로 덧붙인 뒤 limitations의 모든 문자열을 원문 순서대로 하나도 빠뜨리지 않고 그대로 덧붙인다.
16. before_anomaly와 after_anomaly를 실제 절대온도나 기존·변경 후 실제 온도라고 부르지 않는다.
17. delta_c는 모델 기준 예상 변화량으로만 표현하며 실제 정책의 인과효과로 단정하지 않는다.
18. 위에서 요구한 모델 기준 예상 변화량, interpretation_basis, 모든 limitations와 해당되는 모든 warnings·policy_direction_notes를 절대 생략하지 않는다.
"""


def _create_ollama_client() -> Any:
    try:
        from ollama import Client
    except ModuleNotFoundError as exc:
        raise RuntimeError(
            "Ollama Python 패키지가 없습니다. "
            "python -m pip install -r backend/llm_poc/requirements.txt 를 실행하세요."
        ) from exc
    return Client()


def _tool_calls(message: Any) -> list[Any]:
    if isinstance(message, Mapping):
        return list(message.get("tool_calls") or [])
    return list(getattr(message, "tool_calls", None) or [])


def _tool_name_and_arguments(tool_call: Any) -> tuple[str, dict[str, Any]]:
    function = (
        tool_call.get("function")
        if isinstance(tool_call, Mapping)
        else getattr(tool_call, "function", None)
    )
    if function is None:
        raise RuntimeError("Qwen이 잘못된 도구 호출 형식을 반환했습니다.")

    if isinstance(function, Mapping):
        name = function.get("name")
        raw_arguments = function.get("arguments", {})
    else:
        name = getattr(function, "name", None)
        raw_arguments = getattr(function, "arguments", {})

    if not isinstance(name, str) or not name:
        raise RuntimeError("Qwen의 도구 호출에 도구명이 없습니다.")

    if isinstance(raw_arguments, str):
        try:
            decoded_arguments = json.loads(raw_arguments)
        except json.JSONDecodeError as exc:
            raise RuntimeError("Qwen의 도구 인자가 올바른 JSON이 아닙니다.") from exc
        if not isinstance(decoded_arguments, Mapping):
            raise RuntimeError("Qwen의 도구 인자가 객체 형식이 아닙니다.")
        arguments = dict(decoded_arguments)
    elif isinstance(raw_arguments, Mapping):
        arguments = dict(raw_arguments)
    else:
        raise RuntimeError("Qwen의 도구 인자가 객체 형식이 아닙니다.")

    return name, arguments


def _message_content(message: Any) -> str:
    if isinstance(message, Mapping):
        content = message.get("content", "")
    else:
        content = getattr(message, "content", "")
    return content.strip() if isinstance(content, str) else ""


def _message_thinking(message: Any) -> str:
    if isinstance(message, Mapping):
        thinking = message.get("thinking", "")
    else:
        thinking = getattr(message, "thinking", "")
    return thinking.strip() if isinstance(thinking, str) else ""


_NUMBER_PATTERN = re.compile(
    r"(?<![\d.])(?P<number>[+\-−]?\d+(?:\.\d+)?)(?![\d.])"
)
_REASONING_PATTERNS = (
    re.compile(r"\b(?:okay|wait|let's|i need to|the user|according to rule)\b", re.I),
    re.compile(r"추론\s*과정"),
)


def _formatted_number_pattern(value: float, decimals: int = 3) -> str:
    magnitude = re.escape(f"{abs(value):.{decimals}f}")
    if value < 0:
        signed = rf"(?:-|−){magnitude}"
    elif value > 0:
        signed = rf"(?:\+)?{magnitude}"
    else:
        signed = magnitude
    return rf"(?<![\d.]){signed}(?![\d.])"


def _contains_formatted_number(
    answer: str,
    value: float,
    *,
    decimals: int = 3,
    unit: str = "",
) -> bool:
    pattern = _formatted_number_pattern(value, decimals)
    if unit:
        pattern += rf"\s*{re.escape(unit)}"
    return re.search(pattern, answer) is not None


def _collect_tool_numbers(
    value: Any,
    *,
    field_name: str | None = None,
) -> list[float]:
    if field_name == "grid_id":
        return []
    if isinstance(value, Real) and not isinstance(value, bool):
        number = float(value)
        return [number] if math.isfinite(number) else []
    if isinstance(value, Mapping):
        numbers: list[float] = []
        for key, item in value.items():
            numbers.extend(
                _collect_tool_numbers(item, field_name=str(key))
            )
        return numbers
    if isinstance(value, (list, tuple)):
        numbers = []
        for item in value:
            numbers.extend(_collect_tool_numbers(item))
        return numbers
    if isinstance(value, str):
        numbers = []
        for match in _NUMBER_PATTERN.finditer(value):
            normalized = match.group("number").replace("−", "-")
            try:
                numbers.append(float(normalized))
            except ValueError:
                continue
        return numbers
    return []


def _allowed_content_numbers(
    tool_name: str,
    tool_result: Mapping[str, Any],
) -> list[float]:
    allowed = _collect_tool_numbers(tool_result)
    if tool_name == "get_grid_data":
        for field in ("green_ratio", "impervious_ratio"):
            value = tool_result.get(field)
            if isinstance(value, Real) and not isinstance(value, bool):
                allowed.append(float(value) * 100)
    elif tool_name == "run_simulation":
        requested = tool_result.get("requested_changes")
        if isinstance(requested, Mapping):
            for feature, value in requested.items():
                if (
                    "ratio" in str(feature)
                    and isinstance(value, Real)
                    and not isinstance(value, bool)
                ):
                    allowed.append(float(value) * 100)
        applied = tool_result.get("applied_changes")
        if isinstance(applied, Mapping):
            for feature, values in applied.items():
                if "ratio" not in str(feature) or not isinstance(values, Mapping):
                    continue
                for value in values.values():
                    if isinstance(value, Real) and not isinstance(value, bool):
                        allowed.append(float(value) * 100)
    return allowed


def _matches_allowed_number(token: str, allowed: list[float]) -> bool:
    normalized = token.replace("−", "-")
    try:
        value = float(normalized)
    except ValueError:
        return False

    unsigned = normalized.lstrip("+-")
    if "." in unsigned:
        decimals = len(unsigned.split(".", 1)[1])
        tolerance = (0.5 * (10 ** -decimals)) + 1e-12
    else:
        tolerance = 1e-12
    return any(
        math.isclose(value, candidate, rel_tol=0, abs_tol=tolerance)
        for candidate in allowed
    )


def _validate_supported_numbers(
    answer: str,
    tool_name: str,
    tool_result: Mapping[str, Any],
) -> None:
    grid_id = str(tool_result.get("grid_id") or "")
    answer_without_grid_id = answer.replace(grid_id, "") if grid_id else answer
    allowed = _allowed_content_numbers(tool_name, tool_result)
    unsupported = [
        match.group("number")
        for match in _NUMBER_PATTERN.finditer(answer_without_grid_id)
        if not _matches_allowed_number(match.group("number"), allowed)
    ]
    if unsupported:
        raise RuntimeError(
            "최종 message.content에 도구 결과에서 유도할 수 없는 숫자가 있습니다: "
            + ", ".join(unsupported)
        )


def _validate_no_reasoning_trace(answer: str) -> None:
    stripped_answer = answer.strip()
    lowered_answer = answer.lower()
    if "<think>" in lowered_answer or "</think>" in lowered_answer:
        raise RuntimeError("최종 message.content에 think 태그가 포함되었습니다.")
    if "```" in answer:
        raise RuntimeError("최종 message.content가 코드 블록으로 감싸졌습니다.")
    if (
        (stripped_answer.startswith("{") and stripped_answer.endswith("}"))
        or (stripped_answer.startswith("[") and stripped_answer.endswith("]"))
    ):
        raise RuntimeError("최종 message.content가 JSON 객체 또는 배열 형태입니다.")
    try:
        json.loads(stripped_answer)
    except (json.JSONDecodeError, TypeError):
        pass
    else:
        raise RuntimeError("최종 message.content가 자연어가 아닌 JSON 값입니다.")
    if re.search(
        r"""(?ix)(?:["'])?(?:thinking|content)(?:["'])?\s*:""",
        answer,
    ):
        raise RuntimeError("최종 message.content에 내부 응답 키가 포함되었습니다.")
    if any(pattern.search(answer) for pattern in _REASONING_PATTERNS):
        raise RuntimeError("최종 message.content에 추론 과정이 포함되었습니다.")


def _validate_grid_answer(
    answer: str,
    tool_result: Mapping[str, Any],
) -> None:
    required_text = (
        str(tool_result["grid_id"]),
        str(tool_result["gu_name"]),
        "녹지율",
        "불투수율",
        f"{float(tool_result['green_ratio']) * 100:.2f}%",
        f"{float(tool_result['impervious_ratio']) * 100:.2f}%",
    )
    missing = [text for text in required_text if text not in answer]
    if missing:
        raise RuntimeError(
            "조회 최종 답변의 필수 정보가 누락되었습니다: " + ", ".join(missing)
        )
    _validate_supported_numbers(answer, "get_grid_data", tool_result)


def _validate_simulation_answer(
    answer: str,
    tool_result: Mapping[str, Any],
) -> None:
    for field in ("grid_id", "gu_name"):
        value = str(tool_result[field])
        if value not in answer:
            raise RuntimeError(f"시뮬레이션 최종 답변에 {field}가 누락되었습니다.")

    before = float(tool_result["before_anomaly"])
    after = float(tool_result["after_anomaly"])
    delta = float(tool_result["delta_c"])
    if not _contains_formatted_number(answer, before):
        raise RuntimeError("최종 답변에 before_anomaly 값이 누락되거나 다릅니다.")
    if not _contains_formatted_number(answer, after):
        raise RuntimeError("최종 답변에 after_anomaly 값이 누락되거나 다릅니다.")
    if not _contains_formatted_number(answer, delta, unit="℃"):
        raise RuntimeError("최종 답변에 delta_c 값이 누락되거나 다릅니다.")

    delta_pattern = _formatted_number_pattern(delta)
    direction_match = re.search(
        rf"(?:delta_c|예상\s*변화량).{{0,100}}?"
        rf"{delta_pattern}\s*℃.{{0,30}}?(증가|감소|변화가\s*없)",
        answer,
        re.DOTALL,
    )
    if direction_match is None:
        raise RuntimeError("delta_c와 방향 설명을 함께 확인할 수 없습니다.")
    direction = direction_match.group(1)
    if delta > 0 and direction != "증가":
        raise RuntimeError("양수 delta_c를 증가로 설명하지 않았습니다.")
    if delta < 0 and direction != "감소":
        raise RuntimeError("음수 delta_c를 감소로 설명하지 않았습니다.")
    if delta == 0 and not direction.startswith("변화가"):
        raise RuntimeError("0인 delta_c를 변화 없음으로 설명하지 않았습니다.")

    if "before_anomaly" not in answer or "after_anomaly" not in answer:
        raise RuntimeError("anomaly 필드의 의미 설명이 누락되었습니다.")
    if re.search(
        r"절대\s*온도.{0,24}(?:아니|아님|않|별개|구분)",
        answer,
        re.DOTALL,
    ) is None:
        raise RuntimeError(
            "before_anomaly와 after_anomaly가 절대온도가 아니라는 설명이 누락되었습니다."
        )
    if re.search(
        r"(?:기존|변경\s*후)\s*(?:실제\s*)?온도",
        answer,
    ):
        raise RuntimeError("anomaly를 기존 또는 변경 후 실제 온도로 잘못 표현했습니다.")

    if "delta_c" not in answer or "모델 기준 예상 변화량" not in answer:
        raise RuntimeError("delta_c의 모델 기준 예상 변화량 설명이 누락되었습니다.")
    if re.search(
        r"인과\s*효과.{0,40}(?:단정할 수 없|단정하지 않|아니|보장하지 않)",
        answer,
        re.DOTALL,
    ) is None:
        raise RuntimeError("실제 정책의 인과효과로 단정할 수 없다는 한계가 누락되었습니다.")
    for term in ("비용", "토지", "공사기간", "행정 가능성"):
        if term not in answer:
            raise RuntimeError(f"필수 모델 한계가 누락되었습니다: {term}")
    if re.search(
        r"반영.{0,20}(?:않|안|되지|못|제외|미반영)",
        answer,
        re.DOTALL,
    ) is None:
        raise RuntimeError("비용 등 현실 조건이 반영되지 않았다는 설명이 누락되었습니다.")

    policy_notes = list(tool_result.get("policy_direction_notes") or [])
    for note in policy_notes:
        if str(note) not in answer:
            raise RuntimeError("일반적인 저감 정책과 반대 방향이라는 설명이 누락되었습니다.")

    warnings = list(tool_result.get("warnings") or [])
    for warning in warnings:
        if str(warning) not in answer:
            raise RuntimeError("predict_core가 반환한 경고가 최종 답변에서 누락되었습니다.")
    if warnings and not (
        "학습 범위" in answer
        and ("보정" in answer or "clip" in answer)
        and ("applied_changes" in answer or "실제 반영값" in answer)
    ):
        raise RuntimeError("학습 범위 보정과 실제 적용값에 대한 설명이 누락되었습니다.")

    _validate_supported_numbers(answer, "run_simulation", tool_result)


def _validate_final_answer(
    answer: str,
    tool_name: str,
    tool_result: Mapping[str, Any],
) -> None:
    """message.content의 필수 값·방향·해석·한계를 의미 단위로 검증한다."""

    _validate_no_reasoning_trace(answer)
    if tool_result.get("success") is not True:
        error = tool_result.get("error")
        if not isinstance(error, str) or error not in answer:
            raise RuntimeError(
                "Qwen의 오류 답변에 도구가 반환한 error가 포함되지 않았습니다."
            )
        _validate_supported_numbers(answer, tool_name, tool_result)
        return

    if tool_name == "get_grid_data":
        _validate_grid_answer(answer, tool_result)
    elif tool_name == "run_simulation":
        _validate_simulation_answer(answer, tool_result)
    else:
        raise RuntimeError(f"최종 답변 검증을 지원하지 않는 도구입니다: {tool_name}")


def _validate_expected_arguments(
    tool_name: str,
    tool_arguments: Mapping[str, Any],
    expected_arguments: Mapping[str, Any],
) -> None:
    if tool_arguments.get("grid_id") != expected_arguments.get("grid_id"):
        raise RuntimeError("Qwen이 질문의 grid_id를 정확히 전달하지 않았습니다.")

    if tool_name == "get_grid_data":
        if set(tool_arguments) != {"grid_id"}:
            raise RuntimeError("get_grid_data에 예상하지 않은 인자가 전달되었습니다.")
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


def run_tool_calling(
    question: str,
    client: Any | None = None,
    expected_tool_name: str | None = None,
    expected_arguments: Mapping[str, Any] | None = None,
) -> str:
    """질문을 전송하고 도구 호출 추적과 최종 답변을 출력한다."""

    ollama_client = client or _create_ollama_client()
    print("사용자 질문:")
    print(question)
    messages: list[Any] = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": question},
    ]

    first_response = ollama_client.chat(
        model=MODEL_NAME,
        messages=messages,
        tools=TOOL_SCHEMAS,
        think=True,
        options={"temperature": 0},
    )
    assistant_message = first_response.message
    messages.append(assistant_message)
    print("첫 번째 message.thinking:")
    print(_message_thinking(assistant_message) or "(없음)")
    print("첫 번째 message.content:")
    print(_message_content(assistant_message) or "(없음)")

    calls = _tool_calls(assistant_message)
    if not calls:
        raise RuntimeError("Qwen이 도구를 호출하지 않았습니다.")
    if len(calls) != 1:
        raise RuntimeError("Qwen은 도구를 정확히 한 번 호출해야 합니다.")

    tool_result: dict[str, Any] | None = None
    selected_tool_name: str | None = None
    for tool_call in calls:
        tool_name, tool_arguments = _tool_name_and_arguments(tool_call)
        selected_tool_name = tool_name
        print(f"호출된 도구명: {tool_name}")
        print("도구 인자:")
        print(json.dumps(tool_arguments, ensure_ascii=False, indent=2))

        if expected_tool_name is not None and tool_name != expected_tool_name:
            raise RuntimeError(
                f"Qwen이 {expected_tool_name} 대신 {tool_name} 도구를 선택했습니다."
            )
        if expected_arguments is not None:
            _validate_expected_arguments(
                tool_name,
                tool_arguments,
                expected_arguments,
            )

        tool_function = TOOL_FUNCTIONS.get(tool_name)
        if tool_function is None:
            tool_result = {
                "success": False,
                "grid_id": tool_arguments.get("grid_id"),
                "error": f"허용되지 않은 도구입니다: {tool_name}",
            }
            print("도구 반환값:")
            print(json.dumps(tool_result, ensure_ascii=False, indent=2))
            raise RuntimeError("Qwen이 허용되지 않은 도구를 호출했습니다.")
        else:
            try:
                tool_result = tool_function(**tool_arguments)
            except TypeError as exc:
                tool_result = {
                    "success": False,
                    "grid_id": tool_arguments.get("grid_id"),
                    "error": f"도구 인자가 올바르지 않습니다: {exc}",
                }

        print("도구 반환값:")
        print(json.dumps(tool_result, ensure_ascii=False, indent=2))
        messages.append(
            {
                "role": "tool",
                "tool_name": tool_name,
                "content": json.dumps(tool_result, ensure_ascii=False),
            }
        )

    if tool_result is None or selected_tool_name is None:
        raise RuntimeError("도구 반환값이 없습니다.")

    final_response = ollama_client.chat(
        model=MODEL_NAME,
        messages=messages,
        think=True,
        options={"temperature": 0},
    )
    final_message = final_response.message
    final_thinking = _message_thinking(final_message)
    final_answer = _message_content(final_message)
    print("최종 message.thinking:")
    print(final_thinking or "(없음)")
    print("최종 message.content:")
    print(final_answer or "(없음)")
    if not final_answer:
        raise RuntimeError("Qwen이 최종 답변을 반환하지 않았습니다.")
    try:
        _validate_final_answer(final_answer, selected_tool_name, tool_result)
    except RuntimeError:
        print("최종 답변 후보(검증 실패):")
        print(final_answer)
        raise

    print("최종 답변:")
    print(final_answer)
    return final_answer


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="GA:ON Ollama + Qwen 조회·시뮬레이션 Tool Calling PoC"
    )
    parser.add_argument(
        "question",
        nargs="?",
        help=(
            "Qwen에 전달할 단일 질문. 생략하면 조회·시뮬레이션 기본 E2E 두 건을 "
            "모두 실행합니다."
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
