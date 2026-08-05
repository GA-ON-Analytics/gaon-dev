"""ML 레포(gaon-ml) 산출물을 앱 레포로 가져와 최신화한다.

ML 파이프라인을 다시 돌린 뒤 이것만 실행하면 앱이 최신 결과를 쓴다.

    python -m scripts.sync_from_ml --check    # 무엇이 다른지만 본다 (복사 안 함)
    python -m scripts.sync_from_ml            # 실제로 복사·생성
    python -m scripts.sync_from_ml --ml-root D:\\gaon-ml

ML 레포 위치는 --ml-root, 환경변수 GAON_ML_ROOT, 기본값 ``../GAON`` 순으로 찾는다.

## 왜 단순 복사로는 안 됐나

앱이 지도 개요에 쓰는 ``seoul_grid_100m_map.geojson``을 **ML 파이프라인이 만들지 않는다.**
누가 언젠가 손으로 만든 뒤 재생성 경로가 사라져서, ML을 다시 돌려도 개요 색칠만 옛 데이터로
남아 있었다(`docs/detail-panel-progress.md`에 "재생성 경로 불명"으로 기록돼 있다).

이 스크립트가 ML의 ``seoul_grid_100m.geojson``(전체 속성 53개, 121MB)에서 지도에 필요한
속성 13개만 남겨 그 파일을 만든다. 좌표는 손대지 않는다.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sys
from pathlib import Path

APP_ROOT = Path(__file__).resolve().parents[1]

# 지도 개요가 실제로 쓰는 속성만 남긴다. 나머지 40개는 구별 상세 파일에 있다.
MAP_KEEP_PROPERTIES = (
    "grid_id",
    "gu_code",
    "gu_name",
    "area_m2",
    "building_ratio",
    "green_ratio",
    "impervious_ratio",
    "ndvi",
    "green_delta_c",
    "mean_actual_anomaly",
    "mean_actual_lst",
    "nearest_shelter_distance_m",
    "priority_score",
)

# (ML 상대경로, 앱 상대경로)
FILE_PAIRS = (
    ("models/seoul_grid_explain_model.joblib", "backend/models/seoul_grid_explain_model.joblib"),
    ("models/seoul_grid_feature_columns.json", "backend/models/seoul_grid_feature_columns.json"),
    ("models/feature_meta.json", "backend/models/feature_meta.json"),
    ("data/processed/seoul_grid_dataset.csv", "backend/data/processed/seoul_grid_dataset.csv"),
    ("outputs/dashboard/seoul_grid_250m.geojson", "public/dashboard/seoul_grid_250m.geojson"),
    ("outputs/dashboard/seoul_grid_500m.geojson", "public/dashboard/seoul_grid_500m.geojson"),
    ("outputs/dashboard/seoul_gu_level.geojson", "public/dashboard/seoul_gu_level.geojson"),
    ("outputs/dashboard/seoul_gu_level_summary.csv", "public/dashboard/seoul_gu_level_summary.csv"),
    ("outputs/dashboard/manifest.csv", "public/dashboard/manifest.csv"),
)
DIR_PAIRS = (
    ("outputs/dashboard/100m", "public/dashboard/100m"),
    ("outputs/dashboard/250m", "public/dashboard/250m"),
)
# ML에는 있지만 앱은 쓰지 않는 것. 복사하지 않는다(121MB + 60MB).
SKIPPED = (
    "outputs/dashboard/seoul_grid_100m.geojson  → 대신 _map 경량본을 생성한다",
    "outputs/dashboard/seoul_grid_100m.csv      → 앱이 쓰지 않는다",
)
MAP_SOURCE = "outputs/dashboard/seoul_grid_100m.geojson"
MAP_TARGET = "public/dashboard/seoul_grid_100m_map.geojson"


def resolve_ml_root(explicit: str | None) -> Path:
    for candidate in (explicit, os.getenv("GAON_ML_ROOT"), APP_ROOT.parent / "GAON"):
        if not candidate:
            continue
        path = Path(candidate).resolve()
        if (path / "outputs" / "dashboard").is_dir():
            return path
    raise SystemExit(
        "ML 레포를 찾지 못했습니다. --ml-root 로 경로를 주거나 GAON_ML_ROOT를 설정하세요."
    )


def _digest(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()[:12]


# 줄바꿈만 다른 것을 "다름"으로 세지 않기 위해 정규화해 비교할 확장자.
# geojson·csv·json은 텍스트라 git이 체크아웃할 때 LF를 CRLF로 바꾼다.
_TEXT_SUFFIXES = {".geojson", ".json", ".csv", ".md", ".txt"}


def _text_digest(path: Path) -> str:
    """CRLF/LF 차이를 무시한 내용 지문.

    ★ 원시 바이트로 비교하면 안 된다. 이 레포는 core.autocrlf=true이고
    .gitattributes가 없어서, git이 LF로 저장한 파일을 Windows 워킹트리에
    CRLF로 풀어놓는다. ML 산출물은 LF 그대로다.

    그래서 내용이 완전히 같아도 바이트는 항상 달라진다. 실제로 그 때문에
    "53건 다름"이라는 거짓 보고가 나왔고, 필요 없는 복사를 한 뒤 git이
    다시 LF로 정규화해 커밋에는 1건만 남았다.
    """

    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block.replace(b"\r\n", b"\n"))
    return digest.hexdigest()[:12]


def _content_digest(path: Path) -> str:
    if path.suffix.lower() in _TEXT_SUFFIXES:
        return _text_digest(path)
    return _digest(path)


def _mb(path: Path) -> float:
    return path.stat().st_size / 1_048_576


def compare(source: Path, target: Path) -> str:
    if not source.exists():
        return "ML에 없음"
    if not target.exists():
        return "앱에 없음 → 새로 복사"
    return "동일" if _content_digest(source) == _content_digest(target) else "다름 → 갱신"


def build_map_overview(source: Path, target: Path, dry_run: bool) -> str:
    """전체 속성 geojson에서 지도 개요용 경량본을 만든다."""

    if not source.exists():
        return "ML에 원본 없음"
    data = json.loads(source.read_text(encoding="utf-8"))
    features = data.get("features") or []
    if not features:
        return "원본에 feature가 없음"

    available = set(features[0].get("properties") or {})
    missing = [key for key in MAP_KEEP_PROPERTIES if key not in available]
    if missing:
        # 여기서 멈추는 편이 낫다. 조용히 빠진 채로 배포되면 지도에서 색이 사라진다.
        raise SystemExit(
            "ML 100m geojson에 지도용 속성이 없습니다: "
            + ", ".join(missing)
            + "\n파이프라인(build_seoul_dashboard.py) 출력 컬럼을 확인하세요."
        )

    slim = {
        "type": data.get("type", "FeatureCollection"),
        "features": [
            {
                "type": "Feature",
                "geometry": feature["geometry"],
                "properties": {
                    key: feature["properties"].get(key) for key in MAP_KEEP_PROPERTIES
                },
            }
            for feature in features
        ],
    }
    if data.get("crs"):
        slim["crs"] = data["crs"]

    payload = json.dumps(slim, ensure_ascii=False, separators=(",", ":"))

    # 내용이 같으면 다시 쓰지 않는다. 40MB 파일을 매번 새로 쓰면 내용이
    # 그대로여도 git이 변경으로 잡아 diff가 지저분해진다.
    if target.exists():
        current = target.read_text(encoding="utf-8").replace("\r\n", "\n")
        if current == payload:
            return f"이미 최신 ({len(features):,}건)"

    if dry_run:
        return f"생성 예정 ({len(features):,}건, 속성 {len(MAP_KEEP_PROPERTIES)}개)"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(payload, encoding="utf-8")
    return f"생성 완료 ({len(features):,}건, {_mb(target):.1f}MB)"


def verify(app_root: Path) -> list[str]:
    """복사 후 앱이 실제로 쓸 수 있는 상태인지 본다."""

    problems: list[str] = []
    dashboard = app_root / "public" / "dashboard"

    counts: dict[str, int] = {}
    for name in ("seoul_grid_100m_map.geojson", "seoul_grid_250m.geojson", "seoul_grid_500m.geojson"):
        path = dashboard / name
        if not path.exists():
            problems.append(f"{name} 없음")
            continue
        counts[name] = len(json.loads(path.read_text(encoding="utf-8")).get("features", []))

    if counts.get("seoul_grid_100m_map.geojson", 0) == 0:
        problems.append("지도 개요 파일에 격자가 없습니다")

    # 구별 분할 25개가 다 있는지
    for resolution in ("100m", "250m"):
        found = len(list((dashboard / resolution).glob("*.geojson")))
        if found != 25:
            problems.append(f"{resolution} 구별 파일이 25개가 아닙니다: {found}개")

    # 모델·데이터가 서로 맞는지 (predict_core가 실제로 뜨는지)
    sys.path.insert(0, str(app_root))
    try:
        from backend.ml import predict_core

        missing = predict_core.missing_required_files()
        if missing:
            problems.append("모델·데이터 파일 누락: " + ", ".join(missing))
        else:
            result = predict_core.predict("11110_00909", {"green_ratio": 0.05})
            if "error" in result:
                problems.append(f"예측 시험 실패: {result['error']}")
    except Exception as exc:  # noqa: BLE001
        problems.append(f"predict_core 확인 실패: {exc}")
    return problems


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--ml-root", help="ML 레포 경로 (기본 ../GAON)")
    parser.add_argument("--check", action="store_true", help="복사하지 않고 차이만 본다")
    args = parser.parse_args()

    ml_root = resolve_ml_root(args.ml_root)
    print(f"ML  {ml_root}")
    print(f"APP {APP_ROOT}")
    print(f"모드 {'점검만 (--check)' if args.check else '복사·생성'}")
    print()

    changed = 0
    print(f"{'대상':<46}{'상태':<18}{'크기':>9}")
    print("-" * 74)
    for ml_rel, app_rel in FILE_PAIRS:
        source, target = ml_root / ml_rel, APP_ROOT / app_rel
        state = compare(source, target)
        size = f"{_mb(source):.1f}MB" if source.exists() else "-"
        print(f"{app_rel:<46}{state:<18}{size:>9}")
        if state.endswith("복사") or state.endswith("갱신"):
            changed += 1
            if not args.check:
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source, target)

    for ml_rel, app_rel in DIR_PAIRS:
        source, target = ml_root / ml_rel, APP_ROOT / app_rel
        if not source.is_dir():
            print(f"{app_rel:<46}{'ML에 없음':<18}{'-':>9}")
            continue
        diff = [p.name for p in sorted(source.glob("*.geojson"))
                if compare(p, target / p.name) != "동일"]
        state = "동일" if not diff else f"{len(diff)}개 갱신"
        print(f"{app_rel + '/':<46}{state:<18}{'-':>9}")
        if diff:
            changed += len(diff)
            if not args.check:
                target.mkdir(parents=True, exist_ok=True)
                for name in diff:
                    shutil.copy2(source / name, target / name)

    print()
    print("지도 개요 경량본 (ML 파이프라인이 만들지 않음 → 여기서 생성)")
    state = build_map_overview(ml_root / MAP_SOURCE, APP_ROOT / MAP_TARGET, args.check)
    print(f"  {MAP_TARGET}: {state}")

    for note in SKIPPED:
        print(f"  건너뜀: {note}")

    print()
    if args.check:
        print(f"갱신 대상 {changed}건. 실제로 반영하려면 --check 없이 실행하세요.")
        return 0

    print("검증")
    problems = verify(APP_ROOT)
    if problems:
        for problem in problems:
            print(f"  X {problem}")
        return 1
    print("  O 격자 수·구별 파일 25개·모델 로드·예측 시험 통과")
    print()
    print(f"완료. 갱신 {changed}건 + 지도 개요 1건.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
