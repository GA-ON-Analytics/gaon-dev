# 사용 가이드 화면 사진

첫 접속 가이드 팝업(`src/components/OnboardingGuide.tsx`)이 여기 있는 파일을 씁니다.
**파일이 없으면 자리표시("화면 사진 준비 중")가 대신 뜹니다.** 아래 이름으로 넣기만 하면
코드를 고치지 않아도 붙습니다.

| 파일 | 어떤 화면 |
|---|---|
| `step-1-overview.webp` | 서울 전체가 격자 색으로 칠해진 첫 화면 |
| `step-2-grid.webp` | 격자 하나를 클릭해 오른쪽 상세 패널이 열린 화면 |
| `step-3-indicator.webp` | 지도 지표를 바꿔 지도 색이 달라진 화면 |
| `step-4-simulation.webp` | 정책 시나리오 + 직접 시뮬레이션 화면 |

## ⚠️ PNG를 그대로 넣지 마세요

- 스크린샷 PNG는 수 MB입니다. **WebP로 바꾸고 가로 1200px 이하**로 줄이세요.
- geojson과 달리 **이미지는 gzip으로 줄지 않습니다**(이미 압축된 형식). 파일 크기를
  줄이는 것 말고는 방법이 없습니다.
- 초기 로딩을 2.3초 → 0.6초로 줄여놨는데, 큰 이미지 몇 장이면 그 이득이 사라집니다.

macOS라면 `sips`로 한 번에 됩니다.

```bash
sips -Z 1200 -s format webp -s formatOptions 70 원본.png --out public/guide/step-1-overview.webp
```

팝업의 사진 칸은 16:10 비율(`object-fit: cover`)이라, 그 비율로 잘라 두면 잘리는 부분이 없습니다.
