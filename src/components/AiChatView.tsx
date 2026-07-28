import {
  Fragment,
  type FormEvent,
  type KeyboardEvent,
  useEffect,
  useRef,
  useState
} from 'react';
import { ApiRequestError, sendChatMessage } from '../services/api';
import type {
  AiChatChangedFeature,
  AiChatMessage,
  AiChatResponse,
  AiChatToolData,
  AiChatToolName
} from '../types/aiChat';

const CHAT_STORAGE_KEY = 'gaon_ai_chat_messages';
const TOOL_NAMES: readonly AiChatToolName[] = ['get_grid_data', 'run_simulation'];
const CHANGE_FIELDS = [
  'green_ratio',
  'impervious_ratio',
  'park_area_within_500m'
] as const;
const LOOKUP_FIELD_PATTERN = /^[a-z][a-z0-9_]*$/;
const UNSAFE_LOOKUP_FIELDS = new Set([
  'constructor',
  'prototype',
  'thinking',
  'content',
  'reasoning',
  'stack',
  'stack_trace',
  'traceback',
  'ollama',
  'ollama_response',
  'raw_response'
]);

const QUICK_QUESTIONS = [
  '이 격자의 녹지율과 불투수율을 알려줘.',
  '이 격자의 녹지율을 5%p 높이면 모델 기준 예상 변화량이 어떻게 돼?',
  '이 격자의 불투수율을 5%p 낮추면 모델 기준 예상 변화량이 어떻게 돼?'
] as const;

export interface AiChatViewProps {
  isActive: boolean;
  selectedGridId: string | null;
  selectedDisplayGridId: string | null;
  selectedGuName: string | null;
  onBack: () => void;
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value);
}

function finiteNumber(value: unknown): number | undefined {
  return typeof value === 'number' && Number.isFinite(value) ? value : undefined;
}

function nonEmptyString(value: unknown): string | undefined {
  if (typeof value !== 'string') return undefined;
  const normalized = value.trim();
  return normalized || undefined;
}

function sanitizeStringList(value: unknown): string[] {
  if (!Array.isArray(value)) return [];
  return value
    .map(nonEmptyString)
    .filter((item): item is string => item !== undefined);
}

function sanitizeToolNames(value: unknown): AiChatToolName[] {
  if (!Array.isArray(value)) return [];
  return value.filter(
    (item): item is AiChatToolName =>
      typeof item === 'string' && TOOL_NAMES.includes(item as AiChatToolName)
  );
}

function sanitizeRequestedChanges(value: unknown): Record<string, number> | undefined {
  if (!isRecord(value)) return undefined;

  const sanitized: Record<string, number> = {};
  for (const field of CHANGE_FIELDS) {
    const number = finiteNumber(value[field]);
    if (number !== undefined) sanitized[field] = number;
  }
  return Object.keys(sanitized).length > 0 ? sanitized : undefined;
}

function sanitizeAppliedChanges(
  value: unknown
): Record<string, AiChatChangedFeature> | undefined {
  if (!isRecord(value)) return undefined;

  const sanitized: Record<string, AiChatChangedFeature> = {};
  for (const field of CHANGE_FIELDS) {
    const candidate = value[field];
    if (!isRecord(candidate)) continue;
    const before = finiteNumber(candidate.before);
    const after = finiteNumber(candidate.after);
    if (before !== undefined && after !== undefined) {
      sanitized[field] = { before, after };
    }
  }
  return Object.keys(sanitized).length > 0 ? sanitized : undefined;
}

function sanitizeRequestedFields(value: unknown): string[] {
  if (!Array.isArray(value)) return [];

  const sanitized: string[] = [];
  const seen = new Set<string>();
  for (const item of value) {
    const field = nonEmptyString(item);
    if (
      field === undefined ||
      !LOOKUP_FIELD_PATTERN.test(field) ||
      UNSAFE_LOOKUP_FIELDS.has(field) ||
      seen.has(field)
    ) {
      continue;
    }
    seen.add(field);
    sanitized.push(field);
  }
  return sanitized;
}

function sanitizeLookupValues(
  value: unknown,
  requestedFields: string[]
): Record<string, number> | undefined {
  if (!isRecord(value)) return undefined;

  const sanitized: Record<string, number> = {};
  for (const field of requestedFields) {
    const number = finiteNumber(value[field]);
    if (number !== undefined) sanitized[field] = number;
  }
  return sanitized;
}

function sanitizeToolData(value: unknown): AiChatToolData | undefined {
  if (!isRecord(value)) return undefined;

  const sanitized: AiChatToolData = {};
  if (typeof value.success === 'boolean') sanitized.success = value.success;

  const gridId = nonEmptyString(value.grid_id);
  if (gridId !== undefined) sanitized.grid_id = gridId;

  const guName = nonEmptyString(value.gu_name);
  if (guName !== undefined) sanitized.gu_name = guName;

  const requestedFields = sanitizeRequestedFields(value.requested_fields);
  if (Array.isArray(value.requested_fields)) {
    sanitized.requested_fields = requestedFields;
  }

  const lookupValues = sanitizeLookupValues(value.values, requestedFields);
  if (lookupValues !== undefined) sanitized.values = lookupValues;

  for (const field of [
    'before_anomaly',
    'after_anomaly',
    'delta_c',
    'uncertainty_std',
    'delta_std'
  ] as const) {
    const number = finiteNumber(value[field]);
    if (number !== undefined) sanitized[field] = number;
  }

  const requestedChanges = sanitizeRequestedChanges(value.requested_changes);
  if (requestedChanges !== undefined) sanitized.requested_changes = requestedChanges;

  const appliedChanges = sanitizeAppliedChanges(value.applied_changes);
  if (appliedChanges !== undefined) sanitized.applied_changes = appliedChanges;

  const interpretationBasis = nonEmptyString(value.interpretation_basis);
  if (interpretationBasis !== undefined) {
    sanitized.interpretation_basis = interpretationBasis;
  }

  for (const field of ['warnings', 'limitations', 'policy_direction_notes'] as const) {
    const items = sanitizeStringList(value[field]);
    if (items.length > 0) sanitized[field] = items;
  }

  return Object.keys(sanitized).length > 0 ? sanitized : undefined;
}

function sanitizeStoredMessage(value: unknown): AiChatMessage | null {
  if (!isRecord(value) || (value.role !== 'user' && value.role !== 'assistant')) {
    return null;
  }

  const text = nonEmptyString(value.text);
  if (text === undefined) return null;

  if (value.role === 'user') {
    return { role: 'user', text };
  }

  const message: AiChatMessage = { role: 'assistant', text };
  const usedTools = sanitizeToolNames(value.used_tools);
  if (usedTools.length > 0) message.used_tools = usedTools;

  const toolData = sanitizeToolData(value.tool_data);
  if (toolData !== undefined) message.tool_data = toolData;

  const warnings = sanitizeStringList(value.warnings);
  if (warnings.length > 0) message.warnings = warnings;

  const limitations = sanitizeStringList(value.limitations);
  if (limitations.length > 0) message.limitations = limitations;

  return message;
}

function restoreMessages(): AiChatMessage[] {
  if (typeof window === 'undefined') return [];

  try {
    const raw = window.sessionStorage.getItem(CHAT_STORAGE_KEY);
    if (raw === null) return [];

    const parsed: unknown = JSON.parse(raw);
    if (!Array.isArray(parsed)) return [];
    return parsed
      .map(sanitizeStoredMessage)
      .filter((message): message is AiChatMessage => message !== null);
  } catch {
    return [];
  }
}

function removeStoredMessages(): void {
  if (typeof window === 'undefined') return;
  try {
    window.sessionStorage.removeItem(CHAT_STORAGE_KEY);
  } catch {
    // 브라우저 저장소가 차단된 경우에도 현재 세션의 React 상태는 계속 사용한다.
  }
}

function storeMessages(messages: AiChatMessage[]): void {
  if (typeof window === 'undefined') return;
  try {
    if (messages.length === 0) {
      window.sessionStorage.removeItem(CHAT_STORAGE_KEY);
    } else {
      window.sessionStorage.setItem(CHAT_STORAGE_KEY, JSON.stringify(messages));
    }
  } catch {
    // 저장 용량 제한이나 브라우저 정책은 채팅 동작 자체를 막지 않는다.
  }
}

function sanitizeResponse(response: AiChatResponse): AiChatMessage {
  const answer = nonEmptyString(response.answer);
  if (answer === undefined) {
    throw new Error('AI 응답 본문이 비어 있습니다.');
  }

  const message: AiChatMessage = { role: 'assistant', text: answer };
  const usedTools = sanitizeToolNames(response.used_tools);
  if (usedTools.length > 0) message.used_tools = usedTools;

  const toolData = sanitizeToolData(response.tool_data);
  if (toolData !== undefined) message.tool_data = toolData;

  const warnings = sanitizeStringList(response.warnings);
  if (warnings.length > 0) message.warnings = warnings;

  const limitations = sanitizeStringList(response.limitations);
  if (limitations.length > 0) message.limitations = limitations;

  return message;
}

function detailMessage(body: unknown): string | undefined {
  if (!isRecord(body)) return undefined;
  const detail = body.detail;
  if (typeof detail === 'string') return nonEmptyString(detail);
  if (isRecord(detail)) return nonEmptyString(detail.message);
  return undefined;
}

function userFacingError(error: unknown): string {
  if (!(error instanceof ApiRequestError)) {
    return 'GA:ON AI 요청을 처리하지 못했습니다. 잠시 후 다시 시도해 주세요.';
  }

  if (error.status === 400) {
    return detailMessage(error.body) ?? '질문 내용과 선택한 격자를 확인해 주세요.';
  }
  if (error.status === 503) {
    return 'GA:ON AI에 연결할 수 없습니다. Ollama 실행 상태와 qwen3:4b 모델을 확인해 주세요.';
  }
  if (error.status === 504) {
    return '모델 응답 시간이 초과되었습니다. 잠시 후 다시 시도해 주세요.';
  }
  return 'GA:ON AI 요청을 처리하지 못했습니다. 백엔드 서버 상태를 확인해 주세요.';
}

function uniqueItems(...groups: Array<string[] | undefined>): string[] {
  const result: string[] = [];
  const seen = new Set<string>();
  for (const item of groups.flatMap((group) => group ?? [])) {
    if (!seen.has(item)) {
      seen.add(item);
      result.push(item);
    }
  }
  return result;
}

function formatModelValue(value: number): string {
  return `${value.toLocaleString('ko-KR', {
    minimumFractionDigits: 3,
    maximumFractionDigits: 3
  })}℃`;
}

function formatDelta(value: number): string {
  const sign = value > 0 ? '+' : '';
  return `${sign}${formatModelValue(value)}`;
}

function deltaDirection(value: number): string {
  if (value > 0) return '증가';
  if (value < 0) return '감소';
  return '변화 없음';
}

function formatLookupValue(value: number): string {
  return value.toLocaleString('ko-KR', { maximumSignificantDigits: 8 });
}

function GridDataResultCard({ message }: { message: AiChatMessage }) {
  const data = message.tool_data;
  if (
    !message.used_tools?.includes('get_grid_data') ||
    data === undefined ||
    data.values === undefined ||
    data.requested_fields === undefined
  ) {
    return null;
  }

  const values = data.values;
  const fields = data.requested_fields.filter(
    (field) =>
      Object.prototype.hasOwnProperty.call(values, field) &&
      typeof values[field] === 'number'
  );
  if (fields.length === 0) return null;

  return (
    <div className="gdpSimulationResult">
      <div className="gdpSimulationDelta">
        <span>조회한 격자 데이터</span>
        <strong>
          {data.gu_name ? `${data.gu_name} · ` : ''}
          {data.grid_id ?? '선택 격자'}
        </strong>
      </div>
      <dl className="gdpSimulationRows">
        {fields.map((field) => (
          <Fragment key={field}>
            <dt>{field}</dt>
            <dd>{formatLookupValue(values[field])}</dd>
          </Fragment>
        ))}
      </dl>
    </div>
  );
}

function SimulationResultCard({ message }: { message: AiChatMessage }) {
  const data = message.tool_data;
  if (
    !message.used_tools?.includes('run_simulation') ||
    data === undefined ||
    typeof data.before_anomaly !== 'number' ||
    typeof data.after_anomaly !== 'number' ||
    typeof data.delta_c !== 'number'
  ) {
    return null;
  }

  return (
    <div className="gdpSimulationResult">
      <div className="gdpSimulationDelta">
        <span>모델 기준 예상 변화량</span>
        <strong>
          {formatDelta(data.delta_c)} {deltaDirection(data.delta_c)}
        </strong>
      </div>
      <dl className="gdpSimulationRows">
        {data.grid_id && (
          <>
            <dt>격자</dt>
            <dd>{data.gu_name ? `${data.gu_name} · ` : ''}{data.grid_id}</dd>
          </>
        )}
        <dt>변경 전 모델 예측 anomaly</dt>
        <dd>{formatModelValue(data.before_anomaly)}</dd>
        <dt>변경 후 모델 예측 anomaly</dt>
        <dd>{formatModelValue(data.after_anomaly)}</dd>
        {typeof data.delta_std === 'number' && (
          <>
            <dt>트리 간 변화량 편차</dt>
            <dd>{formatModelValue(data.delta_std)}</dd>
          </>
        )}
        {typeof data.uncertainty_std === 'number' && (
          <>
            <dt>트리 간 예측 편차</dt>
            <dd>{formatModelValue(data.uncertainty_std)}</dd>
          </>
        )}
      </dl>
      <p className="gdpChatNotice limitation">
        이 값들은 절대온도가 아니라 모델이 예측한 anomaly와 그 변화입니다.
      </p>
      {(typeof data.delta_std === 'number' ||
        typeof data.uncertainty_std === 'number') && (
        <p className="gdpChatNotice limitation">
          편차는 랜덤포레스트 개별 트리 예측값이 흩어진 정도입니다. 실제 오차범위나
          신뢰구간이 아닙니다.
        </p>
      )}
      {data.interpretation_basis && (
        <p className="gdpChatNotice limitation">{data.interpretation_basis}</p>
      )}
    </div>
  );
}

function ChatNotices({ message }: { message: AiChatMessage }) {
  const warnings = uniqueItems(
    message.warnings,
    message.tool_data?.warnings,
    message.tool_data?.policy_direction_notes
  );
  const limitations = uniqueItems(message.limitations, message.tool_data?.limitations);

  if (warnings.length === 0 && limitations.length === 0) return null;

  return (
    <>
      {warnings.map((warning) => (
        <p className="gdpChatNotice warning" key={`warning-${warning}`}>
          {warning}
        </p>
      ))}
      {limitations.map((limitation) => (
        <p className="gdpChatNotice limitation" key={`limitation-${limitation}`}>
          {limitation}
        </p>
      ))}
    </>
  );
}

export default function AiChatView({
  isActive,
  selectedGridId,
  selectedDisplayGridId,
  selectedGuName,
  onBack
}: AiChatViewProps) {
  const [messages, setMessages] = useState<AiChatMessage[]>(restoreMessages);
  const [input, setInput] = useState('');
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const messagesEndRef = useRef<HTMLDivElement | null>(null);
  const textareaRef = useRef<HTMLTextAreaElement | null>(null);
  const inFlightRef = useRef(false);
  const requestSequenceRef = useRef(0);
  const activeControllerRef = useRef<AbortController | null>(null);
  const contextGridId = nonEmptyString(selectedGridId) ?? null;
  const displayGridId = nonEmptyString(selectedDisplayGridId) ?? null;

  useEffect(() => {
    storeMessages(messages);
  }, [messages]);

  useEffect(() => {
    if (!isActive) {
      textareaRef.current?.blur();
      requestSequenceRef.current += 1;
      activeControllerRef.current?.abort();
      activeControllerRef.current = null;
      inFlightRef.current = false;
      setLoading(false);
      return;
    }
    messagesEndRef.current?.scrollIntoView({ block: 'end' });
  }, [isActive]);

  useEffect(() => {
    if (!isActive) return;
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth', block: 'end' });
  }, [messages, loading, error, isActive]);

  useEffect(
    () => () => {
      requestSequenceRef.current += 1;
      activeControllerRef.current?.abort();
    },
    []
  );

  async function submitMessage(rawMessage: string): Promise<void> {
    const message = rawMessage.trim();
    if (!isActive || !message || inFlightRef.current) return;

    const requestSequence = requestSequenceRef.current + 1;
    requestSequenceRef.current = requestSequence;
    const controller = new AbortController();
    activeControllerRef.current = controller;
    inFlightRef.current = true;
    setLoading(true);
    setError(null);
    setInput('');
    setMessages((current) => [...current, { role: 'user', text: message }]);

    try {
      const response = await sendChatMessage(
        {
          message,
          // 표시용 display_grid_id는 API 문맥으로 사용하지 않는다.
          selected_grid_id: contextGridId
        },
        controller.signal
      );
      if (requestSequence !== requestSequenceRef.current) return;
      const assistantMessage = sanitizeResponse(response);
      setMessages((current) => [...current, assistantMessage]);
    } catch (requestError) {
      if (
        requestSequence !== requestSequenceRef.current ||
        (requestError instanceof DOMException && requestError.name === 'AbortError')
      ) {
        return;
      }
      setError(userFacingError(requestError));
    } finally {
      if (requestSequence === requestSequenceRef.current) {
        inFlightRef.current = false;
        activeControllerRef.current = null;
        setLoading(false);
      }
    }
  }

  function resetChat(): void {
    requestSequenceRef.current += 1;
    activeControllerRef.current?.abort();
    activeControllerRef.current = null;
    inFlightRef.current = false;
    setLoading(false);
    setMessages([]);
    setInput('');
    setError(null);
    removeStoredMessages();
    textareaRef.current?.focus();
  }

  function handleSubmit(event: FormEvent<HTMLFormElement>): void {
    event.preventDefault();
    void submitMessage(input);
  }

  function handleKeyDown(event: KeyboardEvent<HTMLTextAreaElement>): void {
    if (
      event.key !== 'Enter' ||
      event.shiftKey ||
      event.nativeEvent.isComposing ||
      event.keyCode === 229
    ) {
      return;
    }
    event.preventDefault();
    void submitMessage(input);
  }

  const selectedContext = contextGridId
    ? `${selectedGuName ? `${selectedGuName} · ` : ''}${contextGridId}`
    : displayGridId
      ? `${selectedGuName ? `${selectedGuName} · ` : ''}${displayGridId} · 100m 격자를 선택해 주세요`
      : '100m 격자를 선택해 주세요';
  const quickQuestionsDisabled = !isActive || loading || contextGridId === null;

  return (
    <section className="gdpChat" hidden={!isActive} aria-hidden={!isActive}>
      <header className="gdpChatHeader">
        <button
          className="gdpChatBack"
          type="button"
          aria-label="대시보드로 돌아가기"
          onClick={onBack}
        >
          ←
        </button>
        <div className="gdpChatTitle">
          <strong>GA:ON AI</strong>
          <p className="gdpChatContext">{selectedContext}</p>
        </div>
        <button className="gdpChatReset" type="button" onClick={resetChat}>
          새 대화
        </button>
      </header>

      <div className="gdpChatBody">
        {messages.length === 0 && (
          <div className="gdpChatIntro">
            <p>격자 데이터를 조회하거나 정책 변경 시나리오를 질문해 보세요.</p>
            {!contextGridId && (
              <p>
                빠른 질문을 사용하려면 지도에서 분석 가능한 100m 격자를 먼저 선택해 주세요.
              </p>
            )}
            <div className="gdpQuickQuestions">
              {QUICK_QUESTIONS.map((question) => (
                <button
                  className="gdpQuickButton"
                  type="button"
                  key={question}
                  disabled={quickQuestionsDisabled}
                  onClick={() => void submitMessage(question)}
                >
                  {question}
                </button>
              ))}
            </div>
          </div>
        )}

        <div className="gdpChatMessages" role="log" aria-live="polite">
          {messages.map((message, index) => (
            <div
              className={`gdpChatMessage ${message.role}`}
              key={`${message.role}-${index}`}
            >
              <div className="gdpChatBubble">{message.text}</div>
              {message.role === 'assistant' && (
                <>
                  <GridDataResultCard message={message} />
                  <SimulationResultCard message={message} />
                  <ChatNotices message={message} />
                </>
              )}
            </div>
          ))}
          {loading && (
            <div className="gdpChatLoading" role="status">
              GA:ON AI가 모델 결과를 확인하고 있어요…
            </div>
          )}
          <div ref={messagesEndRef} />
        </div>

        {error && (
          <p className="gdpChatError" role="alert">
            {error}
          </p>
        )}
      </div>

      <form className="gdpChatComposer" onSubmit={handleSubmit}>
        <textarea
          className="gdpChatTextarea"
          ref={textareaRef}
          value={input}
          rows={3}
          disabled={!isActive || loading}
          placeholder="질문을 입력하세요. Shift+Enter로 줄을 바꿀 수 있어요."
          aria-label="GA:ON AI 질문"
          onChange={(event) => setInput(event.target.value)}
          onKeyDown={handleKeyDown}
        />
        <button
          className="gdpChatSend"
          type="submit"
          disabled={!isActive || loading || !input.trim()}
        >
          {loading ? '응답 중…' : '전송'}
        </button>
      </form>
    </section>
  );
}
