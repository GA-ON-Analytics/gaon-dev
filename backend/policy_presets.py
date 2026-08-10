"""GA:ON 정책 프리셋 정의. 화면과 챗봇이 함께 쓰는 원본이다.

예전에는 이 정의가 ``src/config/policyPresets.ts``에만 있었다. 화면은 잘
돌아갔지만 챗봇은 백엔드에서 돌아서 정책 이름을 읽을 수 없었고, "쿨루프
적용하면?"에 변화량을 몰라 되물었다.

복제하지 않고 여기로 옮긴 이유는 ``predict_core.py``가 두 레포에 갈라져
같은 로직을 두 번 고쳐야 하는 상황을 이미 겪었기 때문이다. 정의는 여기
하나만 두고 화면은 ``/api/policies``로 받아 간다.

``aliases``는 챗봇 전용이다. 화면은 목록에서 골라 누르지만 사람은
"옥상정원", "지붕 하얗게" 같은 말로 묻는다. 정책 이름과 다른 표현을
여기 모아 둔다.

각 정책의 ``source_url``은 서울시 보도자료다. 근거 없는 시나리오를 넣지
않기 위해 필수로 둔다.
"""

from __future__ import annotations

from typing import Any


STANDARD_SCENARIO = "100m 격자 기준 표준 시나리오 · 격자 내 10% 수준 개입"

# 정책이 건드릴 수 있는 지표와 화면 표기. 모델 입력 필드명과 같아야 한다.
POLICY_FEATURE_LABELS: dict[str, str] = {
    "green_ratio": "녹지율",
    "impervious_ratio": "인공표면 비율",
    "road_ratio": "도로 비율",
    "ndvi": "식생지수",
    "albedo": "표면 반사율",
    "building_ratio": "건물면적 비율",
}

POLICY_PRESETS: tuple[dict[str, Any], ...] = (
    {
        "id": "paved_to_green",
        "name": "포장공간 녹지화",
        "description": "100m 격자 내 포장공간 10%p를 녹지로 전환",
        "changes": {
            "green_ratio": 0.1,
            "impervious_ratio": -0.1,
            "ndvi": 0.05,
        },
        "affected_features": ("green_ratio", "impervious_ratio", "ndvi"),
        "assumptions": (
            "영향 비율 0.10",
            "기존 포장면 NDVI 0.00, 전환 후 녹지 NDVI 0.50으로 가정",
        ),
        "source_url": "https://news.seoul.go.kr/env/archives/561644",
        "scenario_label": STANDARD_SCENARIO,
        "minimum_requirements": (
            {
                "feature": "impervious_ratio",
                "minimum": 0.1,
                "unavailable_message": (
                    "현재 격자에는 표준 정책 시나리오를 적용할 충분한 "
                    "포장공간이 없습니다."
                ),
            },
        ),
        "aliases": ("포장공간 녹지화", "포장면 녹지화", "아스팔트 녹지화", "포장 녹지"),
    },
    {
        "id": "road_to_green",
        "name": "도로 가로녹지화",
        "description": "100m 격자 내 도로공간 10%p를 녹지로 전환",
        "changes": {
            "road_ratio": -0.1,
            "green_ratio": 0.1,
            "impervious_ratio": -0.1,
            "ndvi": 0.05,
        },
        "affected_features": (
            "road_ratio",
            "green_ratio",
            "impervious_ratio",
            "ndvi",
        ),
        "assumptions": (
            "영향 비율 0.10",
            "기존 도로 NDVI 0.00, 전환 후 가로녹지 NDVI 0.50으로 가정",
        ),
        "source_url": "https://news.seoul.go.kr/traffic/?p=505617",
        "scenario_label": STANDARD_SCENARIO,
        "minimum_requirements": (
            {
                "feature": "road_ratio",
                "minimum": 0.1,
                "unavailable_message": (
                    "현재 격자에는 표준 정책 시나리오를 적용할 충분한 "
                    "도로공간이 없습니다."
                ),
            },
            {
                "feature": "impervious_ratio",
                "minimum": 0.1,
                "unavailable_message": (
                    "현재 격자에는 표준 정책 시나리오를 적용할 충분한 "
                    "인공표면이 없습니다."
                ),
            },
        ),
        "aliases": ("도로 가로녹지화", "가로녹지", "가로수", "도로 녹지화"),
    },
    {
        "id": "vegetation_improvement",
        "name": "기존 녹지 개선",
        "description": "100m 격자 내 기존 녹지 일부의 식생 활력 및 수관 개선",
        "changes": {"ndvi": 0.02},
        "affected_features": ("ndvi",),
        "assumptions": (
            "영향 비율 0.10",
            "기존 식생 NDVI 0.35, 개선 후 NDVI 0.55로 가정",
            "녹지 면적 자체는 늘어나지 않는 시나리오",
        ),
        "source_url": "https://news.seoul.go.kr/env/archives/567149",
        "scenario_label": STANDARD_SCENARIO,
        "minimum_requirements": (
            {
                "feature": "green_ratio",
                "minimum": 0.1,
                "unavailable_message": (
                    "현재 격자에는 표준 식생 개선 시나리오를 적용할 충분한 "
                    "기존 녹지가 없습니다."
                ),
            },
        ),
        "aliases": ("기존 녹지 개선", "녹지 개선", "식생 개선", "수관 개선"),
    },
    {
        "id": "small_park",
        "name": "소규모 공원·정원 조성",
        "description": "100m 격자 내 포장·유휴공간 10%p를 공원 또는 정원으로 전환",
        "changes": {
            "green_ratio": 0.1,
            "impervious_ratio": -0.1,
            "ndvi": 0.05,
        },
        "affected_features": ("green_ratio", "impervious_ratio", "ndvi"),
        "assumptions": (
            "영향 비율 0.10",
            "기존 포장면 NDVI 0.00, 전환 후 녹지 NDVI 0.50으로 가정",
            "v1에서는 공원 거리와 반경 500m 공원면적을 재계산하지 않음",
        ),
        "source_url": "https://news.seoul.go.kr/env/archives/511578",
        "scenario_label": STANDARD_SCENARIO,
        "minimum_requirements": (
            {
                "feature": "impervious_ratio",
                "minimum": 0.1,
                "unavailable_message": (
                    "현재 격자에는 표준 공원·정원 시나리오를 적용할 충분한 "
                    "포장·유휴공간이 없습니다."
                ),
            },
        ),
        "aliases": ("소규모 공원", "공원 조성", "쌈지공원", "정원 조성", "소공원"),
    },
    {
        "id": "green_roof",
        "name": "옥상녹화",
        "description": "100m 격자 내부 건물 옥상에 표준 수준의 녹화 적용",
        "changes": {"ndvi": 0.03},
        "affected_features": ("ndvi",),
        "assumptions": (
            "영향 비율 0.10",
            "기존 비식생 옥상 NDVI 0.00, 녹화 후 NDVI 0.30으로 가정",
            "건물면적 비율은 실제 사용 가능한 옥상면적과 같지 않음",
        ),
        "source_url": "https://news.seoul.go.kr/env/archives/564339",
        "scenario_label": STANDARD_SCENARIO,
        "minimum_requirements": (
            {
                "feature": "building_ratio",
                "minimum": 0.1,
                "unavailable_message": (
                    "현재 격자의 건물면적 비율이 표준 옥상녹화 시나리오 "
                    "기준보다 작습니다."
                ),
            },
        ),
        "aliases": ("옥상녹화", "옥상 녹화", "옥상정원", "루프가든", "지붕 녹화"),
    },
    {
        "id": "cool_roof",
        "name": "쿨루프",
        "description": "100m 격자 내 지붕 10% 수준에 고반사 소재 적용",
        "changes": {"albedo": 0.04},
        "affected_features": ("albedo",),
        "assumptions": (
            "영향 비율 0.10",
            "기존 지붕 albedo 0.20, 쿨루프 albedo 0.60으로 가정",
            "건물면적 비율은 실제 시공 가능한 지붕면적과 같지 않음",
        ),
        "source_url": "https://news.seoul.go.kr/env/archives/43080",
        "scenario_label": STANDARD_SCENARIO,
        "minimum_requirements": (
            {
                "feature": "building_ratio",
                "minimum": 0.1,
                "unavailable_message": (
                    "현재 격자의 건물면적 비율이 표준 쿨루프 시나리오 "
                    "기준보다 작습니다."
                ),
            },
        ),
        "aliases": ("쿨루프", "차열도장", "고반사 지붕", "흰 지붕", "밝은 지붕"),
    },
)

POLICY_PRESET_BY_ID: dict[str, dict[str, Any]] = {
    preset["id"]: preset for preset in POLICY_PRESETS
}

# 정의가 어긋나면 서비스가 뜨기 전에 터뜨린다. 화면과 챗봇이 같은 표를
# 쓰기 때문에, 여기서 조용히 어긋나면 두 곳이 다른 정책을 말하게 된다.
for _preset in POLICY_PRESETS:
    _unknown = set(_preset["affected_features"]) - POLICY_FEATURE_LABELS.keys()
    if _unknown:
        raise RuntimeError(
            f"{_preset['id']} 정책이 모르는 지표를 가리킵니다: {sorted(_unknown)}"
        )
    if set(_preset["changes"]) != set(_preset["affected_features"]):
        raise RuntimeError(
            f"{_preset['id']} 정책의 changes와 affected_features가 어긋납니다."
        )
    if not _preset["source_url"].startswith("https://"):
        raise RuntimeError(f"{_preset['id']} 정책에 근거 링크가 없습니다.")
if len(POLICY_PRESET_BY_ID) != len(POLICY_PRESETS):
    raise RuntimeError("정책 id가 중복되었습니다.")


def _compact(text: str) -> str:
    return "".join(text.split()).lower()


def find_policy_by_text(text: str) -> dict[str, Any] | None:
    """사람이 쓴 문장에서 정책 하나를 찾는다. 못 찾으면 None.

    긴 별칭부터 본다. "옥상녹화"와 "옥상"이 함께 있을 때 짧은 쪽이 먼저
    걸리면 엉뚱한 정책이 잡힌다.
    """

    if not isinstance(text, str) or not text.strip():
        return None
    compact_text = _compact(text)
    matches: list[tuple[int, dict[str, Any]]] = []
    for preset in POLICY_PRESETS:
        for alias in (preset["name"], *preset["aliases"]):
            compact_alias = _compact(alias)
            if compact_alias and compact_alias in compact_text:
                matches.append((len(compact_alias), preset))
                break
    if not matches:
        return None
    matches.sort(key=lambda item: item[0], reverse=True)
    return matches[0][1]


def policy_presets_payload() -> list[dict[str, Any]]:
    """API로 내보낼 모양. 화면이 쓰지 않는 aliases는 빼지 않고 그대로 둔다.

    화면이 지금 쓰지 않더라도 같은 정의를 보는 편이 낫다. 나중에 검색창을
    붙일 때 별칭을 다시 만들 이유가 없다.
    """

    return [
        {
            "id": preset["id"],
            "name": preset["name"],
            "description": preset["description"],
            "changes": dict(preset["changes"]),
            "affectedFeatures": list(preset["affected_features"]),
            "assumptions": list(preset["assumptions"]),
            "sourceUrl": preset["source_url"],
            "scenarioLabel": preset["scenario_label"],
            "minimumRequirements": [
                {
                    "feature": requirement["feature"],
                    "minimum": requirement["minimum"],
                    "unavailableMessage": requirement["unavailable_message"],
                }
                for requirement in preset["minimum_requirements"]
            ],
            "aliases": list(preset["aliases"]),
        }
        for preset in POLICY_PRESETS
    ]
