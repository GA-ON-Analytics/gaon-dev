"""GA:ON Ollama Tool Calling을 API와 CLI에서 함께 쓰는 서비스 계층."""

from __future__ import annotations

import json
import logging
import math
import os
import re
import time
from collections.abc import Mapping
from dataclasses import dataclass
from numbers import Real
from typing import Any

from backend.llm_poc.tools import (
    ALLOWED_GRID_FIELDS,
    DEFAULT_GRID_FIELDS,
    GET_GRID_DATA_TOOL,
    GRID_FIELD_SPECS,
    RUN_SIMULATION_TOOL,
    TOOL_FUNCTIONS,
    format_grid_field_value,
)


MODEL_NAME = "qwen3:4b"
DEFAULT_LLM_TIMEOUT_SECONDS = 120.0
ROUTER_NUM_PREDICT = 3072
MODEL_KEEP_ALIVE = "5m"
TOOL_SCHEMAS = [GET_GRID_DATA_TOOL, RUN_SIMULATION_TOOL]
LOGGER = logging.getLogger("uvicorn.error")
_GRID_FIELD_LABELS = "、".join(
    str(GRID_FIELD_SPECS[field]["label"]) for field in ALLOWED_GRID_FIELDS
)
SUPPORTED_SCOPE_ANSWER = (
    "현재 GA:ON AI는 선택한 100m 격자의 다음 현재 데이터를 조회할 수 있습니다: "
    f"{_GRID_FIELD_LABELS}. "
    "사용자가 지정한 녹지율·불투수율·공원 면적 변경 시나리오만 지원합니다. "
    "정책 추천, 모델 설명, 문서 검색은 현재 지원하지 않습니다."
)
AMBIGUOUS_RATIO_CHANGE_ANSWER = (
    "비율 변경은 상대 퍼센트(%)가 아니라 퍼센트포인트(%p)로 입력해 주세요."
)

SYSTEM_PROMPT = """당신은 GA:ON Tool 라우터다.
사용자에게 답변 문장을 작성하지 말고, 지원 요청이면 도구를 정확히 한 번 호출한다.
- 현재 격자 지표·특성·데이터 조회: get_grid_data
- 녹지율·불투수율·반경 500m 내 공원 면적 변경 후 모델 결과: run_simulation
- 비율 변화량은 0~1 단위의 부호 있는 delta다. 5%p 증가는 0.05, 감소는 -0.05다.
- %p가 아닌 % 변경 요청은 모호하므로 도구를 호출하지 않는다.
- 공원 면적 변화량은 ㎡ 단위다.
- 지원하지 않는 지표·정책 추천·모델 설명·문서 검색에는 도구를 호출하지 않는다.
"""

_GRID_ID_PATTERN = re.compile(r"(?<![\d_])\d{5}_\d{5}(?![\d_])")
_NUMBER_PATTERN = re.compile(
    r"(?<![\d.])"
    r"(?P<number>[+\-−]?(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?)"
    r"(?![\d.])"
)
_ALL_GRID_DATA_PATTERNS = (
    re.compile(r"(?:전체|모든)\s*(?:격자\s*)?(?:데이터|지표|특성)"),
    re.compile(r"19\s*개\s*(?:데이터|지표|특성|필드)"),
)
_AMBIGUOUS_RATIO_CHANGE_PATTERN = re.compile(
    r"\d+(?:\.\d+)?\s*%(?!\s*[pP])"
    r".{0,20}(?:올리|높이|늘리|낮추|줄이|증가|감소|변경)",
)
_EXPLICIT_SIMULATION_PATTERN = re.compile(
    r"\d+(?:\.\d+)?\s*(?:%\s*[pP]|㎡)"
    r".{0,24}(?:올리|높이|늘리|낮추|줄이|증가|감소|변경)",
)
_REASONING_PATTERNS = (
    re.compile(r"\b(?:okay|wait|let's|i need to|the user|according to rule)\b", re.I),
    re.compile(r"추론\s*과정"),
)
_UNSUPPORTED_SCOPE_PATTERNS = (
    re.compile(
        r"(?:정책|대책|방안).{0,20}"
        r"(?:추천|제안|골라|선택|우선|최적|가장\s*(?:좋|효과)|어떤)",
    ),
    re.compile(
        r"(?:추천|제안|최적|가장\s*(?:좋|효과)|어떤).{0,20}"
        r"(?:정책|대책|방안)",
    ),
    re.compile(r"(?:random\s*forest|랜덤\s*포레스트|\bRF\b)", re.IGNORECASE),
    re.compile(
        r"(?:문서|자료|가이드|서비스\s*설명).{0,20}(?:검색|찾|조회)"
        r"|(?:검색|찾|조회).{0,20}(?:문서|자료|가이드|서비스\s*설명)",
    ),
    re.compile(r"\bRAG\b", re.IGNORECASE),
    re.compile(r"(?:인구\s*데이터|인구밀도|인구수)"),
)


def _compact_lookup_text(value: str) -> str:
    return re.sub(r"\s+", "", value).casefold()


def _recognized_lookup_fields(message: str) -> list[str]:
    """질문의 확정된 별칭을 긴 표현 우선으로 모델 필드에 연결한다."""

    if any(pattern.search(message) for pattern in _ALL_GRID_DATA_PATTERNS):
        return list(ALLOWED_GRID_FIELDS)

    compact_message = _compact_lookup_text(message)
    candidates: list[tuple[int, int, str]] = []
    for field in ALLOWED_GRID_FIELDS:
        spec = GRID_FIELD_SPECS[field]
        terms = dict.fromkeys(
            [
                field,
                str(spec["label"]),
                *(str(alias) for alias in spec["aliases"]),
            ]
        )
        for term in terms:
            compact_term = _compact_lookup_text(term)
            if not compact_term:
                continue
            start = compact_message.find(compact_term)
            while start >= 0:
                candidates.append((start, start + len(compact_term), field))
                start = compact_message.find(compact_term, start + 1)

    accepted: list[tuple[int, int, str]] = []
    for candidate in sorted(
        candidates,
        key=lambda item: (-(item[1] - item[0]), item[0]),
    ):
        start, end, _ = candidate
        if any(start < accepted_end and end > accepted_start for accepted_start, accepted_end, _ in accepted):
            continue
        accepted.append(candidate)

    ordered_fields: list[str] = []
    for _, _, field in sorted(accepted, key=lambda item: item[0]):
        if field not in ordered_fields:
            ordered_fields.append(field)
    return ordered_fields


class ChatServiceError(RuntimeError):
    """채팅 서비스에서 외부 계층이 안전하게 변환할 수 있는 기본 예외."""


class ChatInputError(ChatServiceError):
    """사용자 입력 또는 격자 문맥이 올바르지 않을 때 발생한다."""


class OllamaConnectionError(ChatServiceError):
    """Ollama 서버에 연결할 수 없을 때 발생한다."""


class OllamaTimeoutError(ChatServiceError):
    """Ollama 요청 시간이 초과될 때 발생한다."""


class OllamaModelError(ChatServiceError):
    """Ollama 모델 요청 자체가 실패할 때 발생한다."""


class ChatProtocolError(ChatServiceError):
    """모델의 Tool Calling 또는 최종 답변 계약이 올바르지 않을 때 발생한다."""

    def __init__(
        self,
        message: str,
        *,
        result: ChatResult | None = None,
    ) -> None:
        super().__init__(message)
        self.result = result


@dataclass(frozen=True)
class ChatResult:
    """API 응답과 CLI 진단 출력에 필요한 검증 완료 결과."""

    answer: str
    used_tools: list[str]
    tool_data: dict[str, Any]
    warnings: list[str]
    limitations: list[str]
    tool_arguments: dict[str, Any]
    first_thinking: str
    first_content: str
    final_thinking: str
    final_content: str
    metrics: dict[str, Any]


def _new_chat_metrics() -> dict[str, Any]:
    return {
        "chat_total_seconds": 0.0,
        "llm_router_seconds": 0.0,
        "tool_execution_seconds": 0.0,
        "answer_format_seconds": 0.0,
        "validation_seconds": 0.0,
        "ollama_call_count": 0,
        "load_duration": None,
        "prompt_eval_duration": None,
        "eval_duration": None,
        "prompt_eval_count": None,
        "eval_count": None,
        "status": "error",
    }


def _capture_ollama_metrics(
    metrics: dict[str, Any],
    response: Any,
) -> None:
    for field in (
        "load_duration",
        "prompt_eval_duration",
        "eval_duration",
        "prompt_eval_count",
        "eval_count",
    ):
        if isinstance(response, Mapping):
            value = response.get(field)
        else:
            value = getattr(response, field, None)
        if isinstance(value, Real) and not isinstance(value, bool):
            metrics[field] = int(value)


def _log_chat_metrics(metrics: Mapping[str, Any]) -> None:
    LOGGER.info(
        "gaon_llm_metrics %s",
        json.dumps(dict(metrics), ensure_ascii=False, sort_keys=True),
    )


def _llm_timeout_seconds() -> float:
    raw_value = os.getenv("GAON_LLM_TIMEOUT_SECONDS")
    if raw_value is None:
        return DEFAULT_LLM_TIMEOUT_SECONDS
    try:
        timeout = float(raw_value)
    except (TypeError, ValueError):
        return DEFAULT_LLM_TIMEOUT_SECONDS
    if not math.isfinite(timeout) or timeout <= 0:
        return DEFAULT_LLM_TIMEOUT_SECONDS
    return timeout


def _create_ollama_client() -> Any:
    try:
        from ollama import Client
    except ModuleNotFoundError as exc:
        raise OllamaModelError(
            "Ollama Python 패키지를 사용할 수 없습니다."
        ) from exc

    try:
        return Client(timeout=_llm_timeout_seconds())
    except Exception as exc:
        raise OllamaModelError(
            "Ollama 클라이언트를 초기화할 수 없습니다."
        ) from exc


def _raise_ollama_error(exc: Exception) -> None:
    try:
        import httpx
    except ModuleNotFoundError:
        httpx = None  # type: ignore[assignment]

    if isinstance(exc, TimeoutError) or (
        httpx is not None and isinstance(exc, httpx.TimeoutException)
    ):
        raise OllamaTimeoutError(
            "Ollama 응답 시간이 초과되었습니다."
        ) from exc
    if isinstance(exc, ConnectionError) or (
        httpx is not None and isinstance(exc, httpx.ConnectError)
    ):
        raise OllamaConnectionError(
            "Ollama 서버에 연결할 수 없습니다."
        ) from exc

    ollama_errors: tuple[type[Exception], ...] = ()
    try:
        from ollama import RequestError, ResponseError
    except ModuleNotFoundError:
        pass
    else:
        ollama_errors = (RequestError, ResponseError)

    if ollama_errors and isinstance(exc, ollama_errors):
        raise OllamaModelError(
            f"Ollama 모델 {MODEL_NAME} 요청을 처리할 수 없습니다."
        ) from exc
    if httpx is not None and isinstance(exc, httpx.RequestError):
        raise OllamaModelError(
            f"Ollama 모델 {MODEL_NAME} 요청을 처리할 수 없습니다."
        ) from exc
    raise OllamaModelError(
        f"Ollama 모델 {MODEL_NAME} 요청을 처리할 수 없습니다."
    ) from exc


def _ollama_chat(
    client: Any,
    messages: list[Any],
) -> Any:
    try:
        return client.chat(
            model=MODEL_NAME,
            messages=messages,
            tools=TOOL_SCHEMAS,
            think=True,
            options={
                "temperature": 0,
                "num_predict": ROUTER_NUM_PREDICT,
            },
            keep_alive=MODEL_KEEP_ALIVE,
        )
    except ChatServiceError:
        raise
    except Exception as exc:
        _raise_ollama_error(exc)
        raise AssertionError("unreachable")


def _response_message(response: Any) -> Any:
    if isinstance(response, Mapping):
        message = response.get("message")
    else:
        message = getattr(response, "message", None)
    if message is None:
        raise ChatProtocolError("Qwen 응답에 assistant 메시지가 없습니다.")
    return message


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
        raise ChatProtocolError("Qwen이 잘못된 도구 호출 형식을 반환했습니다.")

    if isinstance(function, Mapping):
        name = function.get("name")
        raw_arguments = function.get("arguments", {})
    else:
        name = getattr(function, "name", None)
        raw_arguments = getattr(function, "arguments", {})

    if not isinstance(name, str) or not name:
        raise ChatProtocolError("Qwen의 도구 호출에 도구명이 없습니다.")

    if isinstance(raw_arguments, str):
        try:
            decoded_arguments = json.loads(raw_arguments)
        except json.JSONDecodeError as exc:
            raise ChatProtocolError(
                "Qwen의 도구 인자가 올바른 JSON이 아닙니다."
            ) from exc
        if not isinstance(decoded_arguments, Mapping):
            raise ChatProtocolError("Qwen의 도구 인자가 객체 형식이 아닙니다.")
        arguments = dict(decoded_arguments)
    elif isinstance(raw_arguments, Mapping):
        arguments = dict(raw_arguments)
    else:
        raise ChatProtocolError("Qwen의 도구 인자가 객체 형식이 아닙니다.")

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
            numbers.extend(_collect_tool_numbers(item, field_name=str(key)))
        return numbers
    if isinstance(value, (list, tuple)):
        numbers = []
        for item in value:
            numbers.extend(_collect_tool_numbers(item))
        return numbers
    if isinstance(value, str):
        numbers = []
        for match in _NUMBER_PATTERN.finditer(value):
            normalized = (
                match.group("number").replace("−", "-").replace(",", "")
            )
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
    if tool_name == "get_grid_data":
        allowed: list[float] = []
        raw_fields = tool_result.get("requested_fields")
        values = tool_result.get("values")
        requested_fields = (
            [str(field) for field in raw_fields]
            if isinstance(raw_fields, list)
            else []
        )
        if isinstance(values, Mapping):
            for field in requested_fields:
                value = values.get(field)
                if not isinstance(value, Real) or isinstance(value, bool):
                    continue
                number = float(value)
                if not math.isfinite(number):
                    continue
                allowed.append(number)
                if GRID_FIELD_SPECS.get(field, {}).get("is_ratio") is True:
                    allowed.append(number * 100)

        if requested_fields:
            allowed.append(float(len(requested_fields)))
        for field in requested_fields:
            spec = GRID_FIELD_SPECS.get(field)
            if spec is None:
                continue
            for source in (field, str(spec["label"])):
                allowed.extend(_collect_tool_numbers(source))
        return allowed

    allowed = _collect_tool_numbers(tool_result)
    if tool_name == "run_simulation":
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
    normalized = token.replace("−", "-").replace(",", "")
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
        raise ChatProtocolError(
            "최종 답변에 도구 결과에서 유도할 수 없는 숫자가 있습니다: "
            + ", ".join(unsupported)
        )


def _validate_no_reasoning_trace(answer: str) -> None:
    stripped_answer = answer.strip()
    lowered_answer = answer.lower()
    if "<think>" in lowered_answer or "</think>" in lowered_answer:
        raise ChatProtocolError("최종 답변에 내부 추론 태그가 포함되었습니다.")
    if "```" in answer:
        raise ChatProtocolError("최종 답변이 코드 블록으로 감싸졌습니다.")
    if (
        (stripped_answer.startswith("{") and stripped_answer.endswith("}"))
        or (stripped_answer.startswith("[") and stripped_answer.endswith("]"))
    ):
        raise ChatProtocolError("최종 답변이 JSON 객체 또는 배열 형태입니다.")
    try:
        json.loads(stripped_answer)
    except (json.JSONDecodeError, TypeError):
        pass
    else:
        raise ChatProtocolError("최종 답변이 자연어가 아닌 JSON 값입니다.")
    if re.search(
        r"""(?ix)(?:["'])?(?:thinking|content)(?:["'])?\s*:""",
        answer,
    ):
        raise ChatProtocolError("최종 답변에 내부 응답 키가 포함되었습니다.")
    if any(pattern.search(answer) for pattern in _REASONING_PATTERNS):
        raise ChatProtocolError("최종 답변에 내부 추론 과정이 포함되었습니다.")


def _field_term_match(answer: str, field: str) -> re.Match[str] | None:
    spec = GRID_FIELD_SPECS[field]
    terms = dict.fromkeys(
        [
            str(spec["label"]),
            field,
            *(str(alias) for alias in spec["aliases"]),
        ]
    )
    for term in sorted(terms, key=lambda item: len(_compact_lookup_text(item)), reverse=True):
        compact_term = _compact_lookup_text(term)
        pattern = r"\s*".join(re.escape(character) for character in compact_term)
        match = re.search(pattern, answer, re.IGNORECASE)
        if match is not None:
            return match
    return None


def _validate_grid_answer(
    answer: str,
    tool_result: Mapping[str, Any],
) -> None:
    if re.search(r"(?<!\d)100\s*m\s*격자", answer, re.IGNORECASE):
        raise ChatProtocolError(
            "조회 최종 답변에 도구 결과에 없는 100m 격자 수치가 포함되었습니다."
        )
    for field in ("grid_id", "gu_name"):
        value = str(tool_result.get(field) or "")
        if not value or value not in answer:
            raise ChatProtocolError(
                f"조회 최종 답변에 {field}가 누락되었습니다."
            )

    raw_requested_fields = tool_result.get("requested_fields")
    values = tool_result.get("values")
    if not isinstance(raw_requested_fields, list) or not raw_requested_fields:
        raise ChatProtocolError("조회 도구 결과에 requested_fields가 없습니다.")
    if not isinstance(values, Mapping):
        raise ChatProtocolError("조회 도구 결과에 values 객체가 없습니다.")

    requested_fields = [str(field) for field in raw_requested_fields]
    if (
        len(set(requested_fields)) != len(requested_fields)
        or any(field not in GRID_FIELD_SPECS for field in requested_fields)
    ):
        raise ChatProtocolError("조회 도구 결과의 requested_fields가 올바르지 않습니다.")
    if set(values) != set(requested_fields):
        raise ChatProtocolError(
            "조회 도구 결과의 values가 requested_fields와 일치하지 않습니다."
        )

    for field in requested_fields:
        spec = GRID_FIELD_SPECS[field]
        label = str(spec["label"])
        field_term_match = _field_term_match(answer, field)
        if field_term_match is None:
            raise ChatProtocolError(
                f"조회 최종 답변에 요청 지표의 표시명 또는 등록 별칭이 "
                f"누락되었습니다: {label}"
            )

        raw_value = values[field]
        if (
            not isinstance(raw_value, Real)
            or isinstance(raw_value, bool)
            or not math.isfinite(float(raw_value))
        ):
            raise ChatProtocolError(
                f"조회 도구 결과의 {field} 값이 유한 숫자가 아닙니다."
            )
        value = float(raw_value)
        fragment = answer[field_term_match.start() : field_term_match.start() + 180]
        if spec["is_ratio"]:
            display_pattern = _formatted_number_pattern(value * 100, decimals=2)
            if re.search(display_pattern + r"\s*%", fragment) is None:
                raise ChatProtocolError(
                    f"조회 최종 답변의 {label} 퍼센트 값이 누락되거나 다릅니다."
                )
            continue

        unit = str(spec["unit"])
        matched_value = False
        for match in _NUMBER_PATTERN.finditer(fragment):
            if not _matches_allowed_number(match.group("number"), [value]):
                continue
            suffix = fragment[match.end() :]
            if unit and re.match(rf"\s*{re.escape(unit)}", suffix) is None:
                continue
            matched_value = True
            break
        if not matched_value:
            unit_description = f" {unit}" if unit else ""
            raise ChatProtocolError(
                f"조회 최종 답변의 {label}{unit_description} 값이 "
                "누락되거나 다릅니다."
            )

    unrequested_labels = [
        str(GRID_FIELD_SPECS[field]["label"])
        for field in ALLOWED_GRID_FIELDS
        if field not in requested_fields
        and (
            str(GRID_FIELD_SPECS[field]["label"]) in answer
            or re.search(rf"(?<!\w){re.escape(field)}(?!\w)", answer)
        )
    ]
    if unrequested_labels:
        raise ChatProtocolError(
            "조회 최종 답변에 요청하지 않은 지표가 포함되었습니다: "
            + ", ".join(unrequested_labels)
        )
    _validate_supported_numbers(answer, "get_grid_data", tool_result)


def _validate_simulation_answer(
    answer: str,
    tool_result: Mapping[str, Any],
) -> None:
    for field in ("grid_id", "gu_name"):
        value = str(tool_result[field])
        if value not in answer:
            raise ChatProtocolError(
                f"시뮬레이션 최종 답변에 {field}가 누락되었습니다."
            )

    before = float(tool_result["before_anomaly"])
    after = float(tool_result["after_anomaly"])
    delta = float(tool_result["delta_c"])
    if not _contains_formatted_number(answer, before):
        raise ChatProtocolError(
            "최종 답변에 before_anomaly 값이 누락되거나 다릅니다."
        )
    if not _contains_formatted_number(answer, after):
        raise ChatProtocolError(
            "최종 답변에 after_anomaly 값이 누락되거나 다릅니다."
        )
    if not _contains_formatted_number(answer, delta, unit="℃"):
        raise ChatProtocolError(
            "최종 답변에 delta_c 값이 누락되거나 다릅니다."
        )

    delta_pattern = _formatted_number_pattern(delta)
    direction_match = re.search(
        rf"(?:delta_c|예상\s*변화량).{{0,100}}?"
        rf"{delta_pattern}\s*℃.{{0,30}}?(증가|감소|변화가\s*없)",
        answer,
        re.DOTALL,
    )
    if direction_match is None:
        raise ChatProtocolError(
            "delta_c와 방향 설명을 함께 확인할 수 없습니다."
        )
    direction = direction_match.group(1)
    if delta > 0 and direction != "증가":
        raise ChatProtocolError("양수 delta_c를 증가로 설명하지 않았습니다.")
    if delta < 0 and direction != "감소":
        raise ChatProtocolError("음수 delta_c를 감소로 설명하지 않았습니다.")
    if delta == 0 and not direction.startswith("변화가"):
        raise ChatProtocolError("0인 delta_c를 변화 없음으로 설명하지 않았습니다.")

    has_anomaly_field_names = (
        "before_anomaly" in answer and "after_anomaly" in answer
    )
    has_equivalent_anomaly_explanation = re.search(
        r"(?:"
        r"모델.{0,20}예측.{0,20}anomaly.{0,80}절대\s*온도"
        r".{0,24}(?:아니|아님|않|별개|구분)"
        r"|"
        r"절대\s*온도.{0,40}(?:아니|아님|않|별개|구분)"
        r".{0,80}모델.{0,20}예측.{0,20}anomaly"
        r")",
        answer,
        re.DOTALL | re.IGNORECASE,
    )
    if not has_anomaly_field_names and has_equivalent_anomaly_explanation is None:
        raise ChatProtocolError(
            "before/after anomaly가 모델 예측값이라는 의미 설명이 누락되었습니다."
        )
    if re.search(
        r"절대\s*온도.{0,24}(?:아니|아님|않|별개|구분)",
        answer,
        re.DOTALL,
    ) is None:
        raise ChatProtocolError(
            "before_anomaly와 after_anomaly가 절대온도가 아니라는 설명이 "
            "누락되었습니다."
        )
    if re.search(
        r"(?:기존|변경\s*후)\s*(?:실제\s*)?온도",
        answer,
    ):
        raise ChatProtocolError(
            "anomaly를 기존 또는 변경 후 실제 온도로 잘못 표현했습니다."
        )

    if "delta_c" not in answer or "모델 기준 예상 변화량" not in answer:
        raise ChatProtocolError(
            "delta_c의 모델 기준 예상 변화량 설명이 누락되었습니다."
        )
    if re.search(
        r"인과\s*효과.{0,40}(?:단정할 수 없|단정하지 않|아니|보장하지 않)",
        answer,
        re.DOTALL,
    ) is None:
        raise ChatProtocolError(
            "실제 정책의 인과효과로 단정할 수 없다는 한계가 누락되었습니다."
        )
    for term in ("비용", "토지", "공사기간", "행정 가능성"):
        if term not in answer:
            raise ChatProtocolError(f"필수 모델 한계가 누락되었습니다: {term}")
    if re.search(
        r"반영.{0,20}(?:않|안|되지|못|제외|미반영)",
        answer,
        re.DOTALL,
    ) is None:
        raise ChatProtocolError(
            "비용 등 현실 조건이 반영되지 않았다는 설명이 누락되었습니다."
        )

    policy_notes = list(tool_result.get("policy_direction_notes") or [])
    for note in policy_notes:
        if str(note) not in answer:
            raise ChatProtocolError(
                "일반적인 저감 정책과 반대 방향이라는 설명이 누락되었습니다."
            )

    warnings = list(tool_result.get("warnings") or [])
    for warning in warnings:
        if str(warning) not in answer:
            raise ChatProtocolError(
                "predict_core가 반환한 경고가 최종 답변에서 누락되었습니다."
            )
    if warnings and not (
        "학습 범위" in answer
        and ("보정" in answer or "clip" in answer)
        and ("applied_changes" in answer or "실제 반영값" in answer)
    ):
        raise ChatProtocolError(
            "학습 범위 보정과 실제 적용값에 대한 설명이 누락되었습니다."
        )

    _validate_supported_numbers(answer, "run_simulation", tool_result)


def _tool_error_answer(tool_result: Mapping[str, Any]) -> str | None:
    if tool_result.get("success") is True:
        return None
    error = tool_result.get("error")
    if not isinstance(error, str) or not error.strip():
        raise ChatProtocolError("도구 오류 결과에 error 문자열이 없습니다.")
    return error.strip()


def format_grid_data_answer(tool_result: Mapping[str, Any]) -> str:
    """조회 Tool 결과만 사용해 결정적인 한국어 답변을 만든다."""

    error_answer = _tool_error_answer(tool_result)
    if error_answer is not None:
        return error_answer

    grid_id = tool_result.get("grid_id")
    gu_name = tool_result.get("gu_name")
    requested_fields = tool_result.get("requested_fields")
    values = tool_result.get("values")
    if (
        not isinstance(grid_id, str)
        or not grid_id
        or not isinstance(gu_name, str)
        or not gu_name
        or not isinstance(requested_fields, list)
        or not requested_fields
        or not isinstance(values, Mapping)
    ):
        raise ChatProtocolError("조회 Tool 결과의 필수 필드가 올바르지 않습니다.")

    lines = [f"{grid_id} 격자({gu_name})의 현재 데이터입니다."]
    for raw_field in requested_fields:
        if not isinstance(raw_field, str) or raw_field not in GRID_FIELD_SPECS:
            raise ChatProtocolError("조회 Tool 결과에 지원하지 않는 필드가 있습니다.")
        raw_value = values.get(raw_field)
        if (
            not isinstance(raw_value, Real)
            or isinstance(raw_value, bool)
            or not math.isfinite(float(raw_value))
        ):
            raise ChatProtocolError(
                f"조회 Tool 결과의 {raw_field} 값이 유한 숫자가 아닙니다."
            )
        lines.append(
            f"- {GRID_FIELD_SPECS[raw_field]['label']}: "
            f"{format_grid_field_value(raw_field, float(raw_value))}"
        )
    return "\n".join(lines)


def format_simulation_answer(tool_result: Mapping[str, Any]) -> str:
    """시뮬레이션 Tool 결과만 사용해 결정적인 한국어 답변을 만든다."""

    error_answer = _tool_error_answer(tool_result)
    if error_answer is not None:
        return error_answer

    grid_id = tool_result.get("grid_id")
    gu_name = tool_result.get("gu_name")
    if (
        not isinstance(grid_id, str)
        or not grid_id
        or not isinstance(gu_name, str)
        or not gu_name
    ):
        raise ChatProtocolError("시뮬레이션 Tool 결과의 지역 정보가 올바르지 않습니다.")

    numeric_values: dict[str, float] = {}
    for field in ("before_anomaly", "after_anomaly", "delta_c"):
        raw_value = tool_result.get(field)
        if (
            not isinstance(raw_value, Real)
            or isinstance(raw_value, bool)
            or not math.isfinite(float(raw_value))
        ):
            raise ChatProtocolError(
                f"시뮬레이션 Tool 결과의 {field}가 유한 숫자가 아닙니다."
            )
        numeric_values[field] = float(raw_value)

    before = numeric_values["before_anomaly"]
    after = numeric_values["after_anomaly"]
    delta = numeric_values["delta_c"]
    display_delta = 0.0 if math.isclose(delta, 0.0, abs_tol=0.0005) else delta
    if delta > 0:
        direction = "증가"
    elif delta < 0:
        direction = "감소"
    else:
        direction = "변화가 없는"

    lines = [
        (
            f"{grid_id} 격자({gu_name})의 모델 예측 anomaly는 "
            f"{before:.3f}에서 {after:.3f}로 변했습니다."
        ),
        (
            "모델 기준 예상 변화량(delta_c)은 "
            f"{display_delta:.3f}℃로 {direction} 방향입니다."
        ),
        (
            "before_anomaly와 after_anomaly는 절대온도가 아니라 모델 예측 "
            "anomaly이며, delta_c는 두 모델 예측의 차이인 모델 기준 "
            "예상 변화량입니다."
        ),
    ]

    policy_notes = tool_result.get("policy_direction_notes") or []
    warnings = tool_result.get("warnings") or []
    limitations = tool_result.get("limitations") or []
    if not all(isinstance(items, list) for items in (policy_notes, warnings, limitations)):
        raise ChatProtocolError("시뮬레이션 Tool 결과의 안내 목록이 올바르지 않습니다.")

    lines.extend(str(note) for note in policy_notes)
    lines.extend(f"경고: {warning}" for warning in warnings)
    if warnings:
        lines.append(
            "학습 범위 밖 입력은 내부적으로 보정되었으며, 입력값 그대로가 "
            "아니라 적용 결과에 표시된 값이 실제 반영값입니다."
        )
    lines.extend(str(limitation) for limitation in limitations)
    return "\n".join(lines)


def _format_tool_answer(
    tool_name: str,
    tool_result: Mapping[str, Any],
) -> str:
    if tool_name == "get_grid_data":
        return format_grid_data_answer(tool_result)
    if tool_name == "run_simulation":
        return format_simulation_answer(tool_result)
    raise ChatProtocolError(f"답변 formatter를 지원하지 않는 도구입니다: {tool_name}")


def _validate_final_answer(
    answer: str,
    tool_name: str,
    tool_result: Mapping[str, Any],
) -> None:
    _validate_no_reasoning_trace(answer)
    if tool_result.get("success") is not True:
        error = tool_result.get("error")
        if not isinstance(error, str) or answer.strip() != error.strip():
            raise ChatProtocolError(
                "Qwen의 오류 답변은 도구가 반환한 error 문자열만 포함해야 합니다."
            )
        return

    if tool_name == "get_grid_data":
        _validate_grid_answer(answer, tool_result)
    elif tool_name == "run_simulation":
        _validate_simulation_answer(answer, tool_result)
    else:
        raise ChatProtocolError(
            f"최종 답변 검증을 지원하지 않는 도구입니다: {tool_name}"
        )


def _resolved_grid_id(message: str, selected_grid_id: str | None) -> tuple[str, str]:
    if not isinstance(message, str) or not message.strip():
        raise ChatInputError("질문을 입력해 주세요.")
    normalized_message = message.strip()

    explicit_ids = list(dict.fromkeys(_GRID_ID_PATTERN.findall(normalized_message)))
    if len(explicit_ids) > 1:
        raise ChatInputError(
            "한 번에 하나의 grid_id만 질문할 수 있습니다."
        )
    if explicit_ids:
        return normalized_message, explicit_ids[0]

    if selected_grid_id is not None and not isinstance(selected_grid_id, str):
        raise ChatInputError("selected_grid_id는 문자열이어야 합니다.")
    normalized_selected_grid_id = (
        selected_grid_id.strip() if isinstance(selected_grid_id, str) else ""
    )
    if not normalized_selected_grid_id:
        raise ChatInputError(
            "질문에 grid_id를 입력하거나 지도에서 100m 격자를 선택해 주세요."
        )
    return normalized_message, normalized_selected_grid_id


def _is_unsupported_scope(message: str) -> bool:
    return any(pattern.search(message) for pattern in _UNSUPPORTED_SCOPE_PATTERNS)


def _supported_scope_result(
    *,
    answer: str = SUPPORTED_SCOPE_ANSWER,
    first_thinking: str = "",
    first_content: str = "",
    metrics: dict[str, Any] | None = None,
) -> ChatResult:
    result_metrics = metrics if metrics is not None else _new_chat_metrics()
    return ChatResult(
        answer=answer,
        used_tools=[],
        tool_data={},
        warnings=[],
        limitations=[],
        tool_arguments={},
        first_thinking=first_thinking,
        first_content=first_content,
        final_thinking="",
        final_content=answer,
        metrics=result_metrics,
    )


def _run_chat_with_client(
    client: Any,
    message: str,
    resolved_grid_id: str,
    metrics: dict[str, Any],
) -> ChatResult:
    recognized_lookup_fields = _recognized_lookup_fields(message)
    lookup_field_hint = ""
    if _EXPLICIT_SIMULATION_PATTERN.search(message):
        lookup_field_hint = (
            " 이 요청은 정책 변경 시뮬레이션이다. run_simulation을 즉시 "
            "호출하고 질문에서 변경값의 부호와 크기만 추출한다."
        )
    elif recognized_lookup_fields:
        lookup_field_hint = (
            " 이 요청은 현재 데이터 조회다. get_grid_data를 즉시 호출하며 "
            "fields는 정확히 "
            f"{json.dumps(recognized_lookup_fields, ensure_ascii=False)}로 전달한다."
        )
    request_prompt = (
        f"{SYSTEM_PROMPT}\n"
        f"사용할 grid_id는 {resolved_grid_id}이다. "
        "도구 인자에서 이 값을 글자 하나도 변경하거나 생략하지 않는다."
        f"{lookup_field_hint}"
    )
    messages: list[Any] = [
        {"role": "system", "content": request_prompt},
        {"role": "user", "content": message},
    ]

    router_started = time.perf_counter()
    metrics["ollama_call_count"] += 1
    try:
        first_response = _ollama_chat(client, messages)
    finally:
        metrics["llm_router_seconds"] = round(
            time.perf_counter() - router_started,
            6,
        )
    _capture_ollama_metrics(metrics, first_response)
    first_message = _response_message(first_response)
    first_thinking = _message_thinking(first_message)
    first_content = _message_content(first_message)

    calls = _tool_calls(first_message)
    if not calls:
        return _supported_scope_result(
            first_thinking=first_thinking,
            first_content=first_content,
            metrics=metrics,
        )
    if len(calls) != 1:
        raise ChatProtocolError("Qwen은 도구를 정확히 한 번 호출해야 합니다.")

    tool_name, tool_arguments = _tool_name_and_arguments(calls[0])
    if tool_name not in TOOL_FUNCTIONS:
        raise ChatProtocolError("Qwen이 허용되지 않은 도구를 호출했습니다.")
    if tool_arguments.get("grid_id") != resolved_grid_id:
        raise ChatProtocolError(
            "Qwen이 확정된 grid_id와 다른 값을 도구에 전달했습니다."
        )
    if tool_name == "get_grid_data" and recognized_lookup_fields:
        raw_fields = tool_arguments.get("fields")
        if raw_fields is None:
            actual_fields = list(DEFAULT_GRID_FIELDS)
        elif isinstance(raw_fields, list) and all(
            isinstance(field, str) for field in raw_fields
        ):
            actual_fields = list(dict.fromkeys(raw_fields))
        else:
            actual_fields = []
        if actual_fields != recognized_lookup_fields:
            raise ChatProtocolError(
                "Qwen이 사용자가 요청한 조회 fields를 정확히 전달하지 않았습니다."
            )

    metrics["tool_name"] = tool_name
    tool_function = TOOL_FUNCTIONS[tool_name]
    tool_started = time.perf_counter()
    try:
        raw_tool_result = tool_function(**tool_arguments)
    except TypeError:
        raw_tool_result = {
            "success": False,
            "grid_id": tool_arguments.get("grid_id"),
            "error": "도구 인자가 올바르지 않습니다.",
        }
    except Exception as exc:
        raise ChatProtocolError("도구를 안전하게 실행할 수 없습니다.") from exc
    finally:
        metrics["tool_execution_seconds"] = round(
            time.perf_counter() - tool_started,
            6,
        )
    if not isinstance(raw_tool_result, Mapping):
        raise ChatProtocolError("도구가 올바른 객체를 반환하지 않았습니다.")
    tool_result = dict(raw_tool_result)

    formatter_started = time.perf_counter()
    try:
        final_content = _format_tool_answer(tool_name, tool_result)
    finally:
        metrics["answer_format_seconds"] = round(
            time.perf_counter() - formatter_started,
            6,
        )
    raw_warnings = tool_result.get("warnings")
    warnings = (
        [str(item) for item in raw_warnings]
        if isinstance(raw_warnings, list)
        else []
    )
    raw_limitations = tool_result.get("limitations")
    limitations = (
        [str(item) for item in raw_limitations]
        if isinstance(raw_limitations, list)
        else []
    )
    result = ChatResult(
        answer=final_content,
        used_tools=[tool_name],
        tool_data=tool_result,
        warnings=warnings,
        limitations=limitations,
        tool_arguments=tool_arguments,
        first_thinking=first_thinking,
        first_content=first_content,
        final_thinking="",
        final_content=final_content,
        metrics=metrics,
    )
    validation_started = time.perf_counter()
    try:
        _validate_final_answer(final_content, tool_name, tool_result)
    except ChatProtocolError as exc:
        exc.result = result
        raise
    finally:
        metrics["validation_seconds"] = round(
            time.perf_counter() - validation_started,
            6,
        )
    return result


def run_chat(
    message: str,
    selected_grid_id: str | None = None,
    client: Any | None = None,
) -> ChatResult:
    """검증된 GA:ON Tool Calling 결과를 반환한다.

    질문에 명시된 격자 ID가 지도 선택 문맥보다 우선한다. 내부에서 만든
    Ollama 클라이언트만 이 함수가 닫으며, 테스트 등에서 주입한 클라이언트는
    호출자가 관리한다.
    """

    metrics = _new_chat_metrics()
    chat_started = time.perf_counter()
    try:
        normalized_message, resolved_grid_id = _resolved_grid_id(
            message,
            selected_grid_id,
        )
        if _AMBIGUOUS_RATIO_CHANGE_PATTERN.search(normalized_message):
            result = _supported_scope_result(
                answer=AMBIGUOUS_RATIO_CHANGE_ANSWER,
                metrics=metrics,
            )
        elif _is_unsupported_scope(normalized_message):
            result = _supported_scope_result(metrics=metrics)
        elif client is not None:
            result = _run_chat_with_client(
                client,
                normalized_message,
                resolved_grid_id,
                metrics,
            )
        else:
            ollama_client = _create_ollama_client()
            with ollama_client as managed_client:
                result = _run_chat_with_client(
                    managed_client,
                    normalized_message,
                    resolved_grid_id,
                    metrics,
                )
        metrics["status"] = "ok"
        return result
    except ChatServiceError:
        raise
    except Exception as exc:
        _raise_ollama_error(exc)
        raise AssertionError("unreachable")
    finally:
        metrics["chat_total_seconds"] = round(
            time.perf_counter() - chat_started,
            6,
        )
        _log_chat_metrics(metrics)
