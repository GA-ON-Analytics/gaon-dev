from pathlib import Path
from re import fullmatch
from typing import Any

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


ROOT_DIR = Path(__file__).resolve().parents[1]
BACKEND_DIR = Path(__file__).resolve().parent


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
    grid_ids: list[str]
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

    # 학습범위 clip에 걸린 셀은 요청보다 적은 개입을 받았는데도 저감이 크게 나온다.
    # 실측(구별 200셀 표본)에서 그냥 평균하면 clip 제외 평균보다 0.13~0.35℃ 더 시원하게 나왔고,
    # 이는 delta_c 추정오차(0.132℃)를 넘는다. 즉 반올림 오차가 아니라 계통 편향이다.
    # 평균값 자체는 바꾸지 않고(사용자가 보던 수가 말없이 달라지면 더 혼란스럽다)
    # 몇 개가 잘렸는지와 잘린 셀을 뺀 평균을 함께 돌려준다.
    unclipped = [
        result
        for result in valid_results
        if not any("clip" in str(warning) for warning in result.get("warnings") or [])
    ]
    return {
        "count": len(results),
        "mean_delta_c": mean_delta,
        "clipped_count": len(valid_results) - len(unclipped),
        "valid_count": len(valid_results),
        "mean_delta_c_unclipped": (
            round(sum(result["delta_c"] for result in unclipped) / len(unclipped), 3)
            if unclipped
            else None
        ),
        "results": results,
    }
