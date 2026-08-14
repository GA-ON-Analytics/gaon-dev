import logging
import math
from pathlib import Path
from re import fullmatch
from typing import Any, Literal

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, Field

from backend.llm_poc.chat_service import (
    ChatInputError,
    ChatProtocolError,
    OllamaConnectionError,
    OllamaModelError,
    OllamaTimeoutError,
    run_chat,
)
from backend.llm_poc.tools import ALLOWED_GRID_FIELDS, GRID_FIELD_SPECS
from backend.policy_presets import (
    POLICY_FEATURE_LABELS,
    policy_presets_payload,
)
from backend.simulation_scope import (
    load_aggregate_constituent_mapping,
    load_aggregate_grid_index,
    load_grid_spatial_index,
)


ROOT_DIR = Path(__file__).resolve().parents[1]
BACKEND_DIR = Path(__file__).resolve().parent
LOGGER = logging.getLogger(__name__)


def _resolve_public_dir() -> Path:
    """정적 데이터가 실제로 있는 폴더를 고른다.

    ★ 개발과 배포의 폴더 이름이 다르다.

        로컬     ROOT/public/dashboard      (레포 원본)
        배포     ROOT/dist/dashboard        (CD가 dist/만 rsync 한다)

    이걸 ``public``으로 고정하면 배포 서버에서 ``/api/dashboard/*``와
    ``/api/seoul-gu``가 전부 404가 된다. 프론트에 정적 폴백이 있어 대부분은
    가려지지만, **구 단위만은 폴백 대상이 다른 파일**이라 실제로 깨진다.

        /api/seoul-gu          →  seoul_gu_level.geojson  (분석값 포함)
        폴백 /seoul_gu.geojson  →  경계만 (gu_priority_score 없음)

    그래서 배포 사이트에서 격자 크기 '구'가 "데이터 준비중"으로 나왔다.
    """

    candidate = ROOT_DIR / "public"
    if (candidate / "dashboard").is_dir():
        return candidate
    fallback = ROOT_DIR / "dist"
    if (fallback / "dashboard").is_dir():
        return fallback
    return candidate


PUBLIC_DIR = _resolve_public_dir()
LEGACY_SEOUL_GU_PATH = PUBLIC_DIR / "seoul_gu.geojson"
DASHBOARD_DIR = PUBLIC_DIR / "dashboard"
DASHBOARD_MANIFEST_PATH = DASHBOARD_DIR / "manifest.csv"
DASHBOARD_GRID_PATHS = {
    "100m": DASHBOARD_DIR / "seoul_grid_100m.geojson",
    "250m": DASHBOARD_DIR / "seoul_grid_250m.geojson",
    "500m": DASHBOARD_DIR / "seoul_grid_500m.geojson",
}
DASHBOARD_100M_MAP_PATH = DASHBOARD_DIR / "seoul_grid_100m_map.geojson"
DASHBOARD_GRID_500M_PATH = DASHBOARD_DIR / "seoul_grid_500m.geojson"
DASHBOARD_GU_LEVEL_PATH = DASHBOARD_DIR / "seoul_gu_level.geojson"
VALID_GRID_RESOLUTIONS = set(DASHBOARD_GRID_PATHS)

MODEL_PATH = BACKEND_DIR / "models" / "seoul_grid_explain_model.joblib"
FEATURE_COLUMNS_PATH = BACKEND_DIR / "models" / "seoul_grid_feature_columns.json"
FEATURE_META_PATH = BACKEND_DIR / "models" / "feature_meta.json"
STATIC_GRID_DATASET_PATH = BACKEND_DIR / "data" / "processed" / "seoul_grid_dataset.csv"
REQUIRED_SIMULATION_FILES = (
    MODEL_PATH,
    FEATURE_COLUMNS_PATH,
    FEATURE_META_PATH,
    STATIC_GRID_DATASET_PATH,
)

app = FastAPI(title="GA_ON Urban Heat API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:5173",
        "http://127.0.0.1:5173",
    ],
    allow_credentials=True,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["*"],
)


class SimulationParameters(BaseModel):
    green_ratio_delta: float = 0
    impervious_ratio_delta: float = 0
    park_area_m2: float = Field(default=0, ge=0)


class SimulationRequest(BaseModel):
    grid_id: str
    policy_options: list[str] = Field(default_factory=list)
    parameters: SimulationParameters = Field(default_factory=SimulationParameters)
    changes: dict[str, float] = Field(default_factory=dict)
    # 녹지↔불투수 연동(이슈 #14). 기본 켬. 불투수면을 직접 지정하면 어차피 연동하지 않는다.
    couple_land_cover: bool = True


class BatchSimulationRequest(BaseModel):
    # 기존 250/500m 집계 격자의 명시적 구성 셀 요청을 계속 지원한다.
    grid_ids: list[str] | None = None
    # 100m 중심 격자 주변 정책 범위 요청. scope_m은 Pydantic이 422로 검증한다.
    grid_id: str | None = None
    scope_m: Literal[100, 300, 500] | None = None
    # 자치구/서울 전체는 숫자 scope_m contract와 분리된 selector로 받는다.
    gu_code: str | None = None
    scope_mode: Literal["district", "seoul"] | None = None
    # 250/500m aggregate 자체가 아니라, 해당 영역의 실제 100m 구성 셀을 선택한다.
    aggregate_resolution: Literal["250m", "500m"] | None = None
    aggregate_id: str | None = None
    # 행정구역 전체 ML 대상은 항상 실제 100m이고, 이 값은 응답 집계 해상도만 정한다.
    display_resolution: Literal["100m", "250m", "500m"] | None = None
    # 행정구역 전체 결과에서는 개별 ML 진단 필드를 줄인다.
    compact: bool = False
    changes: dict[str, float] = Field(default_factory=dict)
    couple_land_cover: bool = True


class ChatRequest(BaseModel):
    message: str
    selected_grid_id: str | None = None


class ChatResponse(BaseModel):
    answer: str
    used_tools: list[str]
    tool_data: dict[str, Any]
    warnings: list[str]
    limitations: list[str]


def _file_response(file_path: Path, media_type: str) -> FileResponse:
    if not file_path.exists():
        raise HTTPException(status_code=404, detail="File not found")

    return FileResponse(file_path, media_type=media_type)


def _geojson_file_response(file_path: Path) -> FileResponse:
    return _file_response(file_path, "application/geo+json")


def _grid_path_for_code(gu_code: str) -> Path:
    return _dashboard_district_grid_path("100m", gu_code)


def _validate_resolution(resolution: str) -> str:
    if resolution not in VALID_GRID_RESOLUTIONS:
        raise HTTPException(status_code=400, detail="resolution must be one of 100m, 250m, 500m")

    return resolution


def _dashboard_grid_path(resolution: str) -> Path:
    return DASHBOARD_GRID_PATHS[_validate_resolution(resolution)]


def _dashboard_district_grid_path(resolution: str, gu_code: str) -> Path:
    _validate_resolution(resolution)

    if resolution == "500m":
        return DASHBOARD_GRID_PATHS[resolution]

    if fullmatch(r"\d{5}", gu_code) is None:
        raise HTTPException(status_code=400, detail="gu_code must be exactly five digits")

    grid_dir = (DASHBOARD_DIR / resolution).resolve()
    matches = sorted(grid_dir.glob(f"{gu_code}_*.geojson"))

    if not matches:
        raise HTTPException(status_code=404, detail="District grid file not found")

    grid_path = matches[0].resolve()

    if grid_dir not in grid_path.parents:
        raise HTTPException(status_code=400, detail="Invalid dashboard grid path")

    return grid_path


def _missing_simulation_files() -> list[str]:
    return [
        str(path.relative_to(ROOT_DIR))
        for path in REQUIRED_SIMULATION_FILES
        if not path.exists()
    ]


def _simulation_not_connected_response() -> JSONResponse:
    missing_files = _missing_simulation_files()
    return JSONResponse(
        status_code=501,
        content={
            "status": "not_connected",
            "message": "ML model will be connected later.",
            "missing_files": missing_files,
        },
    )


def _simulation_ready() -> bool:
    return not _missing_simulation_files()


def _changes_from_payload(payload: SimulationRequest) -> dict[str, float]:
    changes = dict(payload.changes)
    if payload.parameters.green_ratio_delta:
        changes["green_ratio"] = payload.parameters.green_ratio_delta
    if payload.parameters.impervious_ratio_delta:
        changes["impervious_ratio"] = payload.parameters.impervious_ratio_delta
    if payload.parameters.park_area_m2:
        changes["park_area_within_500m"] = payload.parameters.park_area_m2
    return changes


def _load_predict_core():
    try:
        from backend.ml import predict_core
    except ModuleNotFoundError:
        from ml import predict_core

    return predict_core


BATCH_NO_CHANGE_THRESHOLD_C = 0.132
SEOUL_BATCH_CHUNK_SIZE = 10_000


def _batch_targets(payload: BatchSimulationRequest) -> tuple[list[tuple[str, float | None]], str]:
    has_explicit_ids = payload.grid_ids is not None
    has_spatial_scope = payload.grid_id is not None or payload.scope_m is not None
    has_administrative_scope = payload.gu_code is not None or payload.scope_mode is not None
    has_aggregate_scope = (
        payload.aggregate_resolution is not None or payload.aggregate_id is not None
    )
    selector_count = sum(
        (
            has_explicit_ids,
            has_spatial_scope,
            has_administrative_scope,
            has_aggregate_scope,
        )
    )
    if selector_count != 1:
        raise HTTPException(
            status_code=422,
            detail=(
                "Use exactly one selector: grid_ids, grid_id with scope_m, "
                "gu_code with district scope_mode, seoul scope_mode, or "
                "aggregate_resolution with aggregate_id"
            ),
        )

    supports_display_resolution = has_administrative_scope and (
        (payload.scope_mode == "seoul" and payload.gu_code is None)
        or (payload.scope_mode == "district" and payload.gu_code is not None)
    )
    if payload.display_resolution is not None and not supports_display_resolution:
        raise HTTPException(
            status_code=422,
            detail="display_resolution is supported only for district or seoul scope",
        )

    if has_explicit_ids:
        if payload.compact:
            raise HTTPException(
                status_code=422,
                detail="compact response is supported only for district or seoul scope",
            )
        grid_ids = payload.grid_ids or []
        # 기존 endpoint의 단순 평균 contract와 지도 파일 비의존성을 보존한다.
        return [(grid_id, None) for grid_id in grid_ids], "explicit_grid_ids"

    if has_aggregate_scope:
        if payload.compact:
            raise HTTPException(
                status_code=422,
                detail="compact response is not supported for aggregate scope",
            )
        if payload.aggregate_resolution is None or payload.aggregate_id is None:
            raise HTTPException(
                status_code=422,
                detail="aggregate_resolution and aggregate_id are both required",
            )
        try:
            grid_index = load_grid_spatial_index(str(DASHBOARD_100M_MAP_PATH))
            aggregate_index = load_aggregate_grid_index(
                str(DASHBOARD_GRID_PATHS[payload.aggregate_resolution])
            )
            # 500m explicit members match the actual aggregate area. The current
            # 250m field is a documented bbox restoration with duplicate members,
            # so 250m uses the real Polygon/MultiPolygon centroid relation instead.
            cells = aggregate_index.select_constituents(
                payload.aggregate_id,
                grid_index,
                use_explicit_members=payload.aggregate_resolution == "500m",
            )
        except KeyError:
            raise HTTPException(status_code=404, detail="aggregate grid not found") from None
        except FileNotFoundError:
            raise HTTPException(
                status_code=503, detail="Aggregate or 100m map data is unavailable"
            ) from None
        except ValueError as exc:
            raise HTTPException(status_code=500, detail="Invalid aggregate map data") from exc
        return [(cell.grid_id, cell.area_m2) for cell in cells], "aggregate"

    if has_administrative_scope:
        if payload.scope_mode == "seoul":
            if payload.gu_code is not None:
                raise HTTPException(
                    status_code=422,
                    detail="gu_code must not be provided for seoul scope",
                )
            if not payload.compact:
                raise HTTPException(
                    status_code=422,
                    detail="compact response is required for seoul scope",
                )

            try:
                index = load_grid_spatial_index(str(DASHBOARD_100M_MAP_PATH))
                cells = index.select_seoul()
            except FileNotFoundError:
                raise HTTPException(
                    status_code=503,
                    detail="Seoul 100m map data is unavailable",
                ) from None
            except ValueError as exc:
                raise HTTPException(
                    status_code=500,
                    detail="Invalid Seoul 100m map data",
                ) from exc

            return [(cell.grid_id, cell.area_m2) for cell in cells], "seoul"

        if payload.gu_code is None or payload.scope_mode != "district":
            raise HTTPException(
                status_code=422,
                detail="gu_code and scope_mode='district' are required for district scope",
            )
        if fullmatch(r"\d{5}", payload.gu_code) is None:
            raise HTTPException(status_code=422, detail="gu_code must be exactly five digits")

        try:
            index = load_grid_spatial_index(str(DASHBOARD_100M_MAP_PATH))
            cells = index.select_district(payload.gu_code)
        except KeyError:
            raise HTTPException(status_code=404, detail="gu_code not found") from None
        except FileNotFoundError:
            raise HTTPException(status_code=503, detail="Seoul 100m map data is unavailable") from None
        except ValueError as exc:
            raise HTTPException(status_code=500, detail="Invalid Seoul 100m map data") from exc

        return [(cell.grid_id, cell.area_m2) for cell in cells], "district"

    if payload.compact:
        raise HTTPException(
            status_code=422,
            detail="compact response is supported only for district or seoul scope",
        )

    if payload.grid_id is None or payload.scope_m is None:
        raise HTTPException(
            status_code=422,
            detail="grid_id and scope_m are required when grid_ids is not provided",
        )

    try:
        index = load_grid_spatial_index(str(DASHBOARD_100M_MAP_PATH))
        cells = index.select_scope(payload.grid_id, payload.scope_m)
    except KeyError:
        raise HTTPException(status_code=404, detail="center grid_id not found") from None
    except FileNotFoundError:
        raise HTTPException(status_code=503, detail="Seoul 100m map data is unavailable") from None
    except ValueError as exc:
        raise HTTPException(status_code=500, detail="Invalid Seoul 100m map data") from exc

    return [(cell.grid_id, cell.area_m2) for cell in cells], "spatial_scope"


def _mean(values: list[tuple[float, float | None]]) -> tuple[float | None, str | None]:
    if not values:
        return None, None
    if all(area is not None and area > 0 for _, area in values):
        total_area = sum(float(area) for _, area in values if area is not None)
        weighted = sum(value * float(area) for value, area in values if area is not None)
        return round(weighted / total_area, 3), "area_weighted"
    return round(sum(value for value, _ in values) / len(values), 3), "unweighted"


def _is_successful_prediction(result: dict[str, Any]) -> bool:
    values = (
        result.get("delta_c"),
        result.get("before_anomaly"),
        result.get("after_anomaly"),
    )
    return all(
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(value)
        for value in values
    )


def _batch_summary(
    targets: list[tuple[str, float | None]],
    results: list[dict[str, Any]],
) -> dict[str, Any]:
    successful = [
        (target, result)
        for target, result in zip(targets, results)
        if _is_successful_prediction(result)
    ]
    before_mean, aggregation = _mean(
        [(float(result["before_anomaly"]), target[1]) for target, result in successful]
    )
    after_mean, _ = _mean(
        [(float(result["after_anomaly"]), target[1]) for target, result in successful]
    )
    delta_mean, _ = _mean(
        [(float(result["delta_c"]), target[1]) for target, result in successful]
    )
    unclipped = [
        (target, result)
        for target, result in successful
        if not any("clip" in str(warning) for warning in result.get("warnings") or [])
    ]
    unclipped_mean, _ = _mean(
        [(float(result["delta_c"]), target[1]) for target, result in unclipped]
    )

    improved = sum(
        float(result["delta_c"]) < -BATCH_NO_CHANGE_THRESHOLD_C
        for _, result in successful
    )
    worsened = sum(
        float(result["delta_c"]) > BATCH_NO_CHANGE_THRESHOLD_C
        for _, result in successful
    )
    unchanged = len(successful) - improved - worsened
    areas = [area for _, area in targets]
    successful_areas = [target[1] for target, _ in successful]

    return {
        # 기존 batch consumer가 사용하는 필드.
        "count": len(results),
        "mean_delta_c": delta_mean,
        "clipped_count": len(successful) - len(unclipped),
        "valid_count": len(successful),
        "mean_delta_c_unclipped": unclipped_mean,
        # 중심 범위 batch가 지역 단위 결과를 해석하는 필드.
        "grid_count": len(targets),
        "requested_grid_count": len(targets),
        "success_count": len(successful),
        "failed_count": len(results) - len(successful),
        "mean_before_anomaly": before_mean,
        "mean_after_anomaly": after_mean,
        "improved_grid_count": improved,
        "worsened_grid_count": worsened,
        "unchanged_grid_count": unchanged,
        "no_change_threshold_c": BATCH_NO_CHANGE_THRESHOLD_C,
        "aggregation": aggregation,
        "total_area_m2": (
            round(sum(float(area) for area in areas if area is not None), 2)
            if areas and all(area is not None for area in areas)
            else None
        ),
        "successful_area_m2": (
            round(sum(float(area) for area in successful_areas if area is not None), 2)
            if successful_areas and all(area is not None for area in successful_areas)
            else None
        ),
    }


@app.get("/api/health")
def health():
    return {"ok": True, "backend": "fastapi"}


@app.post("/api/chat", response_model=ChatResponse)
def chat(payload: ChatRequest) -> ChatResponse:
    try:
        result = run_chat(payload.message, payload.selected_grid_id)
    except ChatInputError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from None
    except OllamaTimeoutError:
        raise HTTPException(
            status_code=504,
            detail="로컬 AI 응답 시간이 초과되었습니다. 다시 시도해 주세요.",
        ) from None
    except OllamaConnectionError:
        raise HTTPException(
            status_code=503,
            detail=(
                "로컬 AI 서버에 연결할 수 없습니다. "
                "Ollama 실행 상태를 확인해 주세요."
            ),
        ) from None
    except OllamaModelError:
        raise HTTPException(
            status_code=503,
            detail=(
                "로컬 AI 모델 qwen3:4b를 사용할 수 없습니다. "
                "모델 설치 상태를 확인해 주세요."
            ),
        ) from None
    except ChatProtocolError:
        raise HTTPException(
            status_code=503,
            detail="로컬 AI 응답을 처리하지 못했습니다. 다시 시도해 주세요.",
        ) from None

    return ChatResponse(
        answer=result.answer,
        used_tools=result.used_tools,
        tool_data=result.tool_data,
        warnings=result.warnings,
        limitations=result.limitations,
    )


@app.get("/api/model/status")
def model_status() -> Any:
    file_status = {
        "model": MODEL_PATH.exists(),
        "feature_columns": FEATURE_COLUMNS_PATH.exists(),
        "feature_meta": FEATURE_META_PATH.exists(),
        "dataset": STATIC_GRID_DATASET_PATH.exists(),
    }

    if not all(file_status.values()):
        return {
            **file_status,
            "ready": False,
        }

    try:
        predict_core = _load_predict_core()
        return predict_core.model_status()
    except Exception:
        return {
            **file_status,
            "ready": False,
        }


@app.head("/api/seoul-gu")
@app.get("/api/seoul-gu")
def seoul_gu():
    if DASHBOARD_GU_LEVEL_PATH.exists():
        return _geojson_file_response(DASHBOARD_GU_LEVEL_PATH)
    return _geojson_file_response(LEGACY_SEOUL_GU_PATH)


@app.head("/api/grids/{gu_code}")
@app.get("/api/grids/{gu_code}")
def grid_by_gu_code(gu_code: str):
    return _geojson_file_response(_grid_path_for_code(gu_code))


@app.get("/api/dashboard/manifest")
def dashboard_manifest():
    return _file_response(DASHBOARD_MANIFEST_PATH, "text/csv")


@app.head("/api/dashboard/500m")
@app.get("/api/dashboard/500m")
def dashboard_500m():
    return _geojson_file_response(DASHBOARD_GRID_500M_PATH)


@app.head("/api/dashboard/grids/{resolution}")
@app.get("/api/dashboard/grids/{resolution}")
def dashboard_grid_by_resolution(resolution: str):
    return _geojson_file_response(_dashboard_grid_path(resolution))


@app.head("/api/dashboard/grids/100m/map")
@app.get("/api/dashboard/grids/100m/map")
def dashboard_100m_map():
    return _geojson_file_response(DASHBOARD_100M_MAP_PATH)


@app.head("/api/dashboard/grids/{resolution}/{gu_code}")
@app.get("/api/dashboard/grids/{resolution}/{gu_code}")
def dashboard_district_grid_by_resolution(resolution: str, gu_code: str):
    return _geojson_file_response(_dashboard_district_grid_path(resolution, gu_code))


@app.get("/api/dashboard/gu-level")
def dashboard_gu_level():
    return _geojson_file_response(DASHBOARD_GU_LEVEL_PATH)


@app.get("/api/features")
def features() -> Any:
    if not _simulation_ready():
        return _simulation_not_connected_response()

    predict_core = _load_predict_core()
    model_meta = {
        item["name"]: item
        for item in predict_core.feature_meta()
        if isinstance(item, dict) and isinstance(item.get("name"), str)
    }
    feature_meta = []
    for field in ALLOWED_GRID_FIELDS:
        spec = GRID_FIELD_SPECS[field]
        feature_meta.append(
            {
                **model_meta.get(field, {}),
                "name": field,
                "label": spec["label"],
                "description": spec["description"],
                "semantic_definition": spec.get(
                    "semantic_definition",
                    spec["description"],
                ),
                "unit": spec["unit"],
                "category": spec["category"],
                # 출처는 spec을 통째로 펼치지 않고 명시적으로 골라 담는다.
                # 여기 안 넣으면 tools.py에 출처를 채워도 API로는 안 나간다.
                "source": spec["source"],
                # 표시 규칙. 챗봇 답변 카드가 Tool 반환값(원본 0~1 비율)을
                # 화면용 문자열로 바꿀 때 쓴다. 이걸 안 내보내면 프론트가
                # 자기 나름의 규칙을 만들고, 같은 값이 말풍선과 카드에서
                # 다르게 보인다(실측: 카드 0.1645 vs 말풍선 16.45%).
                "is_ratio": spec["is_ratio"],
                "display_decimals": spec["display_decimals"],
            }
        )
    return {
        "count": len(feature_meta),
        "features": feature_meta,
    }


@app.get("/api/policies")
def policies() -> Any:
    """정책 프리셋 정의. 화면과 챗봇이 같은 정의를 보게 하는 통로다.

    모델 준비 여부를 보지 않는다. 정책 정의는 예측 결과가 아니라 상수라서
    모델이 없어도 답할 수 있어야 한다.
    """

    presets = policy_presets_payload()
    return {
        "count": len(presets),
        "policies": presets,
        "featureLabels": dict(POLICY_FEATURE_LABELS),
    }


@app.get("/api/grid/{grid_id}")
def grid_features(grid_id: str) -> Any:
    if not _simulation_ready():
        return _simulation_not_connected_response()

    predict_core = _load_predict_core()
    features = predict_core.get_grid_features(grid_id)

    if features is None:
        raise HTTPException(status_code=404, detail="grid_id not found")
    if "_missing_features" in features:
        return JSONResponse(
            status_code=422,
            content={
                "status": "missing_features",
                "grid_id": grid_id,
                "gu_name": features.get("_gu_name"),
                "missing_features": features["_missing_features"],
            },
        )

    gu_name = features.pop("_gu_name", None)
    return {
        "grid_id": grid_id,
        "gu_name": gu_name,
        "features": features,
    }


@app.post("/api/simulate")
def simulate(payload: SimulationRequest) -> Any:
    if not _simulation_ready():
        return _simulation_not_connected_response()

    predict_core = _load_predict_core()
    return predict_core.predict(payload.grid_id, _changes_from_payload(payload),
                                couple_land_cover=payload.couple_land_cover)


def _predict_batch_individually(
    predict_core: Any,
    grid_ids: list[str],
    changes: dict[str, float],
    couple_land_cover: bool,
) -> list[dict[str, Any]]:
    results = []
    for grid_id in grid_ids:
        try:
            result = predict_core.predict(
                grid_id,
                changes,
                couple_land_cover=couple_land_cover,
            )
        except Exception:
            # 한 격자의 데이터/예측 문제가 나머지 성공 결과를 버리지 않게 개별 실패로 남긴다.
            LOGGER.exception("Batch simulation failed for grid_id=%s", grid_id)
            result = {"grid_id": grid_id, "error": "prediction failed"}
        results.append(result)
    return results


def _predict_batch_vectorized(
    predict_core: Any,
    grid_ids: list[str],
    changes: dict[str, float],
    couple_land_cover: bool,
    *,
    chunk_size: int | None = None,
    compact: bool = False,
) -> list[dict[str, Any]]:
    predict_many = getattr(predict_core, "predict_batch", None)
    if not callable(predict_many):
        return _predict_batch_individually(
            predict_core,
            grid_ids,
            changes,
            couple_land_cover,
        )

    size = chunk_size or len(grid_ids) or 1
    results: list[dict[str, Any]] = []
    for start in range(0, len(grid_ids), size):
        chunk_ids = grid_ids[start : start + size]
        try:
            batch_kwargs: dict[str, Any] = {
                "couple_land_cover": couple_land_cover,
            }
            if compact and getattr(predict_core, "SUPPORTS_COMPACT_BATCH", False):
                batch_kwargs["compact"] = True
            chunk_results = predict_many(
                chunk_ids,
                changes,
                **batch_kwargs,
            )
            if len(chunk_results) != len(chunk_ids):
                raise ValueError("predict_batch result count does not match requested grids")
        except Exception:
            # 한 chunk의 matrix 실패가 다른 chunk의 성공을 버리지 않게 해당 구간만
            # 기존 single predict contract로 복구한다.
            LOGGER.exception(
                "Vectorized batch simulation failed; falling back to single predict"
            )
            chunk_results = _predict_batch_individually(
                predict_core,
                chunk_ids,
                changes,
                couple_land_cover,
            )
        results.extend(chunk_results)
    return results


def _compact_batch_results(results: list[dict[str, Any]]) -> list[dict[str, Any]]:
    compact_results = []
    for result in results:
        if result["status"] == "success":
            compact_results.append(
                {
                    "grid_id": result["grid_id"],
                    "status": "success",
                    "delta_c": result["delta_c"],
                    "area_m2": result.get("area_m2"),
                }
            )
        else:
            compact_results.append(
                {
                    "grid_id": result["grid_id"],
                    "status": "failed",
                    "error": str(result.get("error") or "prediction failed"),
                }
            )
    return compact_results


def _aggregate_display_results(
    display_resolution: Literal["250m", "500m"],
    results: list[dict[str, Any]],
    *,
    gu_code: str | None = None,
) -> list[dict[str, Any]]:
    """한 번 예측한 실제 100m 결과를 현재 표시 aggregate로 면적가중한다.

    district 표시에서 경계를 넘는 구성원은 정책 대상이 아니므로 delta=0으로
    전체 분모에 포함한다. 정책 대상 ML 실패 면적은 기존 successful-area 의미에
    맞춰 분모에서 제외한다.
    """
    aggregate_path = str(DASHBOARD_GRID_PATHS[display_resolution])
    grid_path = str(DASHBOARD_100M_MAP_PATH)
    aggregate_index = load_aggregate_grid_index(aggregate_path)
    constituent_mapping = load_aggregate_constituent_mapping(
        aggregate_path,
        grid_path,
        display_resolution == "500m",
    )
    result_by_grid_id = {
        str(result.get("grid_id")): result
        for result in results
        if result.get("grid_id") is not None
    }
    display_results: list[dict[str, Any]] = []

    display_aggregates = (
        aggregate_index.cells
        if gu_code is None
        else tuple(
            aggregate
            for aggregate in aggregate_index.cells
            if aggregate.gu_code == gu_code
        )
    )
    for aggregate in display_aggregates:
        members = constituent_mapping[aggregate.display_grid_id]
        policy_members = (
            members
            if gu_code is None
            else tuple(cell for cell in members if cell.gu_code == gu_code)
        )
        outside_members = (
            ()
            if gu_code is None
            else tuple(cell for cell in members if cell.gu_code != gu_code)
        )
        successful = []
        for cell in policy_members:
            result = result_by_grid_id.get(cell.grid_id)
            if result is not None and _is_successful_prediction(result):
                successful.append((float(result["delta_c"]), cell.area_m2))

        successful_area_m2 = sum(area for _, area in successful)
        outside_area_m2 = sum(cell.area_m2 for cell in outside_members)
        aggregation_area_m2 = successful_area_m2 + outside_area_m2
        # 정책 대상이 하나도 없는 경계 조각은 외부 구성원의 delta=0만으로 계산된다.
        has_valid_result = bool(successful) or not policy_members
        delta_c = (
            round(
                sum(delta * area for delta, area in successful)
                / aggregation_area_m2,
                3,
            )
            if has_valid_result and aggregation_area_m2 > 0
            else None
        )
        common = {
            "grid_id": aggregate.display_grid_id,
            "area_m2": round(aggregate.area_m2, 2),
            "constituent_count": len(members),
            "policy_target_count": len(policy_members),
            "outside_constituent_count": len(outside_members),
            "success_count": len(successful),
            "failed_count": len(policy_members) - len(successful),
            "successful_area_m2": round(successful_area_m2, 2),
            "aggregation_area_m2": round(aggregation_area_m2, 2),
        }
        if delta_c is None:
            display_results.append(
                {
                    **common,
                    "status": "failed",
                    "error": "all constituent predictions failed",
                }
            )
        else:
            display_results.append(
                {
                    **common,
                    "status": "success",
                    "delta_c": delta_c,
                }
            )

    return display_results


@app.post("/api/simulate/batch")
def simulate_batch(payload: BatchSimulationRequest) -> Any:
    if not _simulation_ready():
        return _simulation_not_connected_response()

    targets, target_mode = _batch_targets(payload)
    predict_core = _load_predict_core()
    grid_ids = [grid_id for grid_id, _ in targets]
    raw_results = _predict_batch_vectorized(
        predict_core,
        grid_ids,
        payload.changes,
        payload.couple_land_cover,
        # 서울 전체만 메모리 상한을 둔다. 기존 자치구/100·300·500 호출 형태는 유지한다.
        chunk_size=SEOUL_BATCH_CHUNK_SIZE if target_mode == "seoul" else None,
        compact=payload.compact,
    )
    results = []
    for (grid_id, area_m2), result in zip(targets, raw_results):
        normalized = dict(result)
        normalized.setdefault("grid_id", grid_id)
        normalized["area_m2"] = area_m2
        results.append(normalized)

    summary = _batch_summary(targets, results)
    for result in results:
        result["status"] = "success" if _is_successful_prediction(result) else "failed"
    if (
        target_mode in {"district", "seoul"}
        and payload.display_resolution in {"250m", "500m"}
    ):
        try:
            response_results = _aggregate_display_results(
                payload.display_resolution,
                results,
                gu_code=payload.gu_code if target_mode == "district" else None,
            )
        except (FileNotFoundError, KeyError, ValueError) as exc:
            raise HTTPException(
                status_code=500,
                detail="Invalid aggregate or 100m map data",
            ) from exc
    else:
        response_results = _compact_batch_results(results) if payload.compact else results
    response = {
        **summary,
        "target_mode": target_mode,
        "center_grid_id": payload.grid_id if target_mode == "spatial_scope" else None,
        "scope_m": payload.scope_m if target_mode == "spatial_scope" else None,
        "results": response_results,
    }
    if target_mode == "aggregate":
        response.update(
            {
                "aggregate_resolution": payload.aggregate_resolution,
                "aggregate_id": payload.aggregate_id,
            }
        )
    if target_mode in {"district", "seoul"}:
        response.update(
            {
                "scope_mode": payload.scope_mode,
                "compact": payload.compact,
            }
        )
        if target_mode == "district":
            response["gu_code"] = payload.gu_code
        if payload.display_resolution is not None:
            response.update(
                {
                    "source_resolution": "100m",
                    "display_resolution": payload.display_resolution,
                    "source_grid_count": len(targets),
                    "display_grid_count": len(response_results),
                }
            )
    return response
