import { useEffect, useRef, useState } from 'react';
import { createPortal } from 'react-dom';

/**
 * 저장 키는 밑줄 표기를 따른다. 지금 코드에 `gaon.chatDock.position`(점 표기)과
 * `gaon_ai_chat_messages`(밑줄)가 섞여 있는데, 다수인 밑줄 쪽에 맞춘다.
 * 표기 통일 자체는 별도 작업 — 키를 바꾸면 기존 사용자 설정이 초기화된다.
 */
export const ONBOARDING_STORAGE_KEY = 'gaon_onboarding_seen';

/** 첫 접속인지. localStorage를 못 쓰는 환경(사생활 보호 모드 등)에서는 띄우지 않는다. */
export function shouldShowOnboarding(): boolean {
  try {
    return window.localStorage.getItem(ONBOARDING_STORAGE_KEY) !== '1';
  } catch {
    return false;
  }
}

function rememberSeen() {
  try {
    window.localStorage.setItem(ONBOARDING_STORAGE_KEY, '1');
  } catch {
    // 저장이 막힌 환경이면 매번 뜨는 것까지가 최선이다. 기능은 막지 않는다.
  }
}

interface Step {
  title: string;
  body: string;
  /**
   * public/guide/ 의 화면 사진. 아직 없으면 자리표시가 대신 뜬다 —
   * 파일을 넣기만 하면 코드 수정 없이 붙는다.
   */
  image: string;
  imageAlt: string;
}

const STEPS: Step[] = [
  {
    title: '지도에서 격자를 클릭하세요',
    body: '100m 격자를 누르면 오른쪽에 그 자리의 지표면온도, 온도에 영향이 큰 요인, 취약성이 열립니다.',
    image: '/guide/step-1-grid.webp',
    imageAlt: '지도에서 격자 하나를 클릭한 화면'
  },
  {
    title: '보고 싶은 지표로 바꿔 보세요',
    body: '왼쪽 "지도 지표 선택"에서 개선 우선순위, 실제 지표면온도, 녹지율 같은 지표로 지도 색을 바꿀 수 있어요.',
    image: '/guide/step-2-indicator.webp',
    imageAlt: '지도 지표를 바꿔 지도 색이 달라진 화면'
  },
  {
    title: '정책을 넣어보고 온도를 비교하세요',
    body: '정책 6종을 같은 조건으로 비교하거나, 녹지율·불투수면을 직접 움직여 온도가 얼마나 내려가는지 예측할 수 있습니다.',
    image: '/guide/step-3-simulation.webp',
    imageAlt: '정책 시나리오와 직접 시뮬레이션 화면'
  }
];

interface Props {
  onClose: () => void;
}

function OnboardingGuide({ onClose }: Props) {
  const [step, setStep] = useState(0);
  // 사진이 아직 없는 단계. onError로 확인하고 자리표시로 갈아끼운다.
  const [missingImages, setMissingImages] = useState<Record<string, boolean>>({});
  const closeRef = useRef<HTMLButtonElement>(null);
  const current = STEPS[step];
  const isLast = step === STEPS.length - 1;

  function close() {
    rememberSeen();
    onClose();
  }

  useEffect(() => {
    closeRef.current?.focus();
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape') close();
    };
    window.addEventListener('keydown', onKeyDown);
    return () => window.removeEventListener('keydown', onKeyDown);
    // close는 onClose만 참조한다 — 단계가 바뀔 때마다 다시 걸 필요가 없다.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [onClose]);

  return createPortal(
    <div className="onboardingBackdrop" onClick={close}>
      <div
        className="onboardingModal"
        role="dialog"
        aria-modal="true"
        aria-labelledby="onboarding-title"
        onClick={(event) => event.stopPropagation()}
      >
        <header className="onboardingHeader">
          <div>
            <span className="onboardingKicker">
              사용 가이드 · {step + 1} / {STEPS.length}
            </span>
            <strong id="onboarding-title">{current.title}</strong>
          </div>
          <button
            ref={closeRef}
            className="onboardingClose"
            type="button"
            aria-label="가이드 닫기"
            onClick={close}
          >
            ✕
          </button>
        </header>

        <div className="onboardingShot">
          {missingImages[current.image] ? (
            <div className="onboardingShotEmpty" aria-hidden="true">
              <span>🖼️</span>
              <p>화면 사진 준비 중</p>
            </div>
          ) : (
            <img
              src={current.image}
              alt={current.imageAlt}
              loading="lazy"
              onError={() =>
                setMissingImages((prev) => ({ ...prev, [current.image]: true }))
              }
            />
          )}
        </div>

        <p className="onboardingBody">{current.body}</p>

        <footer className="onboardingFooter">
          <div className="onboardingDots" aria-hidden="true">
            {STEPS.map((item, index) => (
              <span key={item.title} className={index === step ? 'on' : undefined} />
            ))}
          </div>
          <div className="onboardingActions">
            {step > 0 && (
              <button type="button" onClick={() => setStep((prev) => prev - 1)}>
                이전
              </button>
            )}
            <button
              className="primary"
              type="button"
              onClick={() => (isLast ? close() : setStep((prev) => prev + 1))}
            >
              {isLast ? '시작하기' : '다음'}
            </button>
          </div>
        </footer>

        <p className="onboardingRecall">
          이 가이드는 왼쪽 패널의 <b>사용 가이드</b> 버튼으로 다시 볼 수 있어요.
        </p>
      </div>
    </div>,
    document.body
  );
}

export default OnboardingGuide;
