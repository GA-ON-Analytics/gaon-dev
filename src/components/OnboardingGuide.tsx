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
  body: readonly string[];
  /**
   * public/guide/ 의 화면 사진. 아직 없으면 자리표시가 대신 뜬다 —
   * 파일을 넣기만 하면 코드 수정 없이 붙는다.
   *
   * ⚠️ PNG를 그대로 넣지 말 것. 스크린샷 PNG는 수 MB고, 이미지는 geojson과 달리
   * gzip으로 줄지 않는다(이미 압축된 형식). WebP로 바꾸고 가로 1200px 이하로.
   */
  image: string;
  imageAlt: string;
}

const STEPS: readonly Step[] = [
  {
    title: '어디를 먼저 시원하게 할지 찾는 대시보드예요',
    body: [
      '서울을 100m 격자로 나눠, 여름철 지표면온도와 그 온도를 만든 요인을 격자마다 보여줍니다.',
      '녹지를 늘리거나 지붕을 밝게 칠하면 그 격자의 온도가 얼마나 내려갈지도 예측해 볼 수 있어요.'
    ],
    image: '/guide/step-1-overview.webp',
    imageAlt: '서울 전체가 격자 색으로 칠해진 대시보드 첫 화면'
  },
  {
    title: '지도에서 격자를 클릭하세요',
    body: [
      '격자 하나를 누르면 오른쪽에 그 자리의 지표면온도, 온도에 영향이 큰 요인, 취약성이 열립니다.',
      '구 평균보다 몇 ℃ 높은지, 구 안에서 몇 번째로 시급한지도 함께 나옵니다.'
    ],
    image: '/guide/step-2-grid.webp',
    imageAlt: '지도에서 격자 하나를 클릭해 상세 패널이 열린 화면'
  },
  {
    title: '보고 싶은 지표로 지도 색을 바꿀 수 있어요',
    body: [
      '왼쪽 "지도 지표 선택"에서 개선 우선순위, 실제 지표면온도, 녹지율 같은 지표를 고르면 지도 전체가 그 기준으로 다시 칠해집니다.',
      '지금 어떤 지표를 보고 있는지는 목록 위 검은 칩에 표시됩니다.'
    ],
    image: '/guide/step-3-indicator.webp',
    imageAlt: '지도 지표를 바꿔 지도 색이 달라진 화면'
  },
  {
    title: '정책을 넣어보고 온도를 비교하세요',
    body: [
      '격자를 고른 뒤 "이 격자 개선해보기"를 누르면 정책 시나리오로 바로 내려갑니다.',
      '포장공간 녹지화·옥상녹화·쿨루프 등 6가지를 같은 조건으로 비교하거나, 녹지율과 불투수면을 직접 움직여 예측할 수 있어요.',
      '각 정책에는 서울시가 실제로 시행한 사례를 붙여 뒀습니다.'
    ],
    image: '/guide/step-4-simulation.webp',
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

  useEffect(() => {
    closeRef.current?.focus();
  }, []);

  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape') {
        rememberSeen();
        onClose();
      }
    };
    window.addEventListener('keydown', onKeyDown);
    return () => window.removeEventListener('keydown', onKeyDown);
  }, [onClose]);

  function close() {
    rememberSeen();
    onClose();
  }

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

        <div className="onboardingBody">
          <div className="onboardingShot">
            {missingImages[current.image] ? (
              <div className="onboardingShotEmpty">
                <span aria-hidden="true">🖼️</span>
                <p>화면 사진 준비 중</p>
              </div>
            ) : (
              <img
                src={current.image}
                alt={current.imageAlt}
                onError={() =>
                  setMissingImages((prev) => ({ ...prev, [current.image]: true }))
                }
              />
            )}
          </div>

          <div className="onboardingText">
            {current.body.map((line) => (
              <p key={line}>{line}</p>
            ))}
          </div>
        </div>

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
          왼쪽 패널 맨 위 <b>?</b> 버튼을 누르면 언제든 다시 볼 수 있어요.
        </p>
      </div>
    </div>,
    document.body
  );
}

export default OnboardingGuide;
