import { useEffect, useRef, useState } from 'react';
import AiChatView from './AiChatView';

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

/** 로고의 새싹 — 잎 2장 + 줄기. 56px에서 형태가 읽히도록 단순화했다. */
function SproutMark() {
  return (
    <svg viewBox="0 0 28 28" width="27" height="27" aria-hidden="true" focusable="false">
      <path
        d="M14 24.5V12.2"
        fill="none"
        stroke="currentColor"
        strokeWidth="2.6"
        strokeLinecap="round"
      />
      <path
        d="M14 13.4C14 8.9 10.5 6.1 5 6c-.4 5.3 2.6 8.9 9 8.9z"
        fill="currentColor"
        opacity="0.72"
      />
      <path d="M14 16.2c0-4.2 3.3-7 8.4-7.2.4 5.1-2.4 8.4-8.4 8.4z" fill="currentColor" />
    </svg>
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
        />
      </div>

      <button
        ref={fabRef}
        type="button"
        className={`aiChatFab${isOpen ? ' isOpen' : ''}`}
        aria-label={isOpen ? 'GA:ON AI 챗봇 닫기' : 'GA:ON AI 챗봇 열기'}
        aria-expanded={isOpen}
        onClick={() => (isOpen ? close() : setIsOpen(true))}
      >
        {isOpen ? (
          <CloseIcon />
        ) : (
          <>
            <SproutMark />
            <ChatBadge />
            {/* 마우스를 올리면 이름이 뜬다. 터치 기기에서는 안 뜨므로
                aria-label이 본 이름이고 이건 보조 수단이다. */}
            <span className="aiChatFabTip" aria-hidden="true">
              GA:ON AI 챗봇
            </span>
          </>
        )}
      </button>
    </>
  );
}
