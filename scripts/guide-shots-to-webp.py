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

# 잘라낼 곳을 화면 비율(0~1)로 적는다. 촬영 배율을 바꿔도 그대로 쓸 수 있다.
# (왼쪽, 위, 폭) — 높이는 16:10 이 되게 폭에서 계산한다. 팝업의 사진 칸이 그 비율이다.
#
# 전체 화면을 그대로 넣으면 팝업 안에서 글자가 안 읽힌다. 단계마다 그 단계에서
# 봐야 할 곳만 남기고 바짝 자른다.
JOBS: list[tuple[str, tuple[float, float, float] | None]] = [
    # 오른쪽 상세 패널은 격자를 안 골라 비어 있다. 그대로 두면 사진 오른쪽이
    # 흰 여백처럼 보여서 잘라낸다.
    ("step-1-overview", (0.0, 0.0, 0.78)),          # 왼쪽 패널 + 서울 전체 지도
    ("step-2-grid", (0.23, 0.0, 0.77)),             # 지도 팝업 + 오른쪽 상세 패널
    # 0.52 로 자르면 선택된 지표 행이 가로로 잘려 망가져 보인다. 잘라내는 높이는
    # 폭에서 나오므로, 세로를 더 담으려면 폭을 넓혀야 한다.
    ("step-3-indicator", (0.0, 0.0, 0.60)),         # 왼쪽 지표 목록 + 지도 색
    ("step-4-simulation", (0.47, 0.0, 0.53)),       # 오른쪽 정책 패널
    ("step-5-chat", (0.40, 0.30, 0.60)),            # 챗봇 창
]

# 넓은 화면에서 팝업의 사진이 1,014px 로 보인다. 고해상도 화면은 그 2배를
# 원하므로 2,000px 로 담아둔다.
TARGET_WIDTH = 2000
TARGET_HEIGHT = 1250
QUALITY = 82


def main() -> None:
    for name, crop in JOBS:
        source = GUIDE_DIR / f"{name}.png"
        if not source.exists():
            print(f"건너뜀 (PNG 없음): {name}")
            continue

        image = Image.open(source).convert("RGB")
        width, height = image.size

        if crop:
            left_ratio, top_ratio, width_ratio = crop
            box_width = round(width * width_ratio)
            box_height = round(box_width / 1.6)
            left = round(width * left_ratio)
            top = round(height * top_ratio)
            if box_height > height:
                raise SystemExit(f"{name}: 잘라낼 높이가 원본보다 큽니다. 폭을 줄이세요.")
            image = image.crop((left, top, left + box_width, top + box_height))

        box_width, box_height = image.size
        if abs(box_width / box_height - 1.6) > 0.02:
            raise SystemExit(f"{name}: 16:10 이 아닙니다 ({box_width}x{box_height}).")
        if box_width < TARGET_WIDTH:
            print(f"  주의: {name} 원본이 {box_width}px 이라 {TARGET_WIDTH}px 로 늘립니다.")

        image = image.resize((TARGET_WIDTH, TARGET_HEIGHT), Image.LANCZOS)
        target = GUIDE_DIR / f"{name}.webp"
        image.save(target, "WEBP", quality=QUALITY, method=6)
        source.unlink()
        print(f"{name:20s} {target.stat().st_size / 1024:6.1f} KB")


if __name__ == "__main__":
    main()
