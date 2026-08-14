import { useEffect, useRef } from 'react';
import { createPortal } from 'react-dom';
import type { PolicyCase, PolicyPreset } from '../types/dashboard';

interface Props {
  preset: PolicyPreset;
  /** 요약은 `src/config/policyCases.ts`, 원문 링크는 백엔드가 준 preset.sourceUrl */
  policyCase: PolicyCase;
  onClose: () => void;
}

/**
 * 정책 사례 요약 팝업.
 *
 * 원문(news.seoul.go.kr)을 iframe으로 띄우는 것은 불가능하다 — 그 서버가
 * `X-Frame-Options: SAMEORIGIN` 과 `frame-ancestors 'self'` 를 내려보내기 때문에
 * 프론트에서 우회할 방법이 없다. 그래서 원문을 읽고 우리가 요약해 보여주고,
 * 원문은 아래 링크로만 연결한다.
 *
 * 팝업은 document.body 로 portal 한다. 상세 패널에는 `zoom: var(--ui-scale)` 이
 * 걸려 있어서 그 안에 두면 화면 좌표와 CSS 좌표가 어긋난다(src/chatDockDrag.ts 참고).
 * 바깥에 두면 보정이 아예 필요 없다.
 */
function PolicyCaseModal({ preset, policyCase, onClose }: Props) {
  const closeRef = useRef<HTMLButtonElement>(null);

  useEffect(() => {
    closeRef.current?.focus();
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape') onClose();
    };
    window.addEventListener('keydown', onKeyDown);
    return () => window.removeEventListener('keydown', onKeyDown);
  }, [onClose]);

  return createPortal(
    <div className="policyCaseBackdrop" onClick={onClose}>
      <div
        className="policyCaseModal"
        role="dialog"
        aria-modal="true"
        aria-labelledby="policy-case-title"
        onClick={(event) => event.stopPropagation()}
      >
        <header className="policyCaseHeader">
          <div>
            <span className="policyCaseKicker">{preset.name} · 실제 시행 사례</span>
            <strong id="policy-case-title">{policyCase.project}</strong>
          </div>
          <button
            ref={closeRef}
            className="policyCaseClose"
            type="button"
            aria-label="닫기"
            onClick={onClose}
          >
            ✕
          </button>
        </header>

        <div className="policyCaseBody">
          <dl className="policyCaseMeta">
            <div>
              <dt>시행 주체</dt>
              <dd>{policyCase.agency}</dd>
            </div>
            <div>
              <dt>시행 시기</dt>
              <dd>{policyCase.period}</dd>
            </div>
          </dl>

          <div className="policyCaseSummary">
            {policyCase.summary.map((line) => (
              <p key={line}>{line}</p>
            ))}
          </div>

          <ul className="policyCaseFacts">
            {policyCase.facts.map((fact) => (
              <li key={fact.label}>
                <span>{fact.label}</span>
                <b>{fact.value}</b>
              </li>
            ))}
          </ul>

          {policyCase.caveat && <p className="policyCaseCaveat">{policyCase.caveat}</p>}

          <p className="policyCaseNote">
            정책 사례는 실제 시행 사례를 보여주며, 시뮬레이션 변화량 자체의 근거를 뜻하지
            않습니다.
          </p>
        </div>

        <footer className="policyCaseFooter">
          <span>
            출처 · {policyCase.source.name} ({policyCase.source.date})
          </span>
          <a href={preset.sourceUrl} target="_blank" rel="noreferrer">
            원문 보기 ↗
          </a>
        </footer>
      </div>
    </div>,
    document.body
  );
}

export default PolicyCaseModal;
