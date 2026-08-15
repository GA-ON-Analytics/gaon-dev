import { useEffect, useRef, useState } from 'react';
import AiChatView from './AiChatView';
import { applyDockPosition, createDockDrag, loadDockPosition } from '../chatDockDrag';
import { GUIDE_CLOSED_EVENT, ONBOARDING_STORAGE_KEY } from './OnboardingGuide';

/**
 * 챗봇이 있다는 걸 한 번은 말해준다.
 *
 * 버튼은 마스코트 얼굴 + 말풍선 배지인데, 처음 온 사람은 마우스를 올려볼 이유가
 * 없어서 이름표(hover 툴팁)를 영영 못 본다. 그래서 첫 접속에 딱 한 번, 버튼 옆에
 * 말풍선을 띄운다. 한 번 본 뒤에는 다시 뜨지 않는다.
 */
const CHAT_HINT_STORAGE_KEY = 'gaon_chat_hint_seen';

function readFlag(key: string): boolean {
  try {
    return window.localStorage.getItem(key) === '1';
  } catch {
    return true;   // 저장을 못 읽는 환경이면 '이미 봤다'로 친다. 매번 뜨는 게 더 나쁘다.
  }
}

function writeFlag(key: string) {
  try {
    window.localStorage.setItem(key, '1');
  } catch {
    // 저장이 막혀도 이번 방문에는 안 뜬다. 기능은 막지 않는다.
  }
}

// GA:ON AI 채팅 진입점.
//
// 기존에는 격자 상세 패널 안의 탭('dashboard' | 'chat')이라 채팅을 열면 대시보드가
// 가려졌다. 우측 하단 플로팅 버튼 + 오버레이 창으로 분리해 둘을 동시에 볼 수 있게 한다.
//
// 상세 패널(.gridDetailSidePanel)은 접힐 때 transform이 걸린다. transform은
// position:fixed의 기준점(containing block)을 바꾸므로, 이 dock을 패널 안에 두면
// 패널을 접을 때 같이 화면 밖으로 밀려난다. 그래서 패널 바깥(MapDashboard)에 둔다.
//
// 버튼은 브랜드 새싹을 본체로 쓰되, 우하단에 말풍선 배지를 얹어 '대화'라는 것을 알린다.
// 아이콘 하나로 브랜드와 기능을 동시에 말하려면 형태가 뭉개져서, 역할을 둘로 나눴다.

export interface AiChatLauncherProps {
  selectedGridId: string | null;
  selectedDisplayGridId: string | null;
  selectedGuName: string | null;
}

/** 마스코트 얼굴. 전신 SVG는 56px에서 몸통·나무가 뭉개져 머리만 잘라낸 파일을 쓴다. */
function MascotFace() {
  return (
    <img className="aiChatFabMascot" src="/mascot-face.svg" alt="" aria-hidden="true" />
  );
}

/** 우하단 배지 — 이 버튼이 '대화'라는 신호 */
function ChatBadge() {
  return (
    <span className="aiChatFabBadge" aria-hidden="true">
      <svg viewBox="0 0 16 16" width="11" height="11" focusable="false">
        <path
          d="M8 1.6c-3.4 0-6.1 2.1-6.1 4.8 0 1.5.85 2.85 2.2 3.75L3.6 13l2.9-1.4c.48.08.98.12 1.5.12 3.4 0 6.1-2.1 6.1-4.8S11.4 1.6 8 1.6z"
          fill="currentColor"
        />
      </svg>
    </span>
  );
}

function CloseIcon() {
  return (
    <svg viewBox="0 0 24 24" width="21" height="21" aria-hidden="true" focusable="false">
      <path
        d="M6 6l12 12M18 6L6 18"
        fill="none"
        stroke="currentColor"
        strokeWidth="2.2"
        strokeLinecap="round"
      />
    </svg>
  );
}

export default function AiChatLauncher({
  selectedGridId,
  selectedDisplayGridId,
  selectedGuName
}: AiChatLauncherProps) {
  const [isOpen, setIsOpen] = useState(false);
  // 창을 닫아둔 사이에 답변이 도착했는지. 요청은 창을 닫아도 계속 진행된다.
  const [hasUnread, setHasUnread] = useState(false);
  // 첫 접속 말풍선. 사용 가이드가 떠 있는 동안 같이 뜨면 둘 다 안 읽히므로,
  // 가이드를 아직 안 본 사람은 가이드가 닫힌 뒤에 띄운다.
  const [hintOpen, setHintOpen] = useState(false);
  const fabRef = useRef<HTMLButtonElement>(null);
  const dockRef = useRef<HTMLDivElement>(null);

  // Esc로 닫기. 대시보드를 계속 쓸 수 있어야 하므로 모달이 아니고,
  // 포커스도 가두지 않는다(focus trap 없음).
  useEffect(() => {
    if (!isOpen) return;
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape') {
        setIsOpen(false);
        fabRef.current?.focus();
      }
    };
    window.addEventListener('keydown', onKeyDown);
    return () => window.removeEventListener('keydown', onKeyDown);
  }, [isOpen]);

  useEffect(() => {
    if (isOpen) dockRef.current?.focus();
  }, [isOpen]);

  // 말풍선을 언제 띄울지. 이미 본 사람에게는 띄우지 않는다.
  useEffect(() => {
    if (readFlag(CHAT_HINT_STORAGE_KEY)) return;

    // 가이드를 이미 본 사람(재방문)이면 바로, 처음 온 사람이면 가이드를 닫은 뒤.
    if (readFlag(ONBOARDING_STORAGE_KEY)) {
      setHintOpen(true);
      return;
    }
    const onGuideClosed = () => setHintOpen(true);
    window.addEventListener(GUIDE_CLOSED_EVENT, onGuideClosed);
    return () => window.removeEventListener(GUIDE_CLOSED_EVENT, onGuideClosed);
  }, []);

  function dismissHint() {
    if (!hintOpen) return;
    setHintOpen(false);
    writeFlag(CHAT_HINT_STORAGE_KEY);
  }

  // 창을 헤더로 끌어 옮긴다.
  //
  // dock 은 항상 마운트돼 있지만 닫혀 있을 때는 hidden 이라 크기가 0이다.
  // 그 상태에서 위치를 계산하면 화면 밖으로 가둬버리므로, 열렸을 때만 붙인다.
  useEffect(() => {
    const dock = dockRef.current;
    if (!dock || !isOpen) return;

    // 저장해 둔 위치를 먼저 반영한다. 없으면 CSS 의 우하단 기본값을 쓴다.
    applyDockPosition(dock, loadDockPosition());

    const drag = createDockDrag(dock, () => {});
    const onPointerDown = (event: PointerEvent) => {
      drag.onPointerDown(event);
    };
    // 헤더를 두 번 누르면 원래 자리로. 옮겨 놓고 못 찾는 상황을 막는 탈출구다.
    const onDoubleClick = (event: MouseEvent) => {
      const target = event.target as HTMLElement | null;
      if (!target?.closest('.gdpChatHeader')) return;
      if (target.closest('button, input, textarea, a')) return;
      drag.reset();
    };

    dock.addEventListener('pointerdown', onPointerDown);
    dock.addEventListener('dblclick', onDoubleClick);
    // 창 크기가 바뀌어 위치가 화면 밖이 되면 다시 안으로 넣는다.
    window.addEventListener('resize', drag.reclamp);

    return () => {
      dock.removeEventListener('pointerdown', onPointerDown);
      dock.removeEventListener('dblclick', onDoubleClick);
      window.removeEventListener('resize', drag.reclamp);
    };
  }, [isOpen]);

  const close = () => {
    setIsOpen(false);
    fabRef.current?.focus();
  };

  return (
    <>
      {/* dock을 항상 마운트해 둔다. 조건부 렌더링({isOpen && ...})으로 바꾸면
          닫을 때마다 AiChatView가 언마운트되어 대화 기록이 사라진다. */}
      <div
        ref={dockRef}
        className="aiChatDock"
        role="dialog"
        aria-label="GA:ON AI 챗봇"
        tabIndex={-1}
        hidden={!isOpen}
      >
        <AiChatView
          isActive={isOpen}
          selectedGridId={selectedGridId}
          selectedDisplayGridId={selectedDisplayGridId}
          selectedGuName={selectedGuName}
          onBack={close}
          onBackgroundReply={() => setHasUnread(true)}
        />
      </div>

      {/* 첫 접속 말풍선. 버튼 안에 넣을 수 없다 — 닫기 버튼이 들어가는데 버튼
          안의 버튼은 안 된다. 그래서 형제로 두고 위치를 따로 잡는다. */}
      {hintOpen && !isOpen && (
        <div className="aiChatHint" role="status">
          <strong>무엇이든 물어보세요</strong>
          <span>
            “녹지율 5%p 높여줘”처럼 말하면 시뮬레이션을 돌려주고, 용어도 설명해 줍니다.
          </span>
          <button
            className="aiChatHintClose"
            type="button"
            aria-label="안내 닫기"
            onClick={dismissHint}
          >
            ✕
          </button>
        </div>
      )}

      <button
        ref={fabRef}
        type="button"
        className={`aiChatFab${isOpen ? ' isOpen' : ''}${hintOpen ? ' hasHint' : ''}`}
        aria-label={
          isOpen
            ? 'GA:ON AI 챗봇 닫기'
            : hasUnread
              ? 'GA:ON AI 챗봇 열기 — 새 답변 있음'
              : 'GA:ON AI 챗봇 열기'
        }
        aria-expanded={isOpen}
        onClick={() => {
          dismissHint();
          if (isOpen) {
            close();
          } else {
            setIsOpen(true);
            setHasUnread(false);
          }
        }}
      >
        {isOpen ? (
          <CloseIcon />
        ) : (
          <>
            <MascotFace />
            <ChatBadge />
            {hasUnread && <span className="aiChatFabUnread" aria-hidden="true" />}
            {/* 마우스를 올리면 이름이 뜬다. 터치 기기에서는 안 뜨므로
                aria-label이 본 이름이고 이건 보조 수단이다. */}
            <span className="aiChatFabTip" aria-hidden="true">
              {hasUnread ? '새 답변이 도착했어요' : 'GA:ON AI 챗봇'}
            </span>
          </>
        )}
      </button>
    </>
  );
}
