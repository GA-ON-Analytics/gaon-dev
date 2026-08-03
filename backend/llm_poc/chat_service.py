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
    POLICY_STATE_ADVERSE,
    POLICY_STATE_INDISTINGUISHABLE,
    POLICY_STATE_NO_ROOM,
    POLICY_STATE_RANKED,
    POLICY_STATE_UNRESPONSIVE,
    TOOL_FUNCTIONS,
    format_grid_field_value,
)


DEFAULT_MODEL_NAME = "qwen3:4b"
MODEL_NAME = os.getenv("GAON_LLM_MODEL") or DEFAULT_MODEL_NAME
DEFAULT_LLM_TIMEOUT_SECONDS = 120.0
ROUTER_NUM_PREDICT = 768
MODEL_KEEP_ALIVE = "5m"
LOGGER = logging.getLogger("uvicorn.error")
_GRID_FIELD_LABELS = "、".join(
    str(GRID_FIELD_SPECS[field]["label"]) for field in ALLOWED_GRID_FIELDS
)


def _build_router_field_catalog() -> str:
    """tools.py 중앙 메타데이터에서 Qwen용 의미 카탈로그를 만든다."""

    lines: list[str] = []
    for field in ALLOWED_GRID_FIELDS:
        spec = GRID_FIELD_SPECS[field]
        aliases = "·".join(str(item) for item in spec.get("aliases", ()))
        examples = " / ".join(str(item) for item in spec.get("examples", ()))
        confusable = "·".join(
            f"{candidate}({GRID_FIELD_SPECS[candidate]['label']})"
            for candidate in spec.get("confusable_with", ())
            if candidate in GRID_FIELD_SPECS
        )
        parts = [
            f"- {field} | {spec['label']}",
            str(spec.get("semantic_definition") or spec["description"]),
        ]
        if aliases:
            parts.append(f"명시 표현: {aliases}")
        if examples:
            parts.append(f"의미 예시: {examples}")
        if confusable:
            parts.append(f"혼동 후보: {confusable}")
        lines.append(" | ".join(parts))
    return "\n".join(lines)


_ROUTER_FIELD_CATALOG = _build_router_field_catalog()
_SIMULATION_FIELDS = (
    "green_ratio",
    "impervious_ratio",
    "ndvi",
    "albedo",
)
_SIMULATION_ARGUMENT_BY_FIELD = {
    "green_ratio": "green_ratio_delta",
    "impervious_ratio": "impervious_ratio_delta",
    "ndvi": "ndvi_delta",
    "albedo": "albedo_delta",
}
_SIMULATION_FIELD_TERMS = {
    "green_ratio": ("녹지",),
    "impervious_ratio": ("불투수",),
    "ndvi": ("ndvi", "식생", "녹화"),
    "albedo": ("albedo", "알베도", "반사율", "쿨루프"),
}
_SUPPORTED_INTENTS = {
    "field_list",
    "lookup",
    "policy_ranking",
    "simulation",
    "unsupported",
}
# Tool을 부르지 않고 앞 단계에서 곧바로 답을 만드는 intent.
_TOOLLESS_INTENTS = {"field_list", "unsupported"}
# intent → 실행할 Tool. intent가 늘어날 때 고칠 곳을 이 표 하나로 모은다.
# 예전에는 "lookup이면 get_grid_data, 아니면 run_simulation" 삼항식이라 매핑이 코드 흐름에
# 숨어 있었고, 새 intent가 조용히 run_simulation으로 새도 아무도 몰랐다.
_INTENT_TOOL = {
    "lookup": "get_grid_data",
    "policy_ranking": "rank_policies",
    "simulation": "run_simulation",
}
# 표가 어긋나는 두 방향을 모두 import 시점에 잡는다.
#   - 라우터가 낼 수 있는 intent인데 실행할 Tool이 없다  → 예전 삼항식이 조용히 삼키던 경우
#   - 라우터가 낼 수 없는 intent를 매핑해 뒀다            → 죽은 표
if _INTENT_TOOL.keys() | _TOOLLESS_INTENTS != _SUPPORTED_INTENTS:
    raise RuntimeError(
        "intent 표가 _SUPPORTED_INTENTS와 어긋났습니다. "
        f"지원={sorted(_SUPPORTED_INTENTS)} "
        f"Tool연결={sorted(_INTENT_TOOL)} Tool없음={sorted(_TOOLLESS_INTENTS)}"
    )
if not set(_INTENT_TOOL.values()) <= TOOL_FUNCTIONS.keys():
    raise RuntimeError(
        "_INTENT_TOOL이 TOOL_FUNCTIONS에 없는 Tool을 가리킵니다: "
        f"{sorted(set(_INTENT_TOOL.values()) - TOOL_FUNCTIONS.keys())}"
    )
_SUPPORTED_RESOLUTIONS = {"resolved", "ambiguous", "unsupported"}
_SUPPORTED_OPERATIONS = {"increase", "decrease"}
_SUPPORTED_UNITS = {"percent", "percentage_point", "unitless"}
_SUPPORTED_BASES = {"direct", "relative_to_current"}
_FULL_SCOPE_TERMS = ("전체", "모두", "모든", "전부")
_EXCLUDED_SCOPE_TERMS = (
    "말고",
    "제외",
    "필요없",
    "보여주지말고",
    "아니라",
    "대신",
    "빼고",
)
_GENERIC_LOOKUP_TARGET_TERMS = ("데이터", "정보", "현재값", "현황")
_GENERIC_LOOKUP_REQUEST_TERMS = ("알려", "보여", "줘", "조회", "확인")
_META_REQUEST_TERMS = (
    "설명",
    "원리",
    "학습",
    "산출",
    "지원",
    "가능",
    "종류",
    "목록",
    "출처",
    "수집",
    "근거",
    "어디서",
    "할수있",
    "뭘할수",
    "무엇을할수",
)
_EXPLICIT_SIMULATION_TERMS = ("시뮬레이션", "정책변경", "예상변화", "예측변화")
_SIMULATION_ACTION_PATTERN = re.compile(
    r"(?:높여|높이(?:면|고|기|자)|낮춰|낮추|늘려|늘리|줄여|줄이|"
    r"올려|올리|내려|내리|증가시|감소시|확대|축소)"
)
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
}
_CLARIFICATION_BY_CODE = {
    "ambiguous_request": "요청에서 확정되지 않은 부분을 구체적으로 알려주세요.",
    "change_field": "변경할 지표를 알려주세요.",
    "change_operation": "증가 또는 감소 방향을 알려주세요.",
    "change_source": "변경 요청의 원문 근거를 다시 확인해 주세요.",
    "change_unit": (
        "녹지율·불투수율은 % 또는 %p, NDVI·알베도는 0.05처럼 "
        "지수에 더할 소수 변화량으로 알려주세요."
    ),
    "change_value": "변경량을 숫자로 알려주세요.",
    "conflicting_changes": "같은 지표의 변경값은 하나로 확정해 주세요.",
    "lookup_field": "조회할 격자 지표를 알려주세요.",
}
SUPPORTED_SCOPE_ANSWER = (
    "현재 요청은 아직 지원하지 않습니다. "
    "GA:ON AI에서는 격자 데이터 조회, 정책 시뮬레이션, 정책 우선순위 추천을 "
    "사용할 수 있습니다. "
    "AI 화면의 사용 가이드를 확인하거나 "
    "“조회 가능한 데이터 목록 보여줘”라고 입력해 주세요."
)
FIELD_LIST_ANSWER = (
    "조회 가능한 데이터는 다음 18개입니다: "
    f"{_GRID_FIELD_LABELS}. "
    "정확한 데이터명을 몰라도 의미가 비슷한 표현으로 질문할 수 있습니다."
)
ROUTER_OUTPUT_SCHEMA = {
    "type": "object",
    "properties": {
        "intent": {
            "type": "string",
            "enum": [
                "unsupported",
                "field_list",
                "lookup",
                "simulation",
                "policy_ranking",
            ],
            "description": (
                "허용된 격자 지표의 현재값 조회와 전체 조회는 lookup, "
                "조회 가능한 데이터 목록 요청은 field_list, 지원 정책 변경은 "
                "simulation, 어떤 정책이 효과적인지 우선순위를 묻는 요청은 "
                "policy_ranking, 그 밖의 요청은 unsupported"
            ),
        },
        "resolution": {
            "type": "string",
            "enum": ["resolved", "ambiguous", "unsupported"],
            "description": (
                "요청 필드 의미가 하나로 확정되면 resolved, 2~3개 후보 중 "
                "확인이 필요하면 ambiguous, 지원 필드가 아니면 unsupported. "
                "평균 몇 층=avg_ground_floor_count resolved, 가장 높은 건물의 "
                "층수=max_ground_floor_count resolved, 식생의 푸르름·활력=ndvi "
                "resolved이며 단순 식물의 양만 ambiguous"
            ),
        },
        "candidate_fields": {
            "type": "array",
            "items": {
                "type": "string",
                "enum": list(ALLOWED_GRID_FIELDS),
            },
            "maxItems": 3,
            "uniqueItems": True,
            "description": (
                "ambiguous일 때만 가능한 후보 2~3개. resolved에서는 비우거나 "
                "requested_fields와 같게 하고 unsupported에서는 비운다. "
                "평균·가장 높은·푸르름·활력처럼 구분 기준이 원문에 있으면 "
                "ambiguous 후보를 만들지 않는다."
            ),
        },
        "lookup_evidence": {
            "type": "string",
            "maxLength": 200,
            "description": (
                "조회 필드 의미 판단의 근거가 된 사용자 원문의 짧은 문자열. "
                "전체 조회·field_list·unsupported에서는 빈 문자열을 허용한다."
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
                            "ndvi=식생지수, albedo=표면 반사율"
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
                    "source_text": {
                        "type": "string",
                        "description": "변경 지표를 나타낸 원문의 가장 짧은 구간",
                    },
                    "operation_text": {
                        "type": "string",
                        "description": (
                            "증가 또는 감소 방향을 명시한 원문의 가장 짧은 구간. "
                            "방향 표현이 없으면 임의로 만들지 않는다."
                        ),
                    },
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
                    "operation_text",
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
        "resolution",
        "candidate_fields",
        "lookup_evidence",
        "lookup_all",
        "requested_fields",
        "excluded_scope",
        "changes",
        "assumptions",
        "unresolved",
    ],
    "additionalProperties": False,
}

SYSTEM_PROMPT = (
    """사용자 요청의 의미를 JSON schema로만 정규화한다.
답변 문장이나 실제 실행 인자를 만들지 않는다.

조회 가능한 공식 필드 의미 카탈로그:
"""
    + _ROUTER_FIELD_CATALOG
    + """

의미 판정:
- exact 단어 일치만 찾지 말고 카탈로그 정의와 예시를 이용해 자유로운 한국어 표현의 의미를 판정한다.
- 한 필드로 확정되면 resolution=resolved이고 일부 조회는 requested_fields에 공식 필드를 둔다.
- 두 필드 이상의 의미가 실제로 가능하면 resolution=ambiguous, requested_fields=[], candidate_fields에 가장 가까운 2~3개만 둔다.
- 지원 필드와 대응하지 않으면 resolution=unsupported, intent=unsupported이고 candidate_fields, requested_fields, changes를 비운다. 비슷한 다른 필드로 대체하지 않는다.
- resolved의 candidate_fields는 비우거나 requested_fields와 같아야 한다.
- lookup_evidence는 의미 판정 근거가 된 사용자 원문의 가장 짧은 실제 문자열이다. 전체 조회·field_list·unsupported이면 빈 문자열을 쓸 수 있다.
- "식물이 얼마나 많은지"는 면적 비율인 green_ratio와 푸르름·활력 지수인 ndvi 중 뜻이 불명확하므로 ambiguous다.
- "건물 높이"는 평균층수와 최대층수 중 기준이 없으면 avg_ground_floor_count와 max_ground_floor_count 사이의 ambiguous다.
- "평균적으로 몇 층"은 avg_ground_floor_count, "가장 높은 건물이 몇 층"은 max_ground_floor_count로 확정한다. 둘 다 building_ratio가 아니다.
- 식생이 얼마나 "푸른지·건강한지·활력이 있는지"는 ndvi로 확정한다. 단순히 식물이 "얼마나 많은지"만 물으면 green_ratio와 ndvi 사이의 ambiguous다.
- "앤디브이아이"와 글자 사이가 벌어진 "N D V I"는 ndvi다.
- 녹지지역으로 지정된 비율은 zoning_green_ratio, 실제 녹지 토지피복 비율은 green_ratio다.
- 건물이 땅을 덮은 비율은 building_ratio, 총 연면적 수준은 floor_area_ratio_proxy다.
- 가장 가까운 공원까지 거리는 nearest_park_distance_m, 반경 500m 내 공원 총면적은 park_area_within_500m다.

의도 판정:
- 현재값 조회만 intent=lookup이다. 일부 조회는 lookup_all=false와 requested_fields를 사용한다.
- 선택 격자의 데이터·정보·현재값·현황을 특정 지표 없이 요청하면 intent=lookup, resolution=resolved, lookup_all=true다.
- 전체·모두·모든·전부 또는 독립된 "다"로 전체 데이터를 요구하면 lookup_all=true다.
- 전체 범위를 말고·제외·필요 없고·보여주지 말고 등으로 부정하면 excluded_scope=true, lookup_all=false이고 실제 요청 필드만 requested_fields에 둔다.
- "조회 가능한 데이터 목록"처럼 지원 조회 필드 목록 자체를 요구하면 intent=field_list, resolution=resolved다. 실제 격자 Tool 조회는 아니다.
- 특정 지표를 지정하지 않고 어떤 정책이 효과적인지·무엇부터 해야 하는지 우선순위를 물으면 intent=policy_ranking, resolution=resolved이고 requested_fields, candidate_fields, changes를 비운다.
- 데이터 정의·뜻·출처·모델 설명·문서 질문은 아직 지원하지 않으므로 intent=unsupported, resolution=unsupported다.
- 인구·인구밀도처럼 카탈로그에 없는 지표는 unsupported다.
- 데이터 조회를 부정하고 정책 변경을 요청하면 simulation을 우선한다.

시뮬레이션:
- simulation이면 lookup_all=false, requested_fields=[], excluded_scope=false이고 각 변경을 changes에 하나씩 둔다.
- 변경 가능한 정책 레버는 green_ratio, impervious_ratio, ndvi, albedo 네 개뿐이다. 카탈로그의 의미 표현으로도 매핑한다.
- 녹지율·불투수율의 일반 percent 또는 비율 문맥의 단위 없는 수치는 증감 방향이 명확하면 unit=percent, basis=direct로 두고 해석을 assumptions에 쓴다.
- 녹지율·불투수율의 명시적 %p는 percentage_point/direct, 현재 값의 일정 비율은 percent/relative_to_current다.
- NDVI와 알베도는 무단위 지수다. "0.05 높인다"처럼 직접 더할 소수만 unit=unitless, basis=direct다. %·%p 또는 상대 비율은 unresolved=["change_unit"]이다.
- value는 원문 수치 자체다. 5%와 5%p는 value=5이며 0.05로 미리 환산하지 않는다.
- 공원 면적 등 다른 필드는 조회만 가능하고 정책 변경은 지원하지 않는다.
- 각 변경의 증가·감소 방향과 수치가 원문에 명시돼야 한다. 필요한 의미를 추측하지 않는다.
- source_text, operation_text, value_text는 각각 지표·방향·수치와 단위를 나타낸 원문의 가장 짧은 실제 문자열이다.
- 방향 없는 복합 변경은 changes를 실행 가능하게 만들지 말고 unresolved=["change_operation"]이다.
- 문맥상 명확한 오타나 단위 가정만 assumptions에 남긴다. 명확한 조회에는 assumptions=[]다.
- "높이면/낮추면 결과가 어떻게 돼?"도 방향과 변경량이 명시된 simulation이다.

대표 판정:
- "녹지가 이 지역에서 차지하는 비중 알려줘" → lookup/resolved, requested_fields=["green_ratio"].
- "건물들이 평균적으로 몇 층이야" → lookup/resolved, requested_fields=["avg_ground_floor_count"].
- "가장 높은 건물이 몇 층이야" → lookup/resolved, requested_fields=["max_ground_floor_count"].
- "물이 스며들지 않는 땅의 비율" → lookup/resolved, requested_fields=["impervious_ratio"].
- "표면이 햇빛을 얼마나 반사해" → lookup/resolved, requested_fields=["albedo"].
- "NDVI가 무슨 뜻이야?"와 "녹지율의 출처가 어디야?" → unsupported/unsupported.
- "녹지가 차지하는 비율을 5%p 높여줘" → simulation/resolved, green_ratio increase 5 percentage_point.
- "여기 어떤 정책이 가장 효과적이야"와 "뭐부터 해야 해?" → policy_ranking/resolved.
- "그거 5플오 해줘"처럼 변경 대상과 방향이 불명확하면 changes=[]와 unresolved=["change_field","change_operation"]을 사용한다.
- "녹지율을 올려줘"처럼 수치만 없으면 changes=[]와 unresolved=["change_value"]을 사용한다.
"""
)

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
_DIRECTION_TERMS = {
    "increase": ("증가", "증대", "늘", "높", "올", "추가", "확대", "상향", "+"),
    "decrease": ("감소", "줄", "낮", "내", "제거", "축소", "하향", "빼", "-", "−"),
}
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
    operation_text: str
    value_text: str


@dataclass(frozen=True)
class _NormalizedRequest:
    intent: str
    resolution: str
    candidate_fields: tuple[str, ...]
    lookup_evidence: str
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


def _normalized_router_optional_text(value: Any, field_name: str) -> str:
    if not isinstance(value, str):
        raise ChatProtocolError(
            f"Qwen 구조화 출력의 {field_name}이 문자열이 아닙니다."
        )
    normalized = " ".join(value.split())
    if len(normalized) > 200:
        raise ChatProtocolError(
            f"Qwen 구조화 출력의 {field_name}이 너무 깁니다."
        )
    return normalized


def _recognized_lookup_fields(message: str) -> list[str]:
    """명시 필드명·label·alias의 고정밀 보조 신호를 반환한다."""

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


def _high_precision_candidate_resolution(
    message: str,
    candidate_fields: list[str],
) -> str | None:
    """LLM이 좁힌 후보에 중앙 메타데이터의 결정적 구분 표현을 적용한다."""

    compact_message = _compact_routing_text(message)
    matched: list[str] = []
    for field in candidate_fields:
        cues = GRID_FIELD_SPECS[field].get("disambiguation_cues", ())
        if any(
            _compact_routing_text(str(cue)) in compact_message
            for cue in cues
        ):
            matched.append(field)
    return matched[0] if len(matched) == 1 else None


def _simulation_field_is_grounded(source_text: str, field: str) -> bool:
    recognized_fields = _recognized_lookup_fields(source_text)
    if recognized_fields:
        return recognized_fields == [field]
    # exact 표현이 없을 때는 Qwen의 의미 매핑을 허용한다. source_text가 실제
    # 원문인지, field가 정책 레버인지, 방향·수치·단위가 원문에 있는지는
    # 별도 Python 검증이 계속 담당한다.
    return field in _SIMULATION_FIELDS


def _simulation_operation_is_grounded(
    source_text: str,
    operation_text: str,
    operation: str,
) -> bool:
    """구조화된 증감 방향이 실제 원문의 명시적 표현인지 확인한다."""

    compact_source = "".join(source_text.split()).casefold()
    compact_operation = "".join(operation_text.split()).casefold()
    if not compact_operation or compact_operation not in compact_source:
        return False

    matched_directions = {
        direction
        for direction, terms in _DIRECTION_TERMS.items()
        if any(term in compact_operation for term in terms)
    }
    if matched_directions == {operation}:
        return True

    # 소형 모델이 operation_text 대신 수치 구간을 복사하는 경우에도 원문 전체에
    # 한 방향만 명시됐다면 그 근거를 사용할 수 있다. 서로 다른 방향이 섞인
    # 복합 문장에서는 이 보정을 하지 않아 각 변경 방향을 추측하지 않는다.
    original_directions = {
        direction
        for direction, terms in _DIRECTION_TERMS.items()
        if any(term in compact_source for term in terms)
    }
    return original_directions == {operation}


def _simulation_change_context(
    original_message: str,
    source_text: str,
    raw_changes: list[Any],
) -> str:
    """현재 지표부터 다음 변경 지표 전까지의 원문 구간을 반환한다."""

    source_starts = [
        match.start()
        for match in re.finditer(re.escape(source_text), original_message)
    ]
    if len(source_starts) != 1:
        return source_text
    current_start = source_starts[0]

    later_starts: list[int] = []
    for candidate in raw_changes:
        if not isinstance(candidate, Mapping):
            continue
        candidate_source = candidate.get("source_text")
        if not isinstance(candidate_source, str):
            continue
        normalized_candidate = " ".join(candidate_source.split())
        if not normalized_candidate or normalized_candidate == source_text:
            continue
        candidate_start = original_message.find(
            normalized_candidate,
            current_start + len(source_text),
        )
        if candidate_start > current_start:
            later_starts.append(candidate_start)

    context_end = min(later_starts, default=len(original_message))
    return original_message[current_start:context_end]


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


def _compact_routing_text(message: str) -> str:
    return "".join(message.split()).casefold()


def _has_lookup_negation(message: str) -> bool:
    compact_message = _compact_routing_text(message)
    return any(term in compact_message for term in _EXCLUDED_SCOPE_TERMS)


def _has_explicit_current_lookup_intent(message: str) -> bool:
    compact_message = _compact_routing_text(message)
    return any(
        term in compact_message
        for term in ("현재", "현재값", "알려", "보여", "조회", "확인")
    )


def _is_meta_request(message: str) -> bool:
    compact_message = _compact_routing_text(message)
    return any(term in compact_message for term in _META_REQUEST_TERMS)


def _is_field_list_request(message: str) -> bool:
    compact_message = _compact_routing_text(message)
    has_data_target = "데이터" in compact_message or "지표" in compact_message
    has_list_request = any(
        term in compact_message
        for term in ("조회가능", "어떤데이터", "지원데이터", "데이터목록", "지표목록")
    )
    return has_data_target and has_list_request


def _has_simulation_intent(message: str) -> bool:
    compact_message = _compact_routing_text(message)
    if any(term in compact_message for term in _EXPLICIT_SIMULATION_TERMS):
        return True

    simulation_fields = {
        field
        for field in _recognized_lookup_fields(message)
        if field in _SIMULATION_FIELDS
    }
    has_direction = any(
        term in compact_message
        for terms in _DIRECTION_TERMS.values()
        for term in terms
    )
    has_number = _NUMBER_PATTERN.search(message) is not None
    return bool(
        has_direction
        and has_number
        and (
            simulation_fields
            or _SIMULATION_ACTION_PATTERN.search(compact_message) is not None
        )
    )


def _is_generic_full_lookup_request(message: str) -> bool:
    """특정 필드 없는 현재 데이터·정보 조회 요청만 안전하게 인식한다."""

    compact_message = re.sub(
        r"[^0-9a-z가-힣]",
        "",
        _compact_routing_text(message),
    )
    target_matches = [
        (compact_message.find(term), term)
        for term in _GENERIC_LOOKUP_TARGET_TERMS
        if term in compact_message
    ]
    if (
        not target_matches
        or _has_lookup_negation(message)
        or _is_meta_request(message)
        or _has_simulation_intent(message)
    ):
        return False

    target_start, target = min(target_matches, key=lambda item: item[0])
    prefix = compact_message[:target_start]
    suffix = compact_message[target_start + len(target):]
    allowed_prefixes = {
        "",
        "현재",
        "현재의",
        "격자",
        "격자의",
        "이격자",
        "이격자의",
        "이격자현재",
        "선택격자",
        "선택한격자",
        "선택된격자",
        "해당격자",
        "이곳",
        "이곳현재",
        "여기",
        "여기현재",
    }
    if prefix not in allowed_prefixes:
        return False

    for particle in ("으로", "에서", "의", "을", "를", "은", "는", "이", "가", "도", "만"):
        if suffix.startswith(particle):
            suffix = suffix[len(particle):]
            break
    has_lookup_action = any(
        term in suffix for term in _GENERIC_LOOKUP_REQUEST_TERMS
    )
    residual_suffix = suffix
    for term in (
        *_GENERIC_LOOKUP_REQUEST_TERMS,
        "주세요",
        "줘",
        "좀",
        "봐",
        "해",
        "요",
    ):
        residual_suffix = residual_suffix.replace(term, "")
    if residual_suffix:
        return False

    is_implicit_current_request = (
        "현재" in prefix
        or (not prefix and not suffix)
    )
    return has_lookup_action or is_implicit_current_request


def _is_safe_explicit_full_lookup_request(
    message: str,
    *,
    has_full_scope: bool,
    has_excluded_scope: bool,
) -> bool:
    if (
        not has_full_scope
        or has_excluded_scope
        or _has_lookup_negation(message)
        or _is_meta_request(message)
        or _has_simulation_intent(message)
    ):
        return False

    normalized_message = " ".join(message.split()).casefold()
    standalone_match = _STANDALONE_ALL_PATTERN.search(normalized_message)
    if standalone_match is not None:
        without_all = _STANDALONE_ALL_PATTERN.sub("", normalized_message)
        compact_without_all = _compact_routing_text(without_all)
        has_lookup_action = any(
            term in compact_without_all
            for term in _GENERIC_LOOKUP_REQUEST_TERMS
        )
        residual = compact_without_all
        for term in (
            *_GENERIC_LOOKUP_REQUEST_TERMS,
            "주세요",
            "줘",
            "좀",
            "봐",
            "해",
            "요",
        ):
            residual = residual.replace(term, "")
        if has_lookup_action and not residual:
            return True

    without_scope = normalized_message
    for term in _FULL_SCOPE_TERMS:
        without_scope = without_scope.replace(term, "")
    return _is_generic_full_lookup_request(without_scope)


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
        "resolution",
        "candidate_fields",
        "lookup_evidence",
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

    resolution = decoded.get("resolution")
    if (
        not isinstance(resolution, str)
        or resolution not in _SUPPORTED_RESOLUTIONS
    ):
        raise ChatProtocolError(
            "Qwen 구조화 출력의 resolution이 올바르지 않습니다."
        )

    raw_candidate_fields = decoded.get("candidate_fields")
    if not isinstance(raw_candidate_fields, list):
        raise ChatProtocolError(
            "Qwen 구조화 출력의 candidate_fields가 배열이 아닙니다."
        )
    candidate_fields: list[str] = []
    for field in raw_candidate_fields:
        if not isinstance(field, str) or field not in ALLOWED_GRID_FIELDS:
            raise ChatProtocolError(
                "Qwen 구조화 출력에 지원하지 않는 후보 필드가 있습니다."
            )
        if field not in candidate_fields:
            candidate_fields.append(field)
    if len(candidate_fields) > 3:
        raise ChatProtocolError(
            "Qwen 구조화 출력의 후보 필드가 3개를 초과합니다."
        )

    lookup_evidence = _normalized_router_optional_text(
        decoded.get("lookup_evidence"),
        "lookup_evidence",
    )
    normalized_original_message = " ".join(original_message.split())
    if lookup_evidence and lookup_evidence not in normalized_original_message:
        if _is_meta_request(original_message):
            lookup_evidence = ""
        else:
            raise ChatProtocolError(
                "Qwen 조회 의미 근거가 사용자 원문에 존재하지 않습니다."
            )

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
        "operation_text",
        "value_text",
    }
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
        operation_text = _normalized_router_text(
            raw_change.get("operation_text"),
            "operation_text",
        )
        value_text = _normalized_router_text(
            raw_change.get("value_text"),
            "value_text",
        )
        if source_text not in normalized_original_message:
            unresolved.append("change_source")
            continue
        if operation_text not in normalized_original_message:
            unresolved.append("change_operation")
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
        if _M2_UNIT_PATTERN.search(value_unit_evidence):
            unresolved.extend(("change_field", "change_unit"))
            continue
        field_is_grounded = _simulation_field_is_grounded(source_text, field)
        if not field_is_grounded:
            unresolved.append("change_field")
            continue
        if (
            not isinstance(operation, str)
            or operation not in _SUPPORTED_OPERATIONS
        ):
            raise ChatProtocolError("Qwen이 지원하지 않는 변경 연산을 반환했습니다.")
        change_context = _simulation_change_context(
            normalized_original_message,
            source_text,
            raw_changes,
        )
        if not _simulation_operation_is_grounded(
            change_context,
            operation_text,
            operation,
        ):
            unresolved.append("change_operation")
            continue
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

        if field in {"green_ratio", "impervious_ratio"}:
            if _PERCENTAGE_POINT_UNIT_PATTERN.search(value_unit_evidence):
                unit = "percentage_point"
            elif _PERCENT_UNIT_PATTERN.search(value_unit_evidence):
                unit = "percent"
            if unit not in {"percent", "percentage_point"}:
                unresolved.append("change_unit")
                continue
            if unit == "percentage_point" and basis != "direct":
                unresolved.append("change_unit")
                continue
        else:
            if (
                _PERCENTAGE_POINT_UNIT_PATTERN.search(value_unit_evidence)
                or _PERCENT_UNIT_PATTERN.search(value_unit_evidence)
                or unit != "unitless"
                or basis != "direct"
            ):
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
                operation_text=operation_text,
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

    if changes and len(changes) == len(raw_changes):
        # Qwen이 unresolved를 함께 반환했더라도 모든 change가 원문 근거·단위·
        # 수치 검증을 통과했다면 해당 항목의 거짓 양성 신호는 제거한다.
        unresolved = [
            code
            for code in unresolved
            if code
            not in {
                "change_field",
                "change_operation",
                "change_source",
                "change_unit",
                "change_value",
            }
        ]
    elif not raw_changes:
        grounded_simulation_fields = [
            field
            for field in _recognized_lookup_fields(original_message)
            if field in _SIMULATION_FIELDS
        ]
        compact_message = "".join(original_message.split()).casefold()
        grounded_directions = {
            direction
            for direction, terms in _DIRECTION_TERMS.items()
            if any(term in compact_message for term in terms)
        }
        if (
            grounded_simulation_fields
            and len(grounded_directions) == 1
            and _NUMBER_PATTERN.search(original_message) is None
        ):
            unresolved = [
                code
                for code in unresolved
                if code not in {"change_field", "change_operation"}
            ]
            unresolved.append("change_value")

    has_full_scope, has_excluded_scope = _scope_signals(original_message)
    has_meta_request = _is_meta_request(original_message)
    has_field_list_request = _is_field_list_request(original_message)
    has_simulation_intent = _has_simulation_intent(original_message)
    has_generic_full_lookup = _is_generic_full_lookup_request(original_message)
    has_safe_explicit_full_lookup = _is_safe_explicit_full_lookup_request(
        original_message,
        has_full_scope=has_full_scope,
        has_excluded_scope=has_excluded_scope,
    )
    lookup_all = raw_lookup_all
    excluded_scope = raw_excluded_scope
    grounded_fields = _recognized_lookup_fields(original_message)

    if resolution == "unsupported":
        if intent == "simulation" and unresolved and not changes:
            # 변경 요청임은 알지만 대상·방향 등이 미완성인 기존 재질문
            # 계약은 유지한다. resolution은 필드 의미 판정용이고 실행은
            # unresolved 분기에서 계속 차단된다.
            resolution = "resolved"
            candidate_fields = []
            requested_fields = []
            lookup_all = False
            excluded_scope = False
        else:
            # unsupported가 한 축에서라도 판정되면 Qwen이 함께 채운 실행
            # 정보는 신뢰하지 않고 모두 폐기한다.
            intent = "unsupported"
            candidate_fields = []
            requested_fields = []
            changes = []
            assumptions = []
            unresolved = []
            lookup_all = False
            excluded_scope = False
    elif resolution == "ambiguous":
        if (
            intent != "lookup"
            or len(candidate_fields) not in {2, 3}
            or requested_fields
            or changes
            or lookup_all
        ):
            raise ChatProtocolError(
                "ambiguous 의미 판정의 후보 또는 실행 정보가 올바르지 않습니다."
            )
        decisive_field = (
            None
            if has_meta_request or has_simulation_intent
            else _high_precision_candidate_resolution(
                original_message,
                candidate_fields,
            )
        )
        if decisive_field is not None:
            resolution = "resolved"
            requested_fields = [decisive_field]
            candidate_fields = []
            unresolved = []
        excluded_scope = False

    if intent == "field_list":
        if not has_field_list_request:
            intent = "unsupported"
            resolution = "unsupported"
        requested_fields = []
        candidate_fields = []
        changes = []
        assumptions = []
        unresolved = []
        lookup_all = False
        excluded_scope = False
        lookup_evidence = ""
    elif intent == "policy_ranking":
        # 격자 하나에 정책 4개를 전부 돌려보는 요청이라 조회 필드도 변경 인자도
        # 필요 없다. 라우터가 실어 보낸 것이 있으면 그대로 흘리지 말고 비운다.
        # 이 분기가 없으면 새 intent가 아래 어느 elif에도 안 걸려 검증을 통째로
        # 건너뛴다.
        resolution = "resolved"
        requested_fields = []
        candidate_fields = []
        changes = []
        assumptions = []
        unresolved = []
        lookup_all = False
        excluded_scope = False
        lookup_evidence = ""
    elif intent == "simulation":
        if resolution != "resolved":
            raise ChatProtocolError(
                "시뮬레이션 필드 의미가 확정되지 않았습니다."
            )
        if (
            (unresolved or not _has_explicit_current_lookup_intent(original_message))
            and all(field in _SIMULATION_FIELDS for field in requested_fields)
        ):
            # 실행 불가능한 미완성 변경에서 Qwen이 정책 후보를
            # requested_fields에 중복한 경우 Tool은 unresolved로 차단하고
            # 조회 신호만 폐기한다.
            requested_fields = []
            lookup_all = False
            excluded_scope = False
        if requested_fields or lookup_all or excluded_scope:
            raise ChatProtocolError(
                "시뮬레이션 구조화 출력에 조회 정보가 포함되었습니다."
            )
        if candidate_fields and any(
            field not in _SIMULATION_FIELDS for field in candidate_fields
        ):
            raise ChatProtocolError(
                "시뮬레이션 구조화 출력에 정책 레버가 아닌 후보가 포함되었습니다."
            )
        candidate_fields = []
        if not changes and not unresolved:
            unresolved.extend(
                ("change_field", "change_operation", "change_value")
            )
    elif intent == "lookup":
        if has_field_list_request:
            intent = "field_list"
            resolution = "resolved"
            lookup_all = False
            requested_fields = []
            candidate_fields = []
            changes = []
            assumptions = []
            unresolved = []
            excluded_scope = False
            lookup_evidence = ""
        elif changes:
            raise ChatProtocolError(
                "현재값 조회 구조화 출력에 changes가 포함되었습니다."
            )
        else:
            assumptions = []
            excluded_scope = has_excluded_scope

        if intent == "field_list":
            pass
        elif has_meta_request:
            intent = "unsupported"
            resolution = "unsupported"
            lookup_all = False
            requested_fields = []
            candidate_fields = []
            excluded_scope = False
        elif resolution == "ambiguous":
            lookup_all = False
            requested_fields = []
        elif has_simulation_intent:
            intent = "simulation"
            resolution = "resolved"
            lookup_all = False
            requested_fields = []
            candidate_fields = []
            excluded_scope = False
            unresolved.append("ambiguous_request")
        elif has_excluded_scope:
            lookup_all = False
            if not requested_fields and grounded_fields:
                requested_fields = grounded_fields
            if not requested_fields and not unresolved:
                unresolved.append("lookup_field")
        elif has_safe_explicit_full_lookup:
            lookup_all = True
            requested_fields = []
            candidate_fields = []
        elif has_generic_full_lookup:
            lookup_all = True
            requested_fields = []
            candidate_fields = []
            excluded_scope = False
        else:
            lookup_all = False
            if not requested_fields and grounded_fields:
                # 명시 필드명·label·alias는 Qwen 누락 시에만 고정밀 보조
                # 신호로 사용한다. exact 일치가 없다는 이유로 의미 결과를
                # 거절하거나 덮어쓰지는 않는다.
                requested_fields = grounded_fields
            if not requested_fields:
                intent = "unsupported"
                resolution = "unsupported"
                candidate_fields = []
                lookup_evidence = ""
            elif (
                candidate_fields
                and set(candidate_fields) != set(requested_fields)
            ):
                semantic_candidates = list(
                    dict.fromkeys((*candidate_fields, *requested_fields))
                )
                if len(semantic_candidates) not in {2, 3}:
                    raise ChatProtocolError(
                        "resolved 의미 판정의 후보와 조회 필드가 충돌합니다."
                    )
                resolution = "ambiguous"
                candidate_fields = semantic_candidates
                requested_fields = []
            elif grounded_fields and set(grounded_fields) != set(requested_fields):
                conflict_candidates = list(
                    dict.fromkeys((*grounded_fields, *requested_fields))
                )
                if len(conflict_candidates) not in {2, 3}:
                    raise ChatProtocolError(
                        "명시 조회 필드와 의미 조회 결과가 충돌합니다."
                    )
                resolution = "ambiguous"
                candidate_fields = conflict_candidates
                requested_fields = []
    else:
        if has_field_list_request:
            intent = "field_list"
            resolution = "resolved"
            requested_fields = []
            candidate_fields = []
            changes = []
            assumptions = []
            unresolved = []
            lookup_all = False
            excluded_scope = False
            lookup_evidence = ""
        elif has_meta_request:
            intent = "unsupported"
            resolution = "unsupported"
            requested_fields = []
            candidate_fields = []
            changes = []
            assumptions = []
            unresolved = []
            lookup_all = False
            excluded_scope = False
            lookup_evidence = ""
        elif changes or requested_fields or lookup_all:
            raise ChatProtocolError(
                "지원하지 않는 요청의 구조화 출력에 실행 정보가 포함되었습니다."
            )
        elif has_simulation_intent:
            intent = "simulation"
            resolution = "resolved"
            lookup_all = False
            requested_fields = []
            candidate_fields = []
            excluded_scope = False
            unresolved.append("ambiguous_request")
        elif has_excluded_scope and grounded_fields:
            intent = "lookup"
            resolution = "resolved"
            lookup_all = False
            requested_fields = grounded_fields
            candidate_fields = []
            excluded_scope = True
        elif has_safe_explicit_full_lookup:
            intent = "lookup"
            resolution = "resolved"
            lookup_all = True
            requested_fields = []
            candidate_fields = []
            excluded_scope = False
        elif has_generic_full_lookup:
            intent = "lookup"
            resolution = "resolved"
            lookup_all = True
            requested_fields = []
            candidate_fields = []
            excluded_scope = False
        elif (
            grounded_fields
            and any(
                term in _compact_routing_text(original_message)
                for term in _GENERIC_LOOKUP_REQUEST_TERMS
            )
        ):
            intent = "lookup"
            resolution = "resolved"
            lookup_all = False
            requested_fields = grounded_fields
            candidate_fields = []
            excluded_scope = False
        else:
            excluded_scope = has_excluded_scope

    return _NormalizedRequest(
        intent=intent,
        resolution=resolution,
        candidate_fields=tuple(candidate_fields),
        lookup_evidence=lookup_evidence,
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
        "resolution": None,
        "candidate_fields": [],
        "lookup_evidence": "",
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
                if not isinstance(values, Mapping):
                    continue
                before = values.get("before")
                after = values.get("after")
                if (
                    isinstance(before, Real)
                    and not isinstance(before, bool)
                    and isinstance(after, Real)
                    and not isinstance(after, bool)
                ):
                    before_number = float(before)
                    after_number = float(after)
                    if (
                        math.isfinite(before_number)
                        and math.isfinite(after_number)
                    ):
                        allowed.append(after_number - before_number)
                        if "ratio" in str(feature):
                            allowed.extend(
                                (
                                    before_number * 100,
                                    after_number * 100,
                                    (after_number - before_number) * 100,
                                )
                            )
        confidence = tool_result.get("direction_confidence")
        if (
            isinstance(confidence, Real)
            and not isinstance(confidence, bool)
            and math.isfinite(float(confidence))
        ):
            allowed.append(float(confidence) * 100)
    if tool_name == "rank_policies":
        # 정책별 적용 내역을 %로 인용할 수 있게 환산값도 허용한다.
        # _collect_tool_numbers가 원본 비율은 이미 담았다.
        policies = tool_result.get("policies")
        if isinstance(policies, list):
            for policy in policies:
                if not isinstance(policy, Mapping):
                    continue
                requested_delta = policy.get("delta")
                if (
                    "ratio" in str(policy.get("feature"))
                    and isinstance(requested_delta, Real)
                    and not isinstance(requested_delta, bool)
                    and math.isfinite(float(requested_delta))
                ):
                    allowed.append(float(requested_delta) * 100)
                applied = policy.get("applied")
                if not isinstance(applied, Mapping):
                    continue
                for feature, values in applied.items():
                    if not isinstance(values, Mapping):
                        continue
                    before = values.get("before")
                    after = values.get("after")
                    if (
                        not isinstance(before, Real)
                        or isinstance(before, bool)
                        or not isinstance(after, Real)
                        or isinstance(after, bool)
                    ):
                        continue
                    before_number = float(before)
                    after_number = float(after)
                    if not (
                        math.isfinite(before_number)
                        and math.isfinite(after_number)
                    ):
                        continue
                    allowed.append(after_number - before_number)
                    if "ratio" in str(feature):
                        allowed.extend(
                            (
                                before_number * 100,
                                after_number * 100,
                                (after_number - before_number) * 100,
                            )
                        )
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

    applied_changes = tool_result.get("applied_changes")
    auto_applied_changes = tool_result.get("auto_applied_changes")
    if not isinstance(applied_changes, Mapping) or not isinstance(
        auto_applied_changes,
        Mapping,
    ):
        raise ChatProtocolError("시뮬레이션 실제 적용 내역이 올바르지 않습니다.")
    for raw_field, raw_values in applied_changes.items():
        if not isinstance(raw_field, str) or not isinstance(raw_values, Mapping):
            raise ChatProtocolError(
                "시뮬레이션 실제 적용 내역이 올바르지 않습니다."
            )
        expected_line = _simulation_change_line(
            raw_field,
            raw_values,
            automatic=raw_field in auto_applied_changes,
        )
        if expected_line not in answer:
            raise ChatProtocolError(
                f"실제 적용된 {raw_field} 변경값이 최종 답변에서 누락되었습니다."
            )

    confidence = tool_result.get("direction_confidence")
    if confidence is None:
        if (
            "direction_confidence" not in answer
            or "산정되지 않았" not in answer
        ):
            raise ChatProtocolError(
                "산정되지 않은 direction_confidence 설명이 누락되었습니다."
            )
    elif (
        isinstance(confidence, Real)
        and not isinstance(confidence, bool)
        and math.isfinite(float(confidence))
        and 0.0 <= float(confidence) <= 1.0
    ):
        if (
            "direction_confidence" not in answer
            or not _contains_formatted_number(
                answer,
                float(confidence) * 100,
                decimals=1,
                unit="%",
            )
        ):
            raise ChatProtocolError(
                "direction_confidence 값이 최종 답변에서 누락되거나 다릅니다."
            )
    else:
        raise ChatProtocolError("direction_confidence 반환값이 올바르지 않습니다.")
    if "신뢰구간" not in answer or re.search(
        r"신뢰구간.{0,24}(?:아니|아님|아닙|않)",
        answer,
        re.DOTALL,
    ) is None:
        raise ChatProtocolError(
            "direction_confidence가 신뢰구간이 아니라는 설명이 누락되었습니다."
        )
    if "delta_std" in answer:
        raise ChatProtocolError(
            "사용자 답변에 내부 트리 산포 지표 delta_std가 노출되었습니다."
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
    clip_warnings = [
        str(warning)
        for warning in warnings
        if "학습범위" in str(warning) or "clip" in str(warning).casefold()
    ]
    if clip_warnings and not (
        "학습 범위" in answer
        and ("보정" in answer or "clip" in answer)
        and ("applied_changes" in answer or "실제 반영값" in answer)
    ):
        raise ChatProtocolError(
            "학습 범위 보정과 실제 적용값에 대한 설명이 누락되었습니다."
        )

    _validate_supported_numbers(answer, "run_simulation", tool_result)


def _validate_policy_ranking_answer(
    answer: str,
    tool_result: Mapping[str, Any],
) -> None:
    """정책 순위 답변이 Tool 결과 밖으로 나가지 않았는지 확인한다."""

    _validate_supported_numbers(answer, "rank_policies", tool_result)

    grid_id = tool_result.get("grid_id")
    gu_name = tool_result.get("gu_name")
    if isinstance(grid_id, str) and grid_id and grid_id not in answer:
        raise ChatProtocolError("정책 순위 답변에 격자 ID가 없습니다.")
    if isinstance(gu_name, str) and gu_name and gu_name not in answer:
        raise ChatProtocolError("정책 순위 답변에 구 이름이 없습니다.")

    scenario_note = tool_result.get("scenario_note")
    if isinstance(scenario_note, str) and scenario_note not in answer:
        # 시나리오 크기를 바꾸면 순위가 바뀐다. 크기가 빠진 순위표는 근거 없는 표다.
        raise ChatProtocolError("정책 순위 답변에 시나리오 크기가 없습니다.")

    policies = tool_result.get("policies")
    if not isinstance(policies, list):
        raise ChatProtocolError("정책 순위 Tool 결과에 정책 목록이 없습니다.")
    for policy in policies:
        if not isinstance(policy, Mapping):
            continue
        label = policy.get("label")
        # 상태와 무관하게 정책 4개가 모두 답변에 나와야 한다. 빠지면 사용자는
        # 그 정책을 검토했는지조차 알 수 없다.
        if isinstance(label, str) and label and label not in answer:
            raise ChatProtocolError(
                f"정책 순위 답변에서 정책이 누락되었습니다: {label}"
            )


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


def _simulation_change_line(
    field: str,
    values: Mapping[str, Any],
    *,
    automatic: bool,
) -> str:
    if field not in _SIMULATION_FIELDS:
        raise ChatProtocolError(
            "시뮬레이션 Tool 결과에 지원하지 않는 변경 필드가 있습니다."
        )
    before = values.get("before")
    after = values.get("after")
    if (
        not isinstance(before, Real)
        or isinstance(before, bool)
        or not math.isfinite(float(before))
        or not isinstance(after, Real)
        or isinstance(after, bool)
        or not math.isfinite(float(after))
    ):
        raise ChatProtocolError(
            f"시뮬레이션 Tool 결과의 {field} 적용값이 올바르지 않습니다."
        )

    before_number = float(before)
    after_number = float(after)
    delta = after_number - before_number
    label = str(GRID_FIELD_SPECS[field]["label"])
    if automatic:
        label += "(녹지율 연동 자동 조정)"
    if GRID_FIELD_SPECS[field]["is_ratio"]:
        return (
            f"- {label}: {before_number * 100:.2f}% → "
            f"{after_number * 100:.2f}% ({delta * 100:+.2f}%p)"
        )
    return (
        f"- {label}: {before_number:.4f} → {after_number:.4f} "
        f"({delta:+.4f})"
    )


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

    requested_changes = tool_result.get("requested_changes")
    applied_changes = tool_result.get("applied_changes")
    auto_applied_changes = tool_result.get("auto_applied_changes")
    if (
        not isinstance(requested_changes, Mapping)
        or not isinstance(applied_changes, Mapping)
        or not isinstance(auto_applied_changes, Mapping)
    ):
        raise ChatProtocolError(
            "시뮬레이션 Tool 결과의 변경 내역이 올바르지 않습니다."
        )
    if not set(auto_applied_changes).issubset(applied_changes):
        raise ChatProtocolError(
            "자동 연동 변경 내역이 실제 적용 내역과 일치하지 않습니다."
        )
    lines.append("실제 적용된 변경:")
    if applied_changes:
        for raw_field, raw_values in applied_changes.items():
            if not isinstance(raw_field, str) or not isinstance(raw_values, Mapping):
                raise ChatProtocolError(
                    "시뮬레이션 Tool 결과의 실제 적용 내역이 올바르지 않습니다."
                )
            lines.append(
                _simulation_change_line(
                    raw_field,
                    raw_values,
                    automatic=raw_field in auto_applied_changes,
                )
            )
    else:
        lines.append("- 학습 범위 보정 후 실제 적용된 변경이 없습니다.")

    confidence = tool_result.get("direction_confidence")
    if confidence is None:
        lines.append(
            "모델 트리의 변화 방향 동의율(direction_confidence)은 "
            "산정되지 않았습니다."
        )
    elif (
        isinstance(confidence, Real)
        and not isinstance(confidence, bool)
        and math.isfinite(float(confidence))
        and 0.0 <= float(confidence) <= 1.0
    ):
        confidence_value = float(confidence)
        lines.append(
            "변화가 있었던 모델 트리의 방향 동의율(direction_confidence)은 "
            f"{confidence_value * 100:.1f}%입니다."
        )
        if confidence_value < 0.6:
            lines.append("모델 트리 사이에서 변화 방향을 판단하기 어렵습니다.")
    else:
        raise ChatProtocolError(
            "시뮬레이션 Tool 결과의 direction_confidence가 올바르지 않습니다."
        )
    lines.append(
        "방향 동의율은 통계적 신뢰구간이나 실제 정책의 성공확률이 아닙니다."
    )

    policy_notes = tool_result.get("policy_direction_notes") or []
    warnings = tool_result.get("warnings") or []
    limitations = tool_result.get("limitations") or []
    if not all(isinstance(items, list) for items in (policy_notes, warnings, limitations)):
        raise ChatProtocolError("시뮬레이션 Tool 결과의 안내 목록이 올바르지 않습니다.")

    lines.extend(str(note) for note in policy_notes)
    lines.extend(f"경고: {warning}" for warning in warnings)
    clip_warnings = [
        str(warning)
        for warning in warnings
        if "학습범위" in str(warning) or "clip" in str(warning).casefold()
    ]
    if clip_warnings:
        lines.append(
            "학습 범위 밖 입력은 내부적으로 보정되었으며, 입력값 그대로가 "
            "아니라 적용 결과에 표시된 값이 실제 반영값입니다."
        )
    lines.extend(str(limitation) for limitation in limitations)
    return "\n".join(lines)


# no_room은 여기에 없다. "왜 못 넣었나"가 정책마다 달라서 한 문장으로 묶을 수 없다.
_POLICY_STATE_SUMMARIES = {
    POLICY_STATE_INDISTINGUISHABLE: "차이가 동률 밴드보다 작아 순위를 구분할 수 없습니다",
    POLICY_STATE_UNRESPONSIVE: "모델이 반응하지 않아 판단할 수 없습니다",
    POLICY_STATE_ADVERSE: "예상 변화가 온도 상승 방향이라 추천하지 않습니다",
}


def _policy_applied_text(policy: Mapping[str, Any]) -> str:
    """정책 하나의 실제 적용 내역을 사람이 읽을 수 있게 만든다."""

    applied = policy.get("applied")
    if not isinstance(applied, Mapping) or not applied:
        return "실제로 반영된 변경이 없습니다"
    parts: list[str] = []
    for feature, values in applied.items():
        if not isinstance(values, Mapping):
            continue
        before = values.get("before")
        after = values.get("after")
        if (
            not isinstance(before, Real)
            or isinstance(before, bool)
            or not isinstance(after, Real)
            or isinstance(after, bool)
        ):
            continue
        spec = GRID_FIELD_SPECS.get(str(feature), {})
        label = str(spec.get("label") or feature)
        if spec.get("is_ratio") is True:
            parts.append(
                f"{label} {float(before) * 100:.2f}% → {float(after) * 100:.2f}%"
            )
        else:
            parts.append(f"{label} {float(before):.4f} → {float(after):.4f}")
    return ", ".join(parts) if parts else "실제로 반영된 변경이 없습니다"


def _policy_no_room_line(policy: Mapping[str, Any]) -> str:
    """★ '못 넣었다'와 '효과가 없다'를 구분해서 말한다.

    막힌 이유가 두 가지인데 사용자에게는 전혀 다른 뜻이다.
    - direct  : 그 지표 자체를 요청한 만큼 넣을 자리가 없다
    - coupled : 지표는 다 들어갔고, 연동돼 따라가야 할 불투수면이 하한에 걸렸다
    둘을 뭉뚱그리면 녹지가 전부 반영된 격자에도 "녹지를 못 넣었다"고 답하게 된다.
    """

    label = str(policy.get("label"))
    applied_text = _policy_applied_text(policy)
    reason = policy.get("clip_reason")
    if reason == "coupled":
        head = (
            f"{label}: 요청한 만큼 반영됐지만 연동된 불투수면이 하한에 걸려 "
            "함께 내려가지 못했습니다"
        )
    else:
        head = f"{label}: 이 격자엔 요청한 만큼 시행할 여지가 없습니다"

    delta_c = policy.get("delta_c")
    tail = ""
    if (
        isinstance(delta_c, Real)
        and not isinstance(delta_c, bool)
        and math.isfinite(float(delta_c))
    ):
        # 부분 적용만으로도 변화량이 클 수 있다. 숨기면 "효과 없음"으로 읽힌다.
        tail = (
            f" 실제 적용 결과의 예상 변화량은 {float(delta_c):.3f}℃지만, "
            "요청과 다른 크기라 순위에서 제외했습니다."
        )
    return f"{head} ({applied_text}).{tail}"


def format_policy_ranking_answer(tool_result: Mapping[str, Any]) -> str:
    """정책 순위 Tool 결과만 사용해 결정적인 한국어 답변을 만든다."""

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
        raise ChatProtocolError("정책 순위 Tool 결과의 지역 정보가 올바르지 않습니다.")

    scenario_note = tool_result.get("scenario_note")
    if not isinstance(scenario_note, str) or not scenario_note:
        raise ChatProtocolError(
            "정책 순위 Tool 결과의 시나리오 정보가 올바르지 않습니다."
        )
    tie_band = tool_result.get("tie_band_c")
    if (
        not isinstance(tie_band, Real)
        or isinstance(tie_band, bool)
        or not math.isfinite(float(tie_band))
    ):
        raise ChatProtocolError("정책 순위 Tool 결과의 동률 밴드가 올바르지 않습니다.")

    policies = tool_result.get("policies")
    if not isinstance(policies, list) or not policies:
        raise ChatProtocolError("정책 순위 Tool 결과에 정책 목록이 없습니다.")

    grouped: dict[str, list[str]] = {}
    ranked: list[tuple[int, str, float]] = []
    no_room: list[Mapping[str, Any]] = []
    for policy in policies:
        if not isinstance(policy, Mapping):
            raise ChatProtocolError(
                "정책 순위 Tool 결과의 정책 항목이 올바르지 않습니다."
            )
        label = policy.get("label")
        state = policy.get("state")
        if not isinstance(label, str) or not label or not isinstance(state, str):
            raise ChatProtocolError(
                "정책 순위 Tool 결과의 정책 이름 또는 상태가 올바르지 않습니다."
            )
        if state == POLICY_STATE_RANKED:
            rank = policy.get("rank")
            delta_c = policy.get("delta_c")
            if (
                not isinstance(rank, int)
                or isinstance(rank, bool)
                or rank < 1
                or not isinstance(delta_c, Real)
                or isinstance(delta_c, bool)
                or not math.isfinite(float(delta_c))
            ):
                raise ChatProtocolError(
                    "순위에 오른 정책의 순위 또는 예상 변화량이 올바르지 않습니다."
                )
            ranked.append((rank, label, float(delta_c)))
            continue
        if state == POLICY_STATE_NO_ROOM:
            no_room.append(policy)
            continue
        if state not in _POLICY_STATE_SUMMARIES:
            raise ChatProtocolError(f"정책 순위 Tool 결과의 상태가 미지원입니다: {state}")
        grouped.setdefault(state, []).append(label)
    ranked.sort(key=lambda item: item[0])

    lines = [f"{grid_id} 격자({gu_name})의 정책 우선순위입니다. ({scenario_note})"]
    if ranked:
        for rank, label, delta_c in ranked:
            lines.append(f"{rank}위 {label}: {delta_c:.3f}℃")
    else:
        lines.append("순위를 매길 수 있는 정책이 없습니다.")
    # ★ 나머지를 상태별로 나눠 말한다. "효과 없음"과 "시행 여지 없음"을 한 문장으로
    # 묶으면 정책 담당자에게 정반대 뜻으로 전달된다.
    for policy in no_room:
        lines.append(_policy_no_room_line(policy))
    for state, summary in _POLICY_STATE_SUMMARIES.items():
        labels = grouped.get(state)
        if labels:
            lines.append(f"{' · '.join(labels)}: {summary}.")
    lines.append(
        f"동률 밴드는 {float(tie_band):.3f}℃이며, 이보다 작은 차이는 "
        "모델 추정오차 안이라 순위로 구분하지 않습니다."
    )
    lines.append(
        "표시된 값은 절대온도가 아니라 두 모델 예측의 차이인 "
        "모델 기준 예상 변화량입니다."
    )

    limitations = tool_result.get("limitations")
    if isinstance(limitations, list):
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
    if tool_name == "rank_policies":
        return format_policy_ranking_answer(tool_result)
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
    elif tool_name == "rank_policies":
        _validate_policy_ranking_answer(answer, tool_result)
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


def _field_clarification_answer(candidate_fields: tuple[str, ...]) -> str:
    lines = ["어느 데이터를 확인할까요?"]
    for field in candidate_fields:
        spec = GRID_FIELD_SPECS[field]
        definition = str(
            spec.get("semantic_definition") or spec["description"]
        )
        lines.append(f"- {spec['label']}: {definition}")
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
        if change.field in {"ndvi", "albedo"}:
            normalized_change = f"{value} {direction}"
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
        "ndvi_delta": 0.0,
        "albedo_delta": 0.0,
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
        if change.unit == "unitless":
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
            "resolution": normalized_request.resolution,
            "candidate_fields": list(normalized_request.candidate_fields),
            "lookup_evidence": normalized_request.lookup_evidence,
            "lookup_all": normalized_request.lookup_all,
            "requested_fields": list(normalized_request.requested_fields),
            "excluded_scope": normalized_request.excluded_scope,
            "validation_result": "accepted",
        }
    )

    if normalized_request.resolution == "ambiguous":
        metrics["final_branch"] = "field_clarification"
        return _supported_scope_result(
            answer=_field_clarification_answer(
                normalized_request.candidate_fields
            ),
            first_thinking=first_thinking,
            first_content=first_content,
            metrics=metrics,
        )
    if normalized_request.unresolved:
        metrics["final_branch"] = "clarification"
        return _supported_scope_result(
            answer=_clarification_answer(normalized_request.unresolved),
            first_thinking=first_thinking,
            first_content=first_content,
            metrics=metrics,
        )
    if normalized_request.intent == "field_list":
        metrics["final_branch"] = "field_list"
        return _supported_scope_result(
            answer=FIELD_LIST_ANSWER,
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
    # 위에서 _TOOLLESS_INTENTS를 모두 걸러냈고, 남은 intent에 Tool이 있다는 건 import 시점에
    # 검증했다. 그래서 직접 조회한다 — 표가 깨지면 서비스가 뜨기 전에 터진다.
    tool_name = _INTENT_TOOL[normalized_request.intent]
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
        elif normalized_request.intent == "policy_ranking":
            # rank_policies는 grid_id만 받는다. 시나리오 크기는 Tool 안에 있다.
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
