"""capture-guide-shots.mjs 가 남긴 PNG 를 가이드용 WebP 로 줄인다.

PNG 를 그대로 두면 안 된다. 스크린샷 PNG 는 장당 수 MB 고, 이미지는 geojson 과
달리 gzip 으로 줄지 않는다(이미 압축된 형식). 초기 로딩을 2.3초 → 0.6초로
줄여놓은 것이 사진 몇 장에 날아간다.

실행: python3 scripts/guide-shots-to-webp.py
"""

from __future__ import annotations

from pathlib import Path

from PIL import Image

GUIDE_DIR = Path(__file__).resolve().parent.parent / "public" / "guide"

# 팝업 안에서 480px 남짓으로 보인다. 전체 화면을 그대로 넣으면 글자가 안 읽혀서
# 단계마다 봐야 할 곳만 잘라낸다. crop 은 (left, top, right, bottom), 2배 해상도 기준.
# 잘라낸 결과도 16:10 이어야 한다 — 팝업의 사진 칸이 그 비율이다.
JOBS: list[tuple[str, tuple[int, int, int, int] | None]] = [
    ("step-1-overview", None),                    # 서울 전체가 요점이라 그대로
    ("step-2-grid", None),                        # 클릭 → 오른쪽이 열린다, 둘 다 필요
    ("step-3-indicator", (0, 0, 1800, 1125)),     # 왼쪽 지표 목록 + 지도 색
    ("step-4-simulation", (1400, 0, 2880, 925)),  # 오른쪽 정책 패널
]

TARGET_WIDTH = 1200
TARGET_HEIGHT = 750


def main() -> None:
    for name, crop in JOBS:
        source = GUIDE_DIR / f"{name}.png"
        if not source.exists():
            print(f"건너뜀 (PNG 없음): {name}")
            continue

        image = Image.open(source).convert("RGB")
        if crop:
            image = image.crop(crop)

        width, height = image.size
        ratio = width / height
        if abs(ratio - 1.6) > 0.02:
            raise SystemExit(f"{name}: 16:10 이 아닙니다 ({width}x{height}). crop 을 고치세요.")

        image = image.resize((TARGET_WIDTH, TARGET_HEIGHT), Image.LANCZOS)
        target = GUIDE_DIR / f"{name}.webp"
        image.save(target, "WEBP", quality=72, method=6)
        source.unlink()
        print(f"{name:20s} {target.stat().st_size / 1024:6.1f} KB")


if __name__ == "__main__":
    main()
