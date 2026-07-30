from pathlib import Path
from re import fullmatch
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, Field


ROOT_DIR = Path(__file__).resolve().parents[1]
BACKEND_DIR = Path(__file__).resolve().parent
PUBLIC_DIR = ROOT_DIR / "public"
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
    grid_ids: list[str]
    changes: dict[str, float] = Field(default_factory=dict)
    couple_land_cover: bool = True


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


@app.get("/api/health")
def health():
    return {"ok": True, "backend": "fastapi"}


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
    feature_meta = predict_core.feature_meta()
    return {
        "count": len(feature_meta),
        "features": feature_meta,
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


@app.post("/api/simulate/batch")
def simulate_batch(payload: BatchSimulationRequest) -> Any:
    if not _simulation_ready():
        return _simulation_not_connected_response()

    predict_core = _load_predict_core()
    results = [predict_core.predict(grid_id, payload.changes,
                                    couple_land_cover=payload.couple_land_cover)
               for grid_id in payload.grid_ids]
    valid_results = [result for result in results if "delta_c" in result]
    mean_delta = (
        round(sum(result["delta_c"] for result in valid_results) / len(valid_results), 3)
        if valid_results
        else None
    )
    return {
        "count": len(results),
        "mean_delta_c": mean_delta,
        "results": results,
    }
