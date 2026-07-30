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
    GRID_FIELD_SPECS,
    TOOL_FUNCTIONS,
    format_grid_field_value,
)


MODEL_NAME = "qwen3:4b"
DEFAULT_LLM_TIMEOUT_SECONDS = 120.0
ROUTER_NUM_PREDICT = 768
MODEL_KEEP_ALIVE = "5m"
LOGGER = logging.getLogger("uvicorn.error")
_GRID_FIELD_LABELS = "、".join(
    str(GRID_FIELD_SPECS[field]["label"]) for field in ALLOWED_GRID_FIELDS
)
_SIMULATION_FIELDS = (
    "green_ratio",
    "impervious_ratio",
    "park_area_within_500m",
)
_SIMULATION_ARGUMENT_BY_FIELD = {
    "green_ratio": "green_ratio_delta",
    "impervious_ratio": "impervious_ratio_delta",
    "park_area_within_500m": "park_area_delta",
}
_SIMULATION_FIELD_TERMS = {
    "green_ratio": ("녹지",),
    "impervious_ratio": ("불투수",),
    "park_area_within_500m": ("공원",),
}
_SIMULATION_ARGUMENTS = tuple(_SIMULATION_ARGUMENT_BY_FIELD.values())
_SUPPORTED_INTENTS = {
    "lookup",
    "simulation",
    "unsupported",
}
_SUPPORTED_OPERATIONS = {"increase", "decrease"}
_SUPPORTED_UNITS = {"percent", "percentage_point", "m2"}
_SUPPORTED_BASES = {"direct", "relative_to_current"}
_FULL_SCOPE_TERMS = ("전체", "모두", "모든", "전부")
_EXCLUDED_SCOPE_TERMS = ("말고", "제외", "필요없", "보여주지말고")
_STANDALONE_ALL_PATTERN = re.compile(
    r"(?<![가-힣A-Za-z0-9_])다(?![가-힣A-Za-z0-9_])"
)
_UNRESOLVED_CODES = {
    "ambiguous_request",
    "change_field",
    "change_operation",
    "change_source",
    "change_unit",
    "change_value",
    "conflicting_changes",
    "lookup_field",
    "park_area_decrease",
}
_CLARIFICATION_BY_CODE = {
    "ambiguous_request": "요청에서 확정되지 않은 부분을 구체적으로 알려주세요.",
    "change_field": "변경할 지표를 알려주세요.",
    "change_operation": "증가 또는 감소 방향을 알려주세요.",
    "change_source": "변경 요청의 원문 근거를 다시 확인해 주세요.",
    "change_unit": "비율은 % 또는 %p, 공원 면적은 ㎡ 단위로 알려주세요.",
    "change_value": "변경량을 숫자로 알려주세요.",
    "conflicting_changes": "같은 지표의 변경값은 하나로 확정해 주세요.",
    "lookup_field": "조회할 격자 지표를 알려주세요.",
    "park_area_decrease": "공원 면적 감소는 지원하지 않습니다.",
}
SUPPORTED_SCOPE_ANSWER = (
    "현재 GA:ON AI는 선택한 100m 격자의 다음 현재 데이터를 조회할 수 있습니다: "
    f"{_GRID_FIELD_LABELS}. "
    "사용자가 지정한 녹지율·불투수율·공원 면적 변경 시나리오만 지원합니다. "
    "정책 추천, 모델 설명, 문서 검색은 현재 지원하지 않습니다."
)
ROUTER_OUTPUT_SCHEMA = {
    "type": "object",
    "properties": {
        "intent": {
            "type": "string",
            "enum": ["unsupported", "lookup", "simulation"],
            "description": (
                "허용된 격자 지표의 현재값 조회와 전체 조회는 lookup, "
                "지원 정책 변경은 simulation, 그 밖의 요청은 unsupported"
            ),
        },
        "lookup_all": {
            "type": "boolean",
            "description": (
                "전체 격자 데이터 조회면 true. 일부 필드 조회 또는 전체 "
                "범위를 부정한 요청이면 false"
            ),
        },
        "requested_fields": {
            "type": "array",
            "items": {
                "type": "string",
                "enum": list(ALLOWED_GRID_FIELDS),
            },
            "description": (
                "일부 조회에서 사용자가 요청한 지표만 순서대로 작성한다. "
                "lookup_all=true, simulation, unsupported에서는 빈 배열이다."
            ),
        },
        "excluded_scope": {
            "type": "boolean",
            "description": (
                "전체·모두·모든·전부·다 같은 전체 범위 표현을 사용자가 "
                "명시적으로 부정하거나 제외했으면 true"
            ),
        },
        "changes": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "field": {
                        "type": "string",
                        "enum": list(_SIMULATION_FIELDS),
                        "description": (
                            "green_ratio=녹지 비율, "
                            "impervious_ratio=불투수 비율, "
                            "park_area_within_500m=공원 면적"
                        ),
                    },
                    "operation": {
                        "type": "string",
                        "enum": sorted(_SUPPORTED_OPERATIONS),
                    },
                    "value": {
                        "type": "number",
                        "exclusiveMinimum": 0,
                        "description": (
                            "사용자 원문에 표시된 수치 자체. %를 0~1로 "
                            "미리 환산하지 않는다."
                        ),
                    },
                    "unit": {
                        "type": "string",
                        "enum": sorted(_SUPPORTED_UNITS),
                    },
                    "basis": {
                        "type": "string",
                        "enum": sorted(_SUPPORTED_BASES),
                    },
                    "source_text": {"type": "string"},
                    "value_text": {
                        "type": "string",
                        "description": (
                            "변경량 숫자와 단위가 실제로 나타난 원문의 가장 "
                            "짧은 구간"
                        ),
                    },
                },
                "required": [
                    "field",
                    "operation",
                    "value",
                    "unit",
                    "basis",
                    "source_text",
                    "value_text",
                ],
                "additionalProperties": False,
            },
        },
        "assumptions": {
            "type": "array",
            "items": {"type": "string"},
        },
        "unresolved": {
            "type": "array",
            "items": {
                "type": "string",
                "enum": sorted(_UNRESOLVED_CODES),
            },
            "description": (
                "추측할 수 없는 항목의 코드만 작성한다. 자유 문장이나 내부 "
                "추론은 작성하지 않는다."
            ),
        },
    },
    "required": [
        "intent",
        "lookup_all",
        "requested_fields",
        "excluded_scope",
        "changes",
        "assumptions",
        "unresolved",
    ],
    "additionalProperties": False,
}

SYSTEM_PROMPT = """사용자 요청의 의미를 JSON schema로만 정규화한다.
답변 문장이나 실제 실행 인자를 만들지 않는다.
- 먼저 지원 범위를 판정한다. 요청 지표가 허용된 requested_fields 또는 지원 변경 field와 대응하지 않으면 다른 지표로 대체하지 말고 unsupported다.
- simulation이면 lookup_all=false, requested_fields=[], excluded_scope=false로 두고 각 변경을 changes에 하나씩 둔다.
- 녹지·녹지 비중은 green_ratio, 불투수·불투수 비중은 impervious_ratio, 공원 면적은 park_area_within_500m이다.
- 일반 percent 또는 비율 문맥의 단위 없는 수치는 증감 방향이 명확하면 unit=percent, basis=direct로 두고 해당 해석을 assumptions에 한국어로 쓴다.
- 명시적 %p는 percentage_point/direct, 현재 값의 일정 비율은 percent/relative_to_current, 공원 면적은 m2/direct다.
- value는 원문 표면 수치를 그대로 쓴다. 예를 들어 5%와 5%p는 value=5이며 0.05로 환산하지 않는다.
- 공원과 제곱미터 의미의 수치가 포함된 면적 변경은 simulation이다.
- direct 변경에는 현재 값이 필요하지 않다.
- 문맥상 명확한 오타는 assumptions에 남기고 정규화한다.
- assumptions는 원문을 실제로 다르게 정규화하거나 단위를 가정한 경우에만 쓰며 명확한 현재값 조회에는 비운다.
- 지시 대상·방향·수치 중 필요한 의미가 불명확할 때는 changes에 추측하지 않는다.
- unresolved에는 자유 문장 대신 change_field, change_operation, change_value, change_unit, ambiguous_request 중 필요한 코드만 쓴다.
- 각 source_text는 지표·방향·변경량이 나타난 원문 구간이고, value_text는 변경량 숫자와 단위가 나타난 가장 짧은 원문 구간이다.
- 현재값 조회를 명시적으로 묻는 경우만 intent=lookup이다. 일부 조회는 lookup_all=false와 요청한 requested_fields를 사용한다.
- 전체·모두·모든·전부 또는 독립된 "다"로 격자의 전체 데이터나 정보를 요구하면 intent=lookup, lookup_all=true, requested_fields=[]다.
- 전체 범위 표현 뒤에 말고·제외·필요 없고·보여주지 말고처럼 전체 조회를 부정하면 excluded_scope=true, lookup_all=false이고 실제로 요청한 일부 필드만 requested_fields에 둔다.
- 식생지수와 NDVI는 ndvi다. 허용 지표가 아닌 요청을 비슷한 requested_fields로 바꾸거나 추측하지 말고 unsupported로 둔다.
- 인구·인구밀도처럼 허용 목록에 없는 지표는 unsupported다.
- 조회에서 공원까지 거리는 nearest_park_distance_m, 500m 내 공원 면적은 park_area_within_500m이다.
- 지원하지 않는 요청은 intent=unsupported, lookup_all=false, requested_fields=[], excluded_scope=false이며 changes를 비운다.
예: "이 격자 데이터 모두 알려줘"는 intent=lookup, lookup_all=true, requested_fields=[], excluded_scope=false다.
예: "모든 데이터 말고 녹지율만 알려줘"는 intent=lookup, lookup_all=false, requested_fields=["green_ratio"], excluded_scope=true다.
예: "인구밀도를 알려줘"는 intent=unsupported, lookup_all=false, requested_fields=[], excluded_scope=false다.
예: "그거 5플오 해줘"처럼 변경 대상과 방향이 불명확하면 intent=simulation, lookup_all=false, requested_fields=[], excluded_scope=false, changes=[], unresolved=["change_field","change_operation"]이다.
"""

_GRID_ID_PATTERN = re.compile(r"(?<![\d_])\d{5}_\d{5}(?![\d_])")
_NUMBER_PATTERN = re.compile(
    r"(?<![\d.])"
    r"(?P<number>[+\-−]?(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?)"
    r"(?![\d.])"
)
_PERCENTAGE_POINT_UNIT_PATTERN = re.compile(r"%\s*[pP]")
_PERCENT_UNIT_PATTERN = re.compile(r"%(?!\s*[pP])")
_M2_UNIT_PATTERN = re.compile(
    r"(?:㎡|m\s*(?:²|\^?2)\b|제곱(?:미터)?)",
    re.IGNORECASE,
)
_ADJACENT_UNIT_SUFFIX_PATTERN = re.compile(
    r"^\s*(?:%\s*[pP]?|㎡|m\s*(?:²|\^?2)\b|제곱(?:미터)?)",
    re.IGNORECASE,
)
_REASONING_PATTERNS = (
    re.compile(r"\b(?:okay|wait|let's|i need to|the user|according to rule)\b", re.I),
    re.compile(r"추론\s*과정"),
)


@dataclass(frozen=True)
class _NormalizedChange:
    field: str
    operation: str
    value: float
    unit: str
    basis: str
    source_text: str
    value_text: str


@dataclass(frozen=True)
class _NormalizedRequest:
    intent: str
    lookup_all: bool
    requested_fields: tuple[str, ...]
    excluded_scope: bool
    changes: tuple[_NormalizedChange, ...]
    assumptions: tuple[str, ...]
    unresolved: tuple[str, ...]


def _normalized_router_text(value: Any, field_name: str) -> str:
    if not isinstance(value, str):
        raise ChatProtocolError(
            f"Qwen 구조화 출력의 {field_name}이 문자열이 아닙니다."
        )
    normalized = " ".join(value.split())
    if not normalized or len(normalized) > 500:
        raise ChatProtocolError(
            f"Qwen 구조화 출력의 {field_name}이 비어 있거나 너무 깁니다."
        )
    return normalized


def _normalized_router_text_list(value: Any, field_name: str) -> tuple[str, ...]:
    if not isinstance(value, list):
        raise ChatProtocolError(
            f"Qwen 구조화 출력의 {field_name}이 배열이 아닙니다."
        )
    normalized: list[str] = []
    for item in value:
        text = _normalized_router_text(item, field_name)
        if text not in normalized:
            normalized.append(text)
    return tuple(normalized)


def _lookup_field_is_grounded(message: str, field: str) -> bool:
    """중앙 필드 메타데이터의 명칭이 사용자 원문에 실제로 있는지 확인한다."""

    compact_message = "".join(message.split()).casefold()
    spec = GRID_FIELD_SPECS[field]
    raw_aliases = spec.get("aliases")
    aliases = raw_aliases if isinstance(raw_aliases, (tuple, list)) else ()
    terms = (field, str(spec["label"]), *(str(alias) for alias in aliases))
    return any(
        compact_term and compact_term in compact_message
        for term in terms
        if (compact_term := "".join(term.split()).casefold())
    )


def _recognized_lookup_fields(message: str) -> list[str]:
    """중앙 alias의 최장 비중첩 일치로 요청된 조회 필드를 확정한다."""

    compact_message = "".join(message.split()).casefold()
    candidates: list[tuple[int, int, str]] = []
    for field in ALLOWED_GRID_FIELDS:
        spec = GRID_FIELD_SPECS[field]
        raw_aliases = spec.get("aliases")
        aliases = raw_aliases if isinstance(raw_aliases, (tuple, list)) else ()
        terms = (field, str(spec["label"]), *(str(alias) for alias in aliases))
        for term in terms:
            compact_term = "".join(term.split()).casefold()
            if not compact_term:
                continue
            start = compact_message.find(compact_term)
            while start >= 0:
                candidate = (start, start + len(compact_term), field)
                if candidate not in candidates:
                    candidates.append(candidate)
                start = compact_message.find(compact_term, start + 1)

    accepted: list[tuple[int, int, str]] = []
    for candidate in sorted(
        candidates,
        key=lambda item: (-(item[1] - item[0]), item[0]),
    ):
        start, end, _ = candidate
        if any(
            start < accepted_end and end > accepted_start
            for accepted_start, accepted_end, _ in accepted
        ):
            continue
        accepted.append(candidate)

    ordered: list[str] = []
    for _, _, field in sorted(accepted, key=lambda item: item[0]):
        if field not in ordered:
            ordered.append(field)
    return ordered


def _simulation_field_is_grounded(source_text: str, field: str) -> bool:
    compact_source = "".join(source_text.split()).casefold()
    return _lookup_field_is_grounded(source_text, field) or any(
        "".join(term.split()).casefold() in compact_source
        for term in _SIMULATION_FIELD_TERMS[field]
    )


def _value_unit_evidence(original_message: str, value_text: str) -> str:
    """숫자 바로 뒤에 붙은 명백한 단위만 value_text에 보완한다."""

    starts: list[int] = []
    start = original_message.find(value_text)
    while start >= 0:
        starts.append(start)
        start = original_message.find(value_text, start + 1)
    if len(starts) != 1:
        return value_text

    suffix = original_message[starts[0] + len(value_text):]
    match = _ADJACENT_UNIT_SUFFIX_PATTERN.match(suffix)
    return value_text + match.group(0) if match is not None else value_text


def _scope_signals(message: str) -> tuple[bool, bool]:
    """소수의 안정적인 전체 범위 표현과 그 부정 여부만 판정한다."""

    normalized_message = " ".join(message.split()).casefold()
    compact_message = "".join(normalized_message.split())
    has_full_scope = any(
        term in compact_message for term in _FULL_SCOPE_TERMS
    ) or _STANDALONE_ALL_PATTERN.search(normalized_message) is not None
    has_excluded_scope = has_full_scope and any(
        term in compact_message for term in _EXCLUDED_SCOPE_TERMS
    )
    return has_full_scope, has_excluded_scope


def _parse_normalized_request(
    content: str,
    original_message: str,
) -> _NormalizedRequest:
    """Qwen JSON schema 출력을 다시 엄격하게 검증한다."""

    try:
        decoded = json.loads(content)
    except json.JSONDecodeError as exc:
        raise ChatProtocolError(
            "Qwen 구조화 출력이 올바른 JSON이 아닙니다."
        ) from exc
    if not isinstance(decoded, Mapping):
        raise ChatProtocolError("Qwen 구조화 출력이 객체가 아닙니다.")

    required_keys = {
        "intent",
        "lookup_all",
        "requested_fields",
        "excluded_scope",
        "changes",
        "assumptions",
        "unresolved",
    }
    if set(decoded) != required_keys:
        raise ChatProtocolError(
            "Qwen 구조화 출력의 최상위 필드가 계약과 일치하지 않습니다."
        )

    intent = decoded.get("intent")
    if not isinstance(intent, str) or intent not in _SUPPORTED_INTENTS:
        raise ChatProtocolError("Qwen 구조화 출력의 intent가 올바르지 않습니다.")

    raw_lookup_all = decoded.get("lookup_all")
    if not isinstance(raw_lookup_all, bool):
        raise ChatProtocolError(
            "Qwen 구조화 출력의 lookup_all이 불리언이 아닙니다."
        )
    raw_excluded_scope = decoded.get("excluded_scope")
    if not isinstance(raw_excluded_scope, bool):
        raise ChatProtocolError(
            "Qwen 구조화 출력의 excluded_scope가 불리언이 아닙니다."
        )

    raw_requested_fields = decoded.get("requested_fields")
    if not isinstance(raw_requested_fields, list):
        raise ChatProtocolError(
            "Qwen 구조화 출력의 requested_fields가 배열이 아닙니다."
        )
    requested_fields: list[str] = []
    for field in raw_requested_fields:
        if not isinstance(field, str) or field not in ALLOWED_GRID_FIELDS:
            raise ChatProtocolError(
                "Qwen 구조화 출력에 지원하지 않는 조회 필드가 있습니다."
            )
        if field not in requested_fields:
            requested_fields.append(field)

    assumptions = list(
        _normalized_router_text_list(decoded.get("assumptions"), "assumptions")
    )
    raw_unresolved = decoded.get("unresolved")
    if not isinstance(raw_unresolved, list):
        raise ChatProtocolError(
            "Qwen 구조화 출력의 unresolved가 배열이 아닙니다."
        )
    unresolved: list[str] = []
    for code in raw_unresolved:
        if not isinstance(code, str) or code not in _UNRESOLVED_CODES:
            raise ChatProtocolError(
                "Qwen 구조화 출력의 unresolved 코드가 올바르지 않습니다."
            )
        if code not in unresolved:
            unresolved.append(code)

    raw_changes = decoded.get("changes")
    if not isinstance(raw_changes, list):
        raise ChatProtocolError("Qwen 구조화 출력의 changes가 배열이 아닙니다.")

    change_keys = {
        "field",
        "operation",
        "value",
        "unit",
        "basis",
        "source_text",
        "value_text",
    }
    normalized_original_message = " ".join(original_message.split())
    changes: list[_NormalizedChange] = []
    duplicate_fields: set[str] = set()
    for raw_change in raw_changes:
        if not isinstance(raw_change, Mapping) or set(raw_change) != change_keys:
            raise ChatProtocolError(
                "Qwen 구조화 출력의 change 필드가 계약과 일치하지 않습니다."
            )
        field = raw_change.get("field")
        operation = raw_change.get("operation")
        unit = raw_change.get("unit")
        basis = raw_change.get("basis")
        value = raw_change.get("value")
        source_text = _normalized_router_text(
            raw_change.get("source_text"),
            "source_text",
        )
        value_text = _normalized_router_text(
            raw_change.get("value_text"),
            "value_text",
        )
        if source_text not in normalized_original_message:
            unresolved.append("change_source")
            continue
        if value_text not in normalized_original_message:
            unresolved.append("change_value")
            continue

        if not isinstance(field, str) or field not in _SIMULATION_FIELDS:
            raise ChatProtocolError("Qwen이 지원하지 않는 변경 필드를 반환했습니다.")
        value_unit_evidence = _value_unit_evidence(
            normalized_original_message,
            value_text,
        )
        park_value_unit = (
            _M2_UNIT_PATTERN.search(value_unit_evidence) is not None
        )
        field_is_grounded = _simulation_field_is_grounded(source_text, field)
        if not field_is_grounded or (
            field != "park_area_within_500m" and park_value_unit
        ):
            unresolved.append("change_field")
            continue
        if (
            not isinstance(operation, str)
            or operation not in _SUPPORTED_OPERATIONS
        ):
            raise ChatProtocolError("Qwen이 지원하지 않는 변경 연산을 반환했습니다.")
        if not isinstance(unit, str) or unit not in _SUPPORTED_UNITS:
            raise ChatProtocolError("Qwen이 지원하지 않는 변경 단위를 반환했습니다.")
        if not isinstance(basis, str) or basis not in _SUPPORTED_BASES:
            raise ChatProtocolError("Qwen이 지원하지 않는 변경 기준을 반환했습니다.")
        if (
            not isinstance(value, Real)
            or isinstance(value, bool)
            or not math.isfinite(float(value))
            or float(value) <= 0
        ):
            unresolved.append("change_value")
            continue

        value_matches = list(_NUMBER_PATTERN.finditer(value_text))
        if len(value_matches) != 1:
            unresolved.append("change_value")
            continue
        value_token = value_matches[0].group("number")
        normalized_value_token = (
            value_token.replace("−", "-").replace(",", "")
        )
        try:
            source_value = float(normalized_value_token)
        except ValueError:
            unresolved.append("change_value")
            continue
        if not math.isclose(
            float(value),
            abs(source_value),
            rel_tol=0,
            abs_tol=1e-12,
        ):
            unresolved.append("change_value")
            continue
        if (
            normalized_value_token.startswith(("-", "−"))
            and operation != "decrease"
        ) or (
            normalized_value_token.startswith("+")
            and operation != "increase"
        ):
            unresolved.append("change_operation")
            continue

        if field == "park_area_within_500m":
            if not park_value_unit:
                unresolved.append("change_unit")
                continue
            unit = "m2"
        elif _PERCENTAGE_POINT_UNIT_PATTERN.search(value_unit_evidence):
            unit = "percentage_point"
        elif _PERCENT_UNIT_PATTERN.search(value_unit_evidence):
            unit = "percent"

        if field == "park_area_within_500m":
            if unit != "m2" or basis != "direct":
                unresolved.append("change_unit")
                continue
            if operation == "decrease":
                unresolved.append("park_area_decrease")
                continue
        else:
            if unit not in {"percent", "percentage_point"}:
                unresolved.append("change_unit")
                continue
            if unit == "percentage_point" and basis != "direct":
                unresolved.append("change_unit")
                continue

        if field in duplicate_fields or any(
            change.field == field for change in changes
        ):
            duplicate_fields.add(field)
            changes = [change for change in changes if change.field != field]
            unresolved.append("conflicting_changes")
            continue

        changes.append(
            _NormalizedChange(
                field=field,
                operation=operation,
                value=float(value),
                unit=unit,
                basis=basis,
                source_text=source_text,
                value_text=value_text,
            )
        )

    duplicated_sources = {
        change.source_text
        for change in changes
        if sum(
            candidate.source_text == change.source_text
            for candidate in changes
        )
        > 1
    }
    if duplicated_sources:
        changes = [
            change
            for change in changes
            if change.source_text not in duplicated_sources
        ]
        unresolved.append("conflicting_changes")

    has_full_scope, has_excluded_scope = _scope_signals(original_message)
    lookup_all = raw_lookup_all
    excluded_scope = raw_excluded_scope

    if intent == "simulation":
        if requested_fields or lookup_all or excluded_scope:
            raise ChatProtocolError(
                "시뮬레이션 구조화 출력에 조회 정보가 포함되었습니다."
            )
        if not changes and not unresolved:
            unresolved.extend(
                ("change_field", "change_operation", "change_value")
            )
    elif intent == "lookup":
        if changes:
            raise ChatProtocolError(
                "현재값 조회 구조화 출력에 changes가 포함되었습니다."
            )
        assumptions = []
        grounded_fields = _recognized_lookup_fields(original_message)
        excluded_scope = has_excluded_scope

        if has_full_scope and not has_excluded_scope:
            lookup_all = True
            requested_fields = []
        elif has_excluded_scope:
            lookup_all = False
            requested_fields = grounded_fields
            if not requested_fields and not unresolved:
                unresolved.append("lookup_field")
        elif lookup_all:
            if requested_fields:
                raise ChatProtocolError(
                    "전체 조회 구조화 출력에 개별 requested_fields가 포함되었습니다."
                )
        else:
            if grounded_fields:
                requested_fields = grounded_fields
            else:
                requested_fields = []
                intent = "unsupported"
    else:
        if changes or requested_fields or lookup_all:
            raise ChatProtocolError(
                "지원하지 않는 요청의 구조화 출력에 실행 정보가 포함되었습니다."
            )
        grounded_fields = _recognized_lookup_fields(original_message)
        if has_full_scope and not has_excluded_scope:
            intent = "lookup"
            lookup_all = True
            requested_fields = []
            excluded_scope = False
        elif has_excluded_scope and grounded_fields:
            intent = "lookup"
            lookup_all = False
            requested_fields = grounded_fields
            excluded_scope = True
        else:
            excluded_scope = has_excluded_scope

    return _NormalizedRequest(
        intent=intent,
        lookup_all=lookup_all,
        requested_fields=tuple(requested_fields),
        excluded_scope=excluded_scope,
        changes=tuple(changes),
        assumptions=tuple(dict.fromkeys(assumptions)),
        unresolved=tuple(dict.fromkeys(unresolved)),
    )


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
        "intent": None,
        "lookup_all": False,
        "requested_fields": [],
        "excluded_scope": False,
        "validation_result": "not_run",
        "final_branch": None,
        "actual_used_tools": [],
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
            format=ROUTER_OUTPUT_SCHEMA,
            think=False,
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


def _compact_lookup_text(value: str) -> str:
    """최종 답변의 등록 필드명 비교를 위한 공백 정리."""

    return re.sub(r"\s+", "", value).casefold()


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
        or any(field not in ALLOWED_GRID_FIELDS for field in requested_fields)
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


def _clarification_answer(unresolved: tuple[str, ...]) -> str:
    lines = ["다음 내용만 확인해 주세요:"]
    lines.extend(
        f"- {_CLARIFICATION_BY_CODE[item]}"
        for item in unresolved
    )
    return "\n".join(lines)


def _display_router_number(value: float) -> str:
    return f"{value:g}"


def _append_assumptions(answer: str, assumptions: list[str]) -> str:
    if not assumptions:
        return answer
    lines = [answer, "해석 가정:"]
    lines.extend(f"- {assumption}" for assumption in assumptions)
    return "\n".join(lines)


def _safe_simulation_assumptions(
    normalized_request: _NormalizedRequest,
) -> list[str]:
    """Qwen의 assumption 신호를 검증된 구조에 기반한 문장으로 재작성한다."""

    if not normalized_request.assumptions:
        return []

    safe: list[str] = []
    for change in normalized_request.changes:
        label = str(GRID_FIELD_SPECS[change.field]["label"])
        direction = "증가" if change.operation == "increase" else "감소"
        value = _display_router_number(change.value)
        if change.field == "park_area_within_500m":
            normalized_change = f"{value}㎡ {direction}"
        elif change.basis == "relative_to_current":
            normalized_change = f"현재값의 {value}% {direction}"
        else:
            normalized_change = f"{value}%p {direction}"
        safe.append(
            f'"{change.source_text}" 표현을 {label} '
            f"{normalized_change} 요청으로 정규화했습니다."
        )
    return list(dict.fromkeys(safe))


def _prepare_simulation_arguments(
    normalized_request: _NormalizedRequest,
    grid_id: str,
) -> tuple[dict[str, Any], list[str], dict[str, Any] | None]:
    """검증된 의미 구조에서 기존 run_simulation 인자를 계산한다."""

    tool_arguments: dict[str, Any] = {
        "grid_id": grid_id,
        "green_ratio_delta": 0.0,
        "impervious_ratio_delta": 0.0,
        "park_area_delta": 0.0,
    }
    assumptions = _safe_simulation_assumptions(normalized_request)
    relative_fields = [
        change.field
        for change in normalized_request.changes
        if change.basis == "relative_to_current"
    ]
    current_values: Mapping[str, Any] = {}
    if relative_fields:
        lookup_function = TOOL_FUNCTIONS["get_grid_data"]
        try:
            raw_lookup_result = lookup_function(
                grid_id=grid_id,
                fields=list(dict.fromkeys(relative_fields)),
            )
        except Exception as exc:
            raise ChatProtocolError(
                "상대 변경 계산에 필요한 현재값을 조회할 수 없습니다."
            ) from exc
        if not isinstance(raw_lookup_result, Mapping):
            raise ChatProtocolError(
                "상대 변경 계산용 조회 결과가 올바른 객체가 아닙니다."
            )
        if raw_lookup_result.get("success") is not True:
            error = raw_lookup_result.get("error")
            error_text = (
                str(error).strip()
                if isinstance(error, str) and error.strip()
                else "상대 변경 계산에 필요한 현재값을 조회할 수 없습니다."
            )
            return (
                tool_arguments,
                assumptions,
                {
                    "success": False,
                    "grid_id": grid_id,
                    "error": error_text,
                },
            )
        raw_values = raw_lookup_result.get("values")
        if not isinstance(raw_values, Mapping):
            raise ChatProtocolError(
                "상대 변경 계산용 조회 결과에 values가 없습니다."
            )
        current_values = raw_values

    for change in normalized_request.changes:
        label = str(GRID_FIELD_SPECS[change.field]["label"])
        if change.field == "park_area_within_500m":
            magnitude = change.value
        elif change.unit == "percentage_point":
            magnitude = change.value / 100.0
        elif change.basis == "relative_to_current":
            raw_current = current_values.get(change.field)
            if (
                not isinstance(raw_current, Real)
                or isinstance(raw_current, bool)
                or not math.isfinite(float(raw_current))
            ):
                raise ChatProtocolError(
                    f"{label}의 현재값이 유한 숫자가 아닙니다."
                )
            magnitude = float(raw_current) * change.value / 100.0
            assumptions.append(
                f"현재 {label} 값의 {_display_router_number(change.value)}%에 "
                "해당하는 상대 변경으로 적용했습니다."
            )
        else:
            magnitude = change.value / 100.0
            assumptions.append(
                f"{label}의 {_display_router_number(change.value)}% 표현을 "
                "동일한 수치의 %p 변경으로 적용했습니다."
            )

        delta = magnitude if change.operation == "increase" else -magnitude
        if not math.isfinite(delta):
            raise ChatProtocolError(
                f"{label}의 계산된 변경값이 유한 숫자가 아닙니다."
            )
        parameter = _SIMULATION_ARGUMENT_BY_FIELD[change.field]
        tool_arguments[parameter] = delta

    return (
        tool_arguments,
        list(dict.fromkeys(assumptions)),
        None,
    )


def _run_chat_with_client(
    client: Any,
    message: str,
    resolved_grid_id: str,
    metrics: dict[str, Any],
) -> ChatResult:
    router_message = _GRID_ID_PATTERN.sub("", message).strip()
    messages: list[Any] = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": router_message},
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
    if not first_content:
        raise ChatProtocolError("Qwen이 구조화 요청을 반환하지 않았습니다.")
    normalized_request = _parse_normalized_request(
        first_content,
        router_message,
    )
    metrics.update(
        {
            "intent": normalized_request.intent,
            "lookup_all": normalized_request.lookup_all,
            "requested_fields": list(normalized_request.requested_fields),
            "excluded_scope": normalized_request.excluded_scope,
            "validation_result": "accepted",
        }
    )

    if normalized_request.unresolved:
        metrics["final_branch"] = "clarification"
        return _supported_scope_result(
            answer=_clarification_answer(normalized_request.unresolved),
            first_thinking=first_thinking,
            first_content=first_content,
            metrics=metrics,
        )
    if normalized_request.intent == "unsupported":
        metrics["final_branch"] = "supported_scope"
        return _supported_scope_result(
            first_thinking=first_thinking,
            first_content=first_content,
            metrics=metrics,
        )
    tool_name = (
        "get_grid_data"
        if normalized_request.intent == "lookup"
        else "run_simulation"
    )
    metrics["tool_name"] = tool_name
    tool_function = TOOL_FUNCTIONS[tool_name]
    tool_started = time.perf_counter()
    tool_arguments: dict[str, Any] = {"grid_id": resolved_grid_id}
    try:
        if normalized_request.intent == "lookup":
            tool_arguments["fields"] = (
                list(ALLOWED_GRID_FIELDS)
                if normalized_request.lookup_all
                else list(normalized_request.requested_fields)
            )
            assumptions = []
            preparation_error = None
        else:
            tool_arguments, assumptions, preparation_error = (
                _prepare_simulation_arguments(
                    normalized_request,
                    resolved_grid_id,
                )
            )
        raw_tool_result = (
            preparation_error
            if preparation_error is not None
            else tool_function(**tool_arguments)
        )
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
        if tool_result.get("success") is True:
            final_content = _append_assumptions(final_content, assumptions)
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
        used_tools=[tool_name] if tool_result.get("success") is True else [],
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
    metrics["final_branch"] = "tool_result"
    metrics["actual_used_tools"] = list(result.used_tools)
    validation_started = time.perf_counter()
    try:
        validation_tool_result = dict(tool_result)
        validation_tool_result["routing_assumptions"] = assumptions
        _validate_final_answer(
            final_content,
            tool_name,
            validation_tool_result,
        )
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
        if client is not None:
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
