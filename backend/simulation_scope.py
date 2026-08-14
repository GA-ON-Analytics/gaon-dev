"""서울 100m 격자의 정책 적용 범위를 geometry로 찾는다.

``grid_id``는 자치구별 일련번호라 행/열 위치를 복원할 수 없다. 따라서 서울 전체
100m GeoJSON의 실제 geometry 중심을 meter 좌표로 바꿔 중심 격자 주변을 조회한다.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any


EARTH_RADIUS_M = 6_371_008.8
SEOUL_REFERENCE_LATITUDE = 37.5665
ALLOWED_SCOPE_METERS = (100, 300, 500)


@dataclass(frozen=True)
class GridCell:
    grid_id: str
    gu_code: str
    area_m2: float
    longitude: float
    latitude: float
    x_m: float
    y_m: float


@dataclass(frozen=True)
class AggregateGridCell:
    display_grid_id: str
    gu_code: str
    geometry: dict[str, Any]
    member_grid_ids: tuple[str, ...]


def _ring_centroid(ring: list[list[float]]) -> tuple[float, float, float] | None:
    if len(ring) < 3 or len(ring[0]) < 2:
        return None

    origin_longitude = float(ring[0][0])
    origin_latitude = float(ring[0][1])
    cross_sum = 0.0
    longitude_sum = 0.0
    latitude_sum = 0.0
    for first, second in zip(ring, ring[1:] + ring[:1]):
        if len(first) < 2 or len(second) < 2:
            continue
        first_longitude = float(first[0]) - origin_longitude
        first_latitude = float(first[1]) - origin_latitude
        second_longitude = float(second[0]) - origin_longitude
        second_latitude = float(second[1]) - origin_latitude
        cross = (
            first_longitude * second_latitude
            - second_longitude * first_latitude
        )
        cross_sum += cross
        longitude_sum += (first_longitude + second_longitude) * cross
        latitude_sum += (first_latitude + second_latitude) * cross

    if math.isclose(cross_sum, 0.0, abs_tol=1e-18):
        raw_points = ring[:-1] if ring[0][:2] == ring[-1][:2] else ring
        points = [point for point in raw_points if len(point) >= 2]
        if not points:
            return None
        return (
            sum(float(point[0]) for point in points) / len(points),
            sum(float(point[1]) for point in points) / len(points),
            1.0,
        )

    return (
        origin_longitude + longitude_sum / (3.0 * cross_sum),
        origin_latitude + latitude_sum / (3.0 * cross_sum),
        abs(cross_sum),
    )


def _polygon_centroid(rings: list[list[list[float]]]) -> tuple[float, float, float] | None:
    if not rings:
        return None

    # 현재 100m 데이터에는 interior ring이 없지만, 외곽 ring을 명시적으로 사용해
    # 향후 hole 방향에 따른 centroid 상쇄를 피한다.
    return _ring_centroid(rings[0])


def _geometry_centroid(geometry: dict[str, Any]) -> tuple[float, float]:
    geometry_type = geometry.get("type")
    coordinates = geometry.get("coordinates")

    if geometry_type == "Polygon" and isinstance(coordinates, list):
        centroid = _polygon_centroid(coordinates)
        if centroid is not None:
            return centroid[0], centroid[1]

    if geometry_type == "MultiPolygon" and isinstance(coordinates, list):
        parts = [
            centroid
            for polygon in coordinates
            if isinstance(polygon, list)
            for centroid in [_polygon_centroid(polygon)]
            if centroid is not None
        ]
        if parts:
            total_weight = sum(part[2] for part in parts)
            return (
                sum(part[0] * part[2] for part in parts) / total_weight,
                sum(part[1] * part[2] for part in parts) / total_weight,
            )

    raise ValueError(f"Unsupported or empty grid geometry: {geometry_type}")


def _ring_contains_point(
    ring: list[list[float]], longitude: float, latitude: float
) -> bool:
    """Ray casting with a half-open boundary rule.

    100m centroids can lie exactly on a shared 250m/500m edge. A half-open rule
    assigns such a point to at most one adjacent aggregate instead of duplicating it.
    """
    inside = False
    previous = ring[-1]
    for current in ring:
        if len(previous) < 2 or len(current) < 2:
            previous = current
            continue
        previous_longitude = float(previous[0])
        previous_latitude = float(previous[1])
        current_longitude = float(current[0])
        current_latitude = float(current[1])
        crosses_ray = (current_latitude > latitude) != (previous_latitude > latitude)
        if crosses_ray:
            intersection_longitude = (
                (previous_longitude - current_longitude)
                * (latitude - current_latitude)
                / (previous_latitude - current_latitude)
                + current_longitude
            )
            if longitude < intersection_longitude:
                inside = not inside
        previous = current
    return inside


def _polygon_contains_point(
    rings: list[list[list[float]]], longitude: float, latitude: float
) -> bool:
    if not rings or not _ring_contains_point(rings[0], longitude, latitude):
        return False
    return not any(
        _ring_contains_point(hole, longitude, latitude) for hole in rings[1:]
    )


def _geometry_contains_point(
    geometry: dict[str, Any], longitude: float, latitude: float
) -> bool:
    geometry_type = geometry.get("type")
    coordinates = geometry.get("coordinates")
    if geometry_type == "Polygon" and isinstance(coordinates, list):
        return _polygon_contains_point(coordinates, longitude, latitude)
    if geometry_type == "MultiPolygon" and isinstance(coordinates, list):
        return any(
            _polygon_contains_point(polygon, longitude, latitude)
            for polygon in coordinates
            if isinstance(polygon, list)
        )
    raise ValueError(f"Unsupported aggregate geometry: {geometry_type}")


def _geometry_bounds(geometry: dict[str, Any]) -> tuple[float, float, float, float]:
    bounds = [math.inf, math.inf, -math.inf, -math.inf]

    def visit(value: Any) -> None:
        if (
            isinstance(value, list)
            and len(value) >= 2
            and isinstance(value[0], (int, float))
            and isinstance(value[1], (int, float))
        ):
            longitude = float(value[0])
            latitude = float(value[1])
            bounds[0] = min(bounds[0], longitude)
            bounds[1] = min(bounds[1], latitude)
            bounds[2] = max(bounds[2], longitude)
            bounds[3] = max(bounds[3], latitude)
            return
        if isinstance(value, list):
            for item in value:
                visit(item)

    visit(geometry.get("coordinates"))
    if not all(math.isfinite(value) for value in bounds):
        raise ValueError("Aggregate grid geometry has no finite coordinates")
    return bounds[0], bounds[1], bounds[2], bounds[3]


def _to_meter_coordinates(longitude: float, latitude: float) -> tuple[float, float]:
    reference_cosine = math.cos(math.radians(SEOUL_REFERENCE_LATITUDE))
    return (
        EARTH_RADIUS_M * math.radians(longitude) * reference_cosine,
        EARTH_RADIUS_M * math.radians(latitude),
    )


class SeoulGridSpatialIndex:
    def __init__(self, cells: tuple[GridCell, ...]):
        self.cells = cells
        self.by_id = {cell.grid_id: cell for cell in cells}
        if len(self.by_id) != len(cells):
            raise ValueError("Duplicate grid_id in Seoul 100m map data")
        district_cells: dict[str, list[GridCell]] = {}
        for cell in cells:
            district_cells.setdefault(cell.gu_code, []).append(cell)
        self.by_gu_code = {
            gu_code: tuple(sorted(items, key=lambda cell: cell.grid_id))
            for gu_code, items in district_cells.items()
        }

    @classmethod
    def from_geojson(cls, path: Path) -> "SeoulGridSpatialIndex":
        payload = json.loads(path.read_text(encoding="utf-8"))
        cells: list[GridCell] = []

        for feature in payload.get("features", []):
            properties = feature.get("properties") or {}
            grid_id = properties.get("grid_id")
            area_m2 = properties.get("area_m2")
            geometry = feature.get("geometry")
            if not isinstance(grid_id, str) or not grid_id:
                raise ValueError("100m grid feature is missing grid_id")
            if not isinstance(area_m2, (int, float)) or not math.isfinite(area_m2) or area_m2 <= 0:
                raise ValueError(f"100m grid feature has invalid area_m2: {grid_id}")
            if not isinstance(geometry, dict):
                raise ValueError(f"100m grid feature is missing geometry: {grid_id}")

            longitude, latitude = _geometry_centroid(geometry)
            x_m, y_m = _to_meter_coordinates(longitude, latitude)
            cells.append(
                GridCell(
                    grid_id=grid_id,
                    gu_code=str(properties.get("gu_code", "")),
                    area_m2=float(area_m2),
                    longitude=longitude,
                    latitude=latitude,
                    x_m=x_m,
                    y_m=y_m,
                )
            )

        if not cells:
            raise ValueError("Seoul 100m map data has no features")
        return cls(tuple(cells))

    def select_scope(self, center_grid_id: str, scope_m: int) -> tuple[GridCell, ...]:
        if scope_m not in ALLOWED_SCOPE_METERS:
            raise ValueError(f"scope_m must be one of {ALLOWED_SCOPE_METERS}")

        center = self.by_id.get(center_grid_id)
        if center is None:
            raise KeyError(center_grid_id)
        if scope_m == 100:
            return (center,)

        half_scope_m = scope_m / 2.0
        # WGS84→meter 근사 및 경계 부동소수점 오차만 허용한다. 다음 100m 열까지
        # 포함할 만큼 큰 tolerance를 두면 3×3/5×5 의미가 깨진다.
        tolerance_m = 1.0
        selected = [
            cell
            for cell in self.cells
            if abs(cell.x_m - center.x_m) <= half_scope_m + tolerance_m
            and abs(cell.y_m - center.y_m) <= half_scope_m + tolerance_m
        ]
        return tuple(sorted(selected, key=lambda cell: cell.grid_id))

    def select_district(self, gu_code: str) -> tuple[GridCell, ...]:
        cells = self.by_gu_code.get(gu_code)
        if cells is None:
            raise KeyError(gu_code)
        return cells

    def select_seoul(self) -> tuple[GridCell, ...]:
        """지도 GeoJSON에 실제로 존재하는 서울 100m 격자 전체를 반환한다."""
        return self.cells

    def select_aggregate_geometry(
        self, geometry: dict[str, Any], gu_code: str
    ) -> tuple[GridCell, ...]:
        """Select actual 100m map cells by aggregate geometry.

        The current 250m ``member_grid_ids`` were restored with a bbox
        approximation and contain duplicate assignments. The documented mapping
        rule is therefore applied directly: a 100m geometry centroid belongs to
        the aggregate polygon. Tiny clipped slivers with no centroid use the
        nearest 100m centroid from the same district as the existing data-pipeline
        fallback does.
        """
        west, south, east, north = _geometry_bounds(geometry)
        selected = [
            cell
            for cell in self.cells
            if west <= cell.longitude <= east
            and south <= cell.latitude <= north
            and _geometry_contains_point(geometry, cell.longitude, cell.latitude)
        ]
        if selected:
            return tuple(sorted(selected, key=lambda cell: cell.grid_id))

        district_cells = self.by_gu_code.get(gu_code)
        if not district_cells:
            raise KeyError(gu_code)
        longitude, latitude = _geometry_centroid(geometry)
        x_m, y_m = _to_meter_coordinates(longitude, latitude)
        nearest = min(
            district_cells,
            key=lambda cell: (
                (cell.x_m - x_m) ** 2 + (cell.y_m - y_m) ** 2,
                cell.grid_id,
            ),
        )
        return (nearest,)


class SeoulAggregateGridIndex:
    def __init__(self, cells: tuple[AggregateGridCell, ...]):
        self.cells = cells
        self.by_id = {cell.display_grid_id: cell for cell in cells}
        if len(self.by_id) != len(cells):
            raise ValueError("Duplicate display_grid_id in aggregate map data")

    @classmethod
    def from_geojson(cls, path: Path) -> "SeoulAggregateGridIndex":
        payload = json.loads(path.read_text(encoding="utf-8"))
        cells: list[AggregateGridCell] = []
        for feature in payload.get("features", []):
            properties = feature.get("properties") or {}
            display_grid_id = properties.get("display_grid_id")
            gu_code = properties.get("gu_code")
            geometry = feature.get("geometry")
            raw_member_ids = properties.get("member_grid_ids")
            if not isinstance(display_grid_id, str) or not display_grid_id:
                raise ValueError("Aggregate feature is missing display_grid_id")
            if not isinstance(geometry, dict):
                raise ValueError(
                    f"Aggregate feature is missing geometry: {display_grid_id}"
                )
            member_grid_ids = tuple(
                grid_id
                for grid_id in raw_member_ids
                if isinstance(grid_id, str) and grid_id
            ) if isinstance(raw_member_ids, list) else ()
            cells.append(
                AggregateGridCell(
                    display_grid_id=display_grid_id,
                    gu_code=str(gu_code or ""),
                    geometry=geometry,
                    member_grid_ids=member_grid_ids,
                )
            )
        if not cells:
            raise ValueError("Aggregate map data has no features")
        return cls(tuple(cells))

    def select_constituents(
        self,
        display_grid_id: str,
        grid_index: SeoulGridSpatialIndex,
        *,
        use_explicit_members: bool,
    ) -> tuple[GridCell, ...]:
        aggregate = self.by_id.get(display_grid_id)
        if aggregate is None:
            raise KeyError(display_grid_id)

        if use_explicit_members:
            if not aggregate.member_grid_ids:
                raise ValueError(
                    f"Aggregate feature has no member_grid_ids: {display_grid_id}"
                )
            members: list[GridCell] = []
            seen: set[str] = set()
            for grid_id in aggregate.member_grid_ids:
                if grid_id in seen:
                    raise ValueError(
                        f"Duplicate member grid_id in aggregate: {display_grid_id}"
                    )
                seen.add(grid_id)
                cell = grid_index.by_id.get(grid_id)
                if cell is None:
                    raise ValueError(
                        f"Unknown member grid_id in aggregate: {display_grid_id}"
                    )
                members.append(cell)
            return tuple(sorted(members, key=lambda cell: cell.grid_id))

        return grid_index.select_aggregate_geometry(
            aggregate.geometry, aggregate.gu_code
        )

@lru_cache(maxsize=2)
def load_grid_spatial_index(path: str) -> SeoulGridSpatialIndex:
    return SeoulGridSpatialIndex.from_geojson(Path(path))


@lru_cache(maxsize=2)
def load_aggregate_grid_index(path: str) -> SeoulAggregateGridIndex:
    return SeoulAggregateGridIndex.from_geojson(Path(path))
