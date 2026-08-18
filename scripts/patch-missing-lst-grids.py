"""위성 관측(lst_mean)이 없어 대시보드 격자 파일에서 빠진 격자를 되살린다.

ML 원본(``backend/data/processed/seoul_grid_dataset.csv``)에는 64,676개 격자가
있는데 대시보드 격자 파일에는 64,574개뿐이다. 빠진 102개는 정확히 ``lst_mean``
(위성 지표면온도)이 빈 격자들이다. 이 102개는 lst_mean 하나만 비어 있고
건물비율·NDVI·알베도 등 나머지 41개 값은 모두 정상인데, 파이프라인이 행 자체를
떨어뜨려서 지도에 구멍이 뚫린다(예: 코엑스 옆 11680_03105).

**원래 고쳐야 할 곳은 ML 파이프라인이다.** 도형을 갖고 있는 쪽은 그쪽이고,
온도 파생값만 비워서 내보내면 이 스크립트가 필요 없다. 여기서는 그때까지
쓸 임시 조치로, 자리를 정확히 계산할 수 있는 격자만 되살린다.

자리 계산 — 격자 번호는 한 줄을 따라 1씩 늘고 그 간격이 정확히 100m다.
빠진 번호의 좌우 이웃이 같은 줄에 200m 간격으로 있으면, 그 사이가 빠진 칸이다.
이 조건을 만족하지 않으면(줄 끝이라 좌우 이웃이 다른 줄에 있으면) 건너뛴다 —
자리를 추정해서 그리면 지도에 없는 칸을 지어내는 셈이다.

온도 관련 값(mean_actual_lst·anomaly·priority_score 등)은 넣지 않는다. 없는 값이라
화면에서 '데이터 불완전 격자'로 표시된다.

⚠️ 격자 파일은 ML 쪽 dashboard.zip 에서 오므로, 데이터를 새로 받은 뒤에는
이 스크립트를 다시 돌려야 한다.

실행: python3 scripts/patch-missing-lst-grids.py
"""

from __future__ import annotations

import csv
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATASET = ROOT / "backend" / "data" / "processed" / "seoul_grid_dataset.csv"
DASHBOARD = ROOT / "public" / "dashboard"
MAP_FILE = DASHBOARD / "seoul_grid_100m_map.geojson"

# 지도용 파일에 담기는 속성 (generate-100m-map-data.mjs 의 MAP_PROPERTY_KEYS 와 같다)
MAP_KEYS = [
    "grid_id", "gu_code", "gu_name", "area_m2", "priority_score",
    "mean_actual_anomaly", "mean_actual_lst", "green_delta_c", "green_ratio",
    "ndvi", "building_ratio", "impervious_ratio", "nearest_shelter_distance_m",
]

# CSV 열 → geojson 속성. 온도·순위·인구·쉼터 값은 CSV 에 없으므로 넣지 않는다.
NUMERIC_COLUMNS = {
    "building_ratio": "building_ratio",
    "road_ratio": "road_ratio",
    "floor_area_ratio_proxy": "floor_area_ratio_proxy",
    "ndvi": "ndvi",
    "green_ratio": "green_ratio",
    "impervious_ratio": "impervious_ratio",
    "zoning_residential_ratio": "zoning_residential_ratio",
    "zoning_commercial_ratio": "zoning_commercial_ratio",
    "zoning_industrial_ratio": "zoning_industrial_ratio",
    "zoning_green_ratio": "zoning_green_ratio",
    "nearest_park_distance_m": "nearest_park_distance_m",
    "park_area_within_500m": "park_area_within_500m",
    "nearest_stream_distance_m": "nearest_stream_distance_m",
    "elevation_m": "elevation_m",
    "slope_deg": "slope_deg",
    "albedo": "albedo",
    "avg_ground_floor_count": "avg_ground_floor_count",
    "max_ground_floor_count": "max_ground_floor_count",
    "area_m2_y": "area_m2",
}

LATITUDE_METERS = 110540
LONGITUDE_METERS = 111320 * 0.79   # 서울 위도에서의 경도 1도 길이


def ring_of(feature: dict) -> list:
    geometry = feature["geometry"]
    if geometry["type"] == "Polygon":
        return geometry["coordinates"][0]
    return geometry["coordinates"][0][0]


def centroid(feature: dict) -> tuple[float, float]:
    ring = ring_of(feature)
    return (
        sum(p[0] for p in ring) / len(ring),
        sum(p[1] for p in ring) / len(ring),
    )


def read_missing_rows() -> dict[str, dict]:
    """lst_mean 이 빈 격자의 CSV 행."""
    rows = {}
    with DATASET.open(encoding="utf-8-sig") as handle:
        for row in csv.DictReader(handle):
            if row["lst_mean"] in ("", "NA", "nan", "NaN"):
                rows[row["grid_id"]] = row
    return rows


def build_properties(row: dict) -> dict:
    properties = {
        "grid_id": row["grid_id"],
        "gu_code": int(row["gu_code"]),
        "gu_name": row["gu_name"],
    }
    for column, key in NUMERIC_COLUMNS.items():
        value = row.get(column, "")
        if value in ("", "NA", "nan", "NaN"):
            continue
        try:
            properties[key] = float(value)
        except ValueError:
            continue
    if row.get("building_ratio_estimated"):
        properties["building_ratio_estimated"] = row["building_ratio_estimated"] == "True"
    # 화면이 이유를 설명할 수 있게 남긴다. 온도 값이 없는 것은 관측이 없어서다.
    properties["lst_observed"] = False
    return properties


def shifted_ring(left: dict, right: dict) -> list:
    """좌우 이웃 사이의 빈 칸. 왼쪽 칸을 중심 간격의 절반만큼 동쪽으로 민다."""
    cl, cr = centroid(left), centroid(right)
    dx = (cr[0] - cl[0]) / 2
    dy = (cr[1] - cl[1]) / 2
    return [[round(x + dx, 9), round(y + dy, 9)] for x, y in ring_of(left)]


def insert_features(path: Path, added: list[dict]) -> None:
    """원본 줄을 건드리지 않고 새 격자 줄만 끼워 넣는다.

    파일을 통째로 다시 쓰면 서식(`{ "type": ... }` 처럼 중괄호 안 공백까지)이 달라져
    4,169줄이 전부 바뀐 것으로 보인다. 격자 2개를 더한 변경이 diff 에서 전체 교체가
    되어 리뷰가 불가능해진다. 그래서 텍스트로 끼워 넣는다.

    빠진 격자는 항상 좌우 이웃이 있으므로(그게 되살리는 조건이다) 파일 중간에만
    들어간다 — 마지막 줄의 쉼표를 걱정할 필요가 없다.
    """
    lines = path.read_text().splitlines()
    for feature in sorted(added, key=lambda f: f["properties"]["grid_id"], reverse=True):
        grid_id = feature["properties"]["grid_id"]
        code, number = grid_id.split("_")
        previous = f'"grid_id": "{code}_{int(number) - 1:05d}"'
        for index, line in enumerate(lines):
            if previous in line:
                lines.insert(index + 1, json.dumps(feature, ensure_ascii=False) + ",")
                break
        else:
            raise RuntimeError(f"{grid_id}: 앞 격자 줄을 찾지 못했다")
    path.write_text("\n".join(lines) + "\n")


def patch_district(path: Path, rows: dict[str, dict]) -> list[dict]:
    data = json.loads(path.read_text())
    by_id = {f["properties"]["grid_id"]: f for f in data["features"]}
    added = []

    for grid_id, row in rows.items():
        if not grid_id.startswith(f"{row['gu_code']}_") or grid_id in by_id:
            continue
        code, number = grid_id.split("_")
        n = int(number)
        left = by_id.get(f"{code}_{n - 1:05d}")
        right = by_id.get(f"{code}_{n + 1:05d}")
        if not left or not right:
            continue

        cl, cr = centroid(left), centroid(right)
        if abs(cl[1] - cr[1]) * LATITUDE_METERS >= 5:
            continue   # 다른 줄이다
        gap = (cr[0] - cl[0]) * LONGITUDE_METERS
        if not 190 < gap < 210:
            continue   # 사이가 한 칸이 아니다

        feature = {
            "type": "Feature",
            "properties": build_properties(row),
            "geometry": {"type": "Polygon", "coordinates": [shifted_ring(left, right)]},
        }
        added.append(feature)

    if not added:
        return []

    insert_features(path, added)
    return added


def patch_map_file(added: list[dict]) -> None:
    data = json.loads(MAP_FILE.read_text())
    existing = {f["properties"].get("grid_id") for f in data["features"]}
    fresh = [
        {
            "type": "Feature",
            "properties": {
                k: v for k, v in f["properties"].items() if k in MAP_KEYS or k == "lst_observed"
            },
            "geometry": f["geometry"],
        }
        for f in added
        if f["properties"]["grid_id"] not in existing
    ]
    if not fresh:
        return
    data["features"].extend(fresh)
    MAP_FILE.write_text(json.dumps(data, ensure_ascii=False))
    print(f"지도용 파일에 {len(fresh)}개 추가")


def main() -> None:
    rows = read_missing_rows()
    print(f"lst_mean 이 빈 격자: {len(rows)}개")

    added_all = []
    for path in sorted(DASHBOARD.glob("100m/*.geojson")):
        code = path.name.split("_")[0]
        subset = {g: r for g, r in rows.items() if r["gu_code"] == code}
        if not subset:
            continue
        added = patch_district(path, subset)
        if added:
            names = ", ".join(f["properties"]["grid_id"] for f in added)
            print(f"  {path.name}: {len(added)}개 되살림 ({names})")
            added_all.extend(added)

    patch_map_file(added_all)
    skipped = len(rows) - len(added_all)
    print(f"\n되살림 {len(added_all)}개 · 건너뜀 {skipped}개")
    if skipped:
        print("건너뛴 격자는 좌우 이웃이 다른 줄에 있어 자리를 정확히 계산할 수 없다.")
        print("ML 파이프라인이 도형을 내보내 주어야 한다.")


if __name__ == "__main__":
    main()
