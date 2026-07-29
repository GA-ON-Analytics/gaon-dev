"""LLM/프론트가 '무엇을 바꿀 수 있나'를 알도록 변수 메타데이터를 생성한다.

LLM이 사용자 질문("나무 심으면?")을 변수 변화({ndvi:+0.1, green_ratio:+0.2})로 번역하려면
각 변수가 무엇인지·범위·방향(온도 올림/내림)·자연어 별칭을 알아야 한다. 이 파일이 그 사전이다.

산출: models/feature_meta.json
"""
from __future__ import annotations

import json

try:
    from .predict_core import _load, FEATURE_META_PATH, RATIO_FEATURES
except ImportError:
    from predict_core import _load, FEATURE_META_PATH, RATIO_FEATURES

# 변수별: 한글명, 설명, 자연어 별칭(LLM 매칭용), 온도 방향(+올림/-내림), 조절 가능성
META = {
    "building_ratio": ("건물 바닥면적 비율", "격자에서 건물이 땅을 덮은 비율",
                       ["건물", "건물밀도", "건폐율", "건물 줄이기"], "+", "policy"),
    "avg_ground_floor_count": ("평균 지상층수", "건물들의 평균 층수",
                               ["층수", "높이", "고층", "저층"], "-", "policy"),
    "max_ground_floor_count": ("최대 지상층수", "가장 높은 건물의 층수",
                               ["최고층", "최대높이"], "-", "policy"),
    "floor_area_ratio_proxy": ("연면적비 proxy", "건물 총량 추정(용적률 유사)",
                               ["용적률", "연면적", "개발밀도"], "+", "policy"),
    "road_ratio": ("도로율", "격자에서 도로가 차지하는 비율",
                   ["도로", "포장도로"], "+", "policy"),
    "zoning_residential_ratio": ("주거지역 비율", "주거 용도지역 비율",
                                 ["주거지역", "주거"], "0", "fixed"),
    "zoning_commercial_ratio": ("상업지역 비율", "상업 용도지역 비율",
                                ["상업지역", "상업"], "+", "fixed"),
    "zoning_industrial_ratio": ("공업지역 비율", "공업 용도지역 비율",
                                ["공업지역", "공장"], "+", "fixed"),
    "zoning_green_ratio": ("녹지지역 비율", "용도지역상 녹지 비율",
                           ["녹지지역", "그린벨트"], "-", "policy"),
    "ndvi": ("식생지수", "위성이 본 식물의 푸르름(나무·풀의 양/건강)",
             ["나무", "식생", "수목", "가로수", "나무 심기", "녹화"], "-", "policy"),
    "green_ratio": ("녹지율", "녹지성 토지피복 비율(공원·잔디 등)",
                    ["녹지", "공원", "잔디", "녹지 확대"], "-", "policy"),
    # 위성(Dynamic World)의 인공 구조물 확률 기반이라 엄밀한 불투수면과는 다르다.
    # 예전에 있던 built_surface_ratio는 이 컬럼과 값이 100% 동일한 중복이라 제거했다.
    "impervious_ratio": ("불투수면 비율", "물이 스미지 않는 포장면·인공표면 비율",
                         ["불투수면", "포장면", "아스팔트", "콘크리트", "투수포장",
                          "시가화", "인공표면"], "+", "policy"),
    "nearest_park_distance_m": ("최근접 공원거리(m)", "가장 가까운 공원까지 거리",
                                ["공원 거리", "공원 접근성"], "+", "derived"),
    "park_area_within_500m": ("500m내 공원면적(㎡)", "반경 500m 안 공원 총면적",
                              ["주변 공원", "공원 면적"], "-", "derived"),
    "nearest_stream_distance_m": ("최근접 하천거리(m)", "가장 가까운 하천까지 거리",
                                  ["하천 거리", "물가"], "+", "derived"),
    "elevation_m": ("표고(m)", "해발 고도", ["고도", "표고", "산"], "-", "fixed"),
    "slope_deg": ("경사(도)", "지형 경사", ["경사", "비탈"], "-", "fixed"),
    "albedo": ("표면 반사율", "표면이 빛을 반사하는 정도(밝을수록 덜 뜨거움)",
               ["반사율", "밝은 지붕", "차열도장", "쿨루프", "밝은 표면"], "-", "policy"),
}


def main() -> None:
    _, feats, _, ranges = _load()
    out = []
    for f in feats:
        name, desc, aliases, direction, editable = META.get(f, (f, "", [], "0", "unknown"))
        lo, hi = ranges[f]
        out.append({
            "name": f,
            "korean": name,
            "description": desc,
            "aliases": aliases,           # LLM이 자연어 -> 변수 매칭에 사용
            "temp_direction": direction,  # + 올림 / - 내림 / 0 중립
            "editable": editable,         # policy=정책개입가능, derived=간접, fixed=고정(지형/용도)
            "min": round(lo, 4),
            "max": round(hi, 4),
            "is_ratio": f in RATIO_FEATURES,
        })
    FEATURE_META_PATH.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"saved {FEATURE_META_PATH}  ({len(out)} features)")
    print("정책 개입 가능 변수:", [x["name"] for x in out if x["editable"] == "policy"])


if __name__ == "__main__":
    main()
