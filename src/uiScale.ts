/**
 * 화면 비율을 해상도와 무관하게 일정하게 유지한다.
 *
 * styles.css 의 --ui-scale 을 zoom 으로 쓰는데, 이 값을 0.8 로 못박아 두면
 * "1440px 에서의 비율"만 0.8 이고 다른 해상도에서는 비율이 달라진다.
 * 좌측 패널을 예로 들면 폭이 227px 로 고정되므로
 *   1440px  →  화면의 15.8%
 *   2560px  →  화면의  8.9%   (같은 UI 인데 훨씬 작아 보인다)
 *   1280px  →  화면의 17.7%
 * 이 된다. 폭에 비례해 배율을 바꾸면 어느 해상도에서나 같은 비율이 된다.
 *
 * CSS 만으로는 못 한다. zoom 은 단위 없는 수를 받는데 calc(100vw / 1440) 은
 * 길이를 내놓고, CSS calc 에는 길이를 길이로 나누는 연산이 없다.
 * (tan(atan2(...)) 로 길이를 수로 바꾸는 우회법이 있지만 읽기 어렵다.)
 */

/** 이 폭에서 배율이 정확히 BASE_SCALE 이 된다. 사장님이 보고 정한 기준 화면. */
const REFERENCE_WIDTH = 1440;
const BASE_SCALE = 0.8;

/**
 * 비례만 따르면 작은 화면에서 글자가 못 읽을 만큼 작아진다.
 */
const MIN_SCALE = 0.68;

/**
 * 1 을 넘기지 않는다. "화면 대비 같은 비율"과 "같은 크기로 보임"은 다르다.
 *
 * 노트북 내장 화면은 레티나(dPR 2)라 CSS 픽셀 하나가 물리적으로 작고,
 * 외부 모니터(2560x1440, dPR 1)는 같은 CSS 픽셀이 물리적으로 30% 가까이 크다.
 * 비례만 따르면 2560px 에서 배율이 1.42 까지 올라가, 화면 대비 비율은 같은데
 * 실물 크기는 내장 화면의 두 배가 되어 눈에 띄게 커 보인다. 실제로 그렇게
 * 보인다는 확인을 받고 낮췄다.
 *
 * 상한을 1 로 두는 건 임의의 숫자가 아니다. styles.css 의 px 값 1,139개가
 * 1배 기준으로 그려져 있으므로, 1 을 넘기는 건 원본 설계보다 크게 확대하는
 * 것이다. 그 위로는 커질 이유가 없다.
 *
 * 결과: 1280px → 0.71 · 1440px → 0.80 · 1710px → 0.95 · 1800px 이상 → 1.00
 */
const MAX_SCALE = 1;

/**
 * 이 폭 미만은 styles.css 의 좁은 화면 레이아웃(패널이 화면 폭을 가득 채움)이
 * 걸린다. 거기서 비례로 더 줄이면 이미 좁은 화면의 글자가 읽기 어려워진다.
 * 그 구간은 고정 배율을 쓴다. 값은 styles.css 의 미디어쿼리와 맞춘 것이다.
 */
const NARROW_BREAKPOINT = 821;
const NARROW_SCALE = 0.9;

export function computeUiScale(viewportWidth: number): number {
  if (viewportWidth < NARROW_BREAKPOINT) return NARROW_SCALE;

  const proportional = (BASE_SCALE * viewportWidth) / REFERENCE_WIDTH;
  return Math.min(MAX_SCALE, Math.max(MIN_SCALE, proportional));
}

/**
 * --ui-scale 을 현재 폭에 맞춰 갱신하고, 화면이 바뀔 때마다 다시 맞춘다.
 * 정리 함수를 돌려주므로 React effect 에서 그대로 쓸 수 있다.
 *
 * 신호를 두 가지로 받는다. 어느 하나만으로는 놓치는 경우가 있다.
 *   - resize 이벤트: 사용자가 창을 끌 때 온다. 확대/축소는 놓칠 수 있다.
 *   - ResizeObserver: 요소 상자가 바뀔 때 온다. 다만 뷰포트를 프로그램으로
 *     바꾸는 환경(자동화 도구 등)에서는 안 오는 경우를 실제로 확인했다.
 * 같은 핸들러라 두 번 불려도 결과가 같고, 값이 안 바뀌면 아무 일도 안 한다.
 */
export function startUiScaleSync(): () => void {
  const root = document.documentElement;
  let applied = '';

  const apply = () => {
    // 소수점을 길게 두면 브라우저마다 반올림이 달라 1px 씩 어긋난다.
    const scale = computeUiScale(window.innerWidth).toFixed(3);
    if (scale === applied) return;
    applied = scale;
    root.style.setProperty('--ui-scale', scale);
  };

  apply();

  const observer = new ResizeObserver(apply);
  observer.observe(root);
  window.addEventListener('resize', apply);

  return () => {
    observer.disconnect();
    window.removeEventListener('resize', apply);
  };
}
