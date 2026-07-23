"""Ollama의 qwen3:4b로 get_grid_data Tool Calling을 확인하는 CLI."""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Mapping
from typing import Any

from backend.llm_poc.tools import GET_GRID_DATA_TOOL, TOOL_FUNCTIONS


MODEL_NAME = "qwen3:4b"
DEFAULT_QUESTION = "11230_00001 격자의 녹지율과 불투수율을 알려줘."
SYSTEM_PROMPT = """당신은 GA:ON 격자 데이터 조회 도우미다.

다음 규칙을 반드시 지켜라.
1. 격자의 녹지율이나 불투수율 질문에는 답하기 전에 반드시 get_grid_data 도구를 호출한다.
2. success가 true이면 최종 답변은 도구 반환값에 있는 grid_id, gu_name, green_ratio, impervious_ratio만 근거로 작성한다.
3. 도구 반환값에 없는 수치, 통계, 온도, 원인, 비교, 추정값을 만들거나 보충하지 않는다.
4. success가 false이면 누락된 값을 추측하지 말고 error 문자열을 그대로 최종 답변으로 출력한다.
5. success가 true이면 green_ratio와 impervious_ratio의 0~1 원본값에 100을 곱해 퍼센트로 표현한다.
6. 퍼센트는 소수점 이하 두 자리로 고정하고 % 기호를 붙이며, 이 변환 외에는 어떤 계산도 하지 않는다.
7. lst_mean을 예측 온도라고 표현하지 않고, before_anomaly를 절대 온도라고 표현하지 않는다.
8. success가 true이면 "{grid_id} 격자({gu_name})의 녹지율은 XX.XX%, 불투수율은 YY.YY%입니다." 형식으로 답한다.
9. 최종 답변은 간결한 한국어로 작성한다.
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


def _validate_final_answer(answer: str, tool_result: Mapping[str, Any]) -> None:
    """최종 답변이 도구 결과로만 만든 허용 문장과 일치하는지 확인한다."""

    if tool_result.get("success") is not True:
        error = tool_result.get("error")
        if not isinstance(error, str) or answer != error:
            raise RuntimeError(
                "Qwen의 오류 답변이 도구가 반환한 error와 일치하지 않습니다."
            )
        return

    expected_answer = (
        f"{tool_result['grid_id']} 격자({tool_result['gu_name']})의 "
        f"녹지율은 {float(tool_result['green_ratio']) * 100:.2f}%, "
        f"불투수율은 {float(tool_result['impervious_ratio']) * 100:.2f}%입니다."
    )
    if answer != expected_answer:
        raise RuntimeError(
            "Qwen의 최종 답변이 도구 반환값으로 만든 허용 문장과 일치하지 않습니다."
        )


def run_tool_calling(question: str, client: Any | None = None) -> str:
    """질문을 전송하고 도구 호출 추적과 최종 답변을 출력한다."""

    ollama_client = client or _create_ollama_client()
    messages: list[Any] = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": question},
    ]

    first_response = ollama_client.chat(
        model=MODEL_NAME,
        messages=messages,
        tools=[GET_GRID_DATA_TOOL],
        think=True,
        options={"temperature": 0},
    )
    assistant_message = first_response.message
    messages.append(assistant_message)

    calls = _tool_calls(assistant_message)
    if not calls:
        raise RuntimeError("Qwen이 get_grid_data 도구를 호출하지 않았습니다.")
    if len(calls) != 1:
        raise RuntimeError("Qwen은 get_grid_data 도구를 정확히 한 번 호출해야 합니다.")

    tool_result: dict[str, Any] | None = None
    for tool_call in calls:
        tool_name, tool_arguments = _tool_name_and_arguments(tool_call)
        print(f"호출된 도구명: {tool_name}")
        print("도구 인자:")
        print(json.dumps(tool_arguments, ensure_ascii=False, indent=2))

        tool_function = TOOL_FUNCTIONS.get(tool_name)
        if tool_function is None:
            tool_result = {
                "success": False,
                "grid_id": tool_arguments.get("grid_id"),
                "gu_name": None,
                "green_ratio": None,
                "impervious_ratio": None,
                "error": f"허용되지 않은 도구입니다: {tool_name}",
            }
            print("도구 반환값:")
            print(json.dumps(tool_result, ensure_ascii=False, indent=2))
            raise RuntimeError("Qwen이 get_grid_data가 아닌 도구를 호출했습니다.")
        else:
            try:
                tool_result = tool_function(**tool_arguments)
            except TypeError as exc:
                tool_result = {
                    "success": False,
                    "grid_id": tool_arguments.get("grid_id"),
                    "gu_name": None,
                    "green_ratio": None,
                    "impervious_ratio": None,
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

    if tool_result is None:
        raise RuntimeError("get_grid_data 도구 반환값이 없습니다.")

    final_response = ollama_client.chat(
        model=MODEL_NAME,
        messages=messages,
        think=True,
        options={"temperature": 0},
    )
    final_answer = _message_content(final_response.message)
    if not final_answer:
        raise RuntimeError("Qwen이 최종 답변을 반환하지 않았습니다.")
    try:
        _validate_final_answer(final_answer, tool_result)
    except RuntimeError:
        print("최종 답변 후보(검증 실패):")
        print(final_answer)
        raise

    print("최종 답변:")
    print(final_answer)
    return final_answer


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="GA:ON Ollama + Qwen get_grid_data Tool Calling 최소 PoC"
    )
    parser.add_argument(
        "question",
        nargs="?",
        default=DEFAULT_QUESTION,
        help=f"Qwen에 전달할 질문 (기본값: {DEFAULT_QUESTION})",
    )
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    try:
        run_tool_calling(args.question)
    except RuntimeError as exc:
        print(f"실행 오류: {exc}", file=sys.stderr)
        return 1
    except Exception as exc:
        print(f"Ollama 호출 실패: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
