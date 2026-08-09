/**
 * 챗봇 창을 헤더로 끌어 옮긴다.
 *
 * ★ zoom 주의
 * .aiChatDock 에는 zoom: var(--ui-scale) 이 걸려 있다. 포인터 좌표(clientX/Y)는
 * 화면 기준인데 left/top 은 배율이 적용된 좌표계라 값이 어긋난다. 실측하면
 * left: 100px 을 줬을 때 화면상 95px 에 놓인다(배율 0.95).
 *   화면좌표 = left * 배율   →   left = 화면좌표 / 배율
 * 이 나눗셈을 빠뜨리면 배율이 1이 아닌 화면에서 커서와 창이 어긋나며 끌린다.
 *
 * 위치는 '화면 기준 px' 로 저장한다. 배율이 적용된 left 값을 그대로 저장하면
 * 다른 배율의 모니터에서 열었을 때 엉뚱한 자리에 놓인다.
 */

const STORAGE_KEY = 'gaon.chatDock.position';

/** 창이 화면 밖으로 완전히 나가지 않도록 남겨둘 최소 여백 */
const EDGE_KEEP = 24;

export interface DockPosition {
  /** 화면 기준 좌상단 x (px) */
  x: number;
  /** 화면 기준 좌상단 y (px) */
  y: number;
}

function currentScale(): number {
  const raw = getComputedStyle(document.documentElement)
    .getPropertyValue('--ui-scale')
    .trim();
  const value = Number(raw);
  // 배율을 못 읽으면 1로 둔다. 0으로 나누는 사고를 막는다.
  return Number.isFinite(value) && value > 0 ? value : 1;
}

/** 창이 화면 안에 남아 있도록 가둔다. 폭·높이는 화면 기준(배율 적용 후) 값. */
function clampToViewport(
  position: DockPosition,
  width: number,
  height: number
): DockPosition {
  const maxX = Math.max(0, window.innerWidth - width);
  const maxY = Math.max(0, window.innerHeight - height);
  return {
    // 왼쪽·위로는 EDGE_KEEP 만큼 밖으로 나가도 되게 둔다. 완전히 붙이면
    // 창 그림자가 잘려 어색하고, 헤더를 다시 잡을 수 없게 되지는 않는다.
    x: Math.min(maxX, Math.max(-EDGE_KEEP, position.x)),
    y: Math.min(maxY, Math.max(0, position.y))
  };
}

export function loadDockPosition(): DockPosition | null {
  try {
    const raw = window.localStorage.getItem(STORAGE_KEY);
    if (!raw) return null;
    const parsed = JSON.parse(raw) as unknown;
    if (
      typeof parsed === 'object' &&
      parsed !== null &&
      Number.isFinite((parsed as DockPosition).x) &&
      Number.isFinite((parsed as DockPosition).y)
    ) {
      return parsed as DockPosition;
    }
  } catch {
    // 저장값이 깨졌으면 기본 위치로 돌아간다. 여기서 던지면 창이 안 열린다.
  }
  return null;
}

function saveDockPosition(position: DockPosition | null): void {
  try {
    if (position) {
      window.localStorage.setItem(STORAGE_KEY, JSON.stringify(position));
    } else {
      window.localStorage.removeItem(STORAGE_KEY);
    }
  } catch {
    // 사생활 보호 모드 등에서 저장이 막힐 수 있다. 위치만 기억 못 할 뿐이다.
  }
}

/** 화면 기준 위치를 dock 의 inline style 로 반영한다. */
export function applyDockPosition(
  dock: HTMLElement,
  position: DockPosition | null
): void {
  if (!position) {
    // 기본 위치(우하단)로 되돌린다. CSS 의 right/bottom 규칙이 다시 살아난다.
    dock.style.removeProperty('left');
    dock.style.removeProperty('top');
    dock.style.removeProperty('right');
    dock.style.removeProperty('bottom');
    return;
  }

  const scale = currentScale();
  const rect = dock.getBoundingClientRect();
  const safe = clampToViewport(position, rect.width, rect.height);

  dock.style.left = `${safe.x / scale}px`;
  dock.style.top = `${safe.y / scale}px`;
  dock.style.right = 'auto';
  dock.style.bottom = 'auto';
}

export interface DockDragHandlers {
  /** 헤더에서 포인터를 눌렀을 때 호출한다. 드래그가 시작되면 true. */
  onPointerDown: (event: PointerEvent) => boolean;
  /** 기본 위치로 되돌린다. */
  reset: () => void;
  /** 창 크기가 바뀌었을 때 다시 화면 안으로 넣는다. */
  reclamp: () => void;
}

/**
 * dock 요소에 드래그를 붙인다. 드래그 중에는 React 상태를 건드리지 않고
 * inline style 만 바꾼다. 상태를 매 프레임 갱신하면 대화 목록까지 다시 그려져
 * 끌리는 게 눈에 띄게 끊긴다.
 */
export function createDockDrag(
  dock: HTMLElement,
  onCommit: (position: DockPosition | null) => void
): DockDragHandlers {
  let position: DockPosition | null = loadDockPosition();

  const commit = (next: DockPosition | null) => {
    position = next;
    saveDockPosition(next);
    applyDockPosition(dock, next);
    onCommit(next);
  };

  const onPointerDown = (event: PointerEvent) => {
    // 헤더의 버튼(닫기·가이드 토글)에서 시작한 건 드래그가 아니다.
    const target = event.target as HTMLElement | null;
    if (!target || target.closest('button, input, textarea, a')) return false;
    if (!target.closest('.gdpChatHeader')) return false;
    // 주 버튼(왼쪽 클릭·터치)만 받는다.
    if (event.button !== 0) return false;

    const startRect = dock.getBoundingClientRect();
    const startX = event.clientX;
    const startY = event.clientY;
    const scale = currentScale();

    const move = (moveEvent: PointerEvent) => {
      const next = {
        x: startRect.x + (moveEvent.clientX - startX),
        y: startRect.y + (moveEvent.clientY - startY)
      };
      const safe = clampToViewport(next, startRect.width, startRect.height);
      dock.style.left = `${safe.x / scale}px`;
      dock.style.top = `${safe.y / scale}px`;
      dock.style.right = 'auto';
      dock.style.bottom = 'auto';
      position = safe;
    };

    const end = () => {
      window.removeEventListener('pointermove', move);
      window.removeEventListener('pointerup', end);
      window.removeEventListener('pointercancel', end);
      document.body.classList.remove('isDraggingChatDock');
      commit(position);
    };

    window.addEventListener('pointermove', move);
    window.addEventListener('pointerup', end);
    window.addEventListener('pointercancel', end);
    // 끌던 중에 글자가 선택되면 파랗게 반전돼 지저분해진다.
    document.body.classList.add('isDraggingChatDock');
    event.preventDefault();
    return true;
  };

  return {
    onPointerDown,
    reset: () => commit(null),
    reclamp: () => applyDockPosition(dock, position)
  };
}
