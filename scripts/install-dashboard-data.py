from __future__ import annotations

import argparse
import re
import shutil
import zipfile
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parents[1]
DEFAULT_DASHBOARD_ZIP = Path.home() / "Downloads" / "dashboard.zip"
PUBLIC_DIR = ROOT_DIR / "public"
PUBLIC_DASHBOARD_DIR = PUBLIC_DIR / "dashboard"

GRID_ENTRY_RE = re.compile(r"^dashboard/(100m|250m)/(\d{5})_[^/]+\.geojson$")

DASHBOARD_FILES_TO_KEEP = {
    "dashboard/manifest.csv": PUBLIC_DASHBOARD_DIR / "manifest.csv",
    "dashboard/seoul_grid_100m.geojson": PUBLIC_DASHBOARD_DIR / "seoul_grid_100m.geojson",
    "dashboard/seoul_grid_250m.geojson": PUBLIC_DASHBOARD_DIR / "seoul_grid_250m.geojson",
    "dashboard/seoul_grid_500m.geojson": PUBLIC_DASHBOARD_DIR / "seoul_grid_500m.geojson",
    "dashboard/seoul_gu_level.geojson": PUBLIC_DASHBOARD_DIR / "seoul_gu_level.geojson",
    "dashboard/seoul_gu_level_summary.csv": PUBLIC_DASHBOARD_DIR / "seoul_gu_level_summary.csv",
}


def copy_entry(zip_file: zipfile.ZipFile, source: str, target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    with zip_file.open(source) as src, target.open("wb") as dst:
        shutil.copyfileobj(src, dst)


def install_dashboard_data(zip_path: Path) -> None:
    if not zip_path.exists():
        raise FileNotFoundError(f"dashboard zip not found: {zip_path}")

    archived_100m = 0
    archived_250m = 0

    with zipfile.ZipFile(zip_path) as zip_file:
        names = set(zip_file.namelist())

        for source, target in DASHBOARD_FILES_TO_KEEP.items():
            if source not in names:
                raise FileNotFoundError(f"required file missing in zip: {source}")
            copy_entry(zip_file, source, target)

        for source in zip_file.namelist():
            match = GRID_ENTRY_RE.match(source)
            if not match:
                continue

            resolution, gu_code = match.groups()
            archive_target = PUBLIC_DASHBOARD_DIR / resolution / Path(source).name
            copy_entry(zip_file, source, archive_target)

            if resolution == "100m":
                archived_100m += 1
            else:
                archived_250m += 1

    if archived_100m != 25:
        raise RuntimeError(f"expected 25 district 100m files, archived {archived_100m}")

    print(f"archived {archived_100m} 100m files and {archived_250m} 250m files under public/dashboard")


def main() -> None:
    parser = argparse.ArgumentParser(description="Install GA_ON dashboard GeoJSON outputs.")
    parser.add_argument(
        "zip_path",
        nargs="?",
        default=DEFAULT_DASHBOARD_ZIP,
        type=Path,
        help="Path to dashboard.zip",
    )
    args = parser.parse_args()
    install_dashboard_data(args.zip_path.expanduser().resolve())


if __name__ == "__main__":
    main()
