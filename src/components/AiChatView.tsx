import {
  Fragment,
  type FormEvent,
  type KeyboardEvent,
  useEffect,
  useRef,
  useState
} from 'react';
import {
  ApiRequestError,
  getAiFeatureCatalog,
  sendChatMessage
} from '../services/api';
import type {
  AiChatChangedFeature,
  AiFeatureCatalogItem,
  AiChatMessage,
  AiChatResponse,
  AiChatToolData,
  AiChatToolName
} from '../types/aiChat';

const CHAT_STORAGE_KEY = 'gaon_ai_chat_messages';
const GUIDE_COLLAPSED_STORAGE_KEY = 'gaon_ai_usage_guide_collapsed';
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

const LOOKUP_EXAMPLES = [
  '현재 데이터 전부 알려줘',
  '녹지가 차지하는 비율 알려줘',
  '건물들이 평균적으로 몇 층이야',
  '도로가 차지하는 정도 보여줘'
] as const;
const SIMULATION_EXAMPLES = [
  '녹지율을 5%p 높여줘',
  '식생지수를 0.05 높이면 어떻게 돼?',
  '불투수율을 3%p 낮추고 표면 반사율을 0.02 높여줘'
] as const;
const POLICY_RANKING_EXAMPLES = [
  '어떤 정책이 가장 효과적이야?',
  '뭐부터 해야 해?',
  '정책 우선순위 알려줘'
] as const;
const DOC_SEARCH_EXAMPLES = [
  '식생지수가 무슨 뜻이야?',
  '녹지율 데이터 출처가 어디야?',
  '왜 딥러닝을 안 쓰고 다른 방법을 골랐어?'
] as const;
const FEATURE_CATEGORY_ORDER = ['건축', '토지·용도', '녹지·피복', '공원·지형'] as const;

export interface AiChatViewProps {
  isActive: boolean;
  selectedGridId: string | null;
  selectedDisplayGridId: string | null;
  selectedGuName: string | null;
  onBack: () => void;
  /** 창이 닫힌 사이에 답변이 도착했을 때. 런처가 알림 점을 띄우는 데 쓴다. */
  onBackgroundReply?: () => void;
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

function restoreGuideCollapsed(): boolean {
  if (typeof window === 'undefined') return false;
  try {
    return window.localStorage.getItem(GUIDE_COLLAPSED_STORAGE_KEY) === 'true';
  } catch {
    return false;
  }
}

function storeGuideCollapsed(collapsed: boolean): void {
  if (typeof window === 'undefined') return;
  try {
    window.localStorage.setItem(
      GUIDE_COLLAPSED_STORAGE_KEY,
      collapsed ? 'true' : 'false'
    );
  } catch {
    // 저장소가 차단돼도 현재 화면의 접기/펼치기는 유지한다.
  }
}

function usesDrawerGuideLayout(): boolean {
  return (
    typeof window !== 'undefined' &&
    window.matchMedia('(max-width: 1180px)').matches
  );
}

function sanitizeFeatureCatalog(value: unknown): AiFeatureCatalogItem[] {
  if (!isRecord(value) || !Array.isArray(value.features)) return [];

  const features: AiFeatureCatalogItem[] = [];
  for (const item of value.features) {
    if (!isRecord(item)) continue;
    const name = nonEmptyString(item.name);
    const label = nonEmptyString(item.label);
    const description = nonEmptyString(item.description);
    const semanticDefinition = nonEmptyString(item.semantic_definition);
    const category = nonEmptyString(item.category);
    if (!name || !label || !description || !semanticDefinition || !category) continue;
    features.push({
      name,
      label,
      description,
      semantic_definition: semanticDefinition,
      unit: typeof item.unit === 'string' ? item.unit : '',
      category
    });
  }
  return features;
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

function AiUsageGuide({
  features,
  loading,
  error,
  selectedGridId,
  selectedGuName,
  mobileOpen,
  onClose,
  onExample
}: {
  features: AiFeatureCatalogItem[];
  loading: boolean;
  error: string | null;
  selectedGridId: string | null;
  selectedGuName: string | null;
  mobileOpen: boolean;
  onClose: () => void;
  onExample: (question: string) => void;
}) {
  return (
    <aside
      className={`gdpAiGuide${mobileOpen ? ' mobileOpen' : ''}`}
      aria-label="GA:ON AI 사용 가이드"
      role={mobileOpen ? 'dialog' : undefined}
      aria-modal={mobileOpen || undefined}
    >
      <div className="gdpAiGuideHeader">
        <div>
          <span>GA:ON AI</span>
          <strong>사용 가이드</strong>
        </div>
        <button type="button" onClick={onClose} aria-label="사용 가이드 닫기">
          ×
        </button>
      </div>

      <div className="gdpAiGuideBody">
        <section className="gdpGuideContext" aria-live="polite">
          <strong>선택 격자</strong>
          {selectedGridId ? (
            <p>
              {selectedGuName && <span>{selectedGuName}</span>}
              <b>{selectedGridId}</b>
            </p>
          ) : (
            <p>
              선택된 격자가 없습니다.
              <br />
              용어·데이터 출처 같은 질문은 그대로 물어보셔도 됩니다. 격자별
              데이터 조회와 정책 시뮬레이션은 대시보드에서 100m 격자를 먼저
              선택해 주세요.
            </p>
          )}
        </section>

        <section className="gdpGuideFeature">
          <div className="gdpGuideFeatureTitle">
            <span>기능 1</span>
            <strong>현재 데이터 조회</strong>
          </div>
          <p>
            선택한 격자의 도시환경 데이터를 조회합니다. 정확한 데이터명을 몰라도 의미가
            비슷한 표현으로 질문할 수 있습니다.
          </p>
          <div className="gdpGuideExamples">
            {LOOKUP_EXAMPLES.map((question) => (
              <button type="button" key={question} onClick={() => onExample(question)}>
                {question}
              </button>
            ))}
          </div>
        </section>

        <section className="gdpGuideFeature">
          <div className="gdpGuideFeatureTitle">
            <span>기능 2</span>
            <strong>정책 시뮬레이션</strong>
          </div>
          <p>변경할 지표, 증가·감소 방향, 수치를 함께 입력해 주세요.</p>
          <div className="gdpGuideLeverList" aria-label="지원 정책 레버">
            {['녹지율', '불투수율', '식생지수', '표면 반사율'].map((lever) => (
              <span key={lever}>{lever}</span>
            ))}
          </div>
          <div className="gdpGuideExamples">
            {SIMULATION_EXAMPLES.map((question) => (
              <button type="button" key={question} onClick={() => onExample(question)}>
                {question}
              </button>
            ))}
          </div>
        </section>

        <section className="gdpGuideFeature">
          <div className="gdpGuideFeatureTitle">
            <span>기능 3</span>
            <strong>정책 우선순위 추천</strong>
          </div>
          <p>정책 4개를 이 격자에 적용해 효과가 큰 순서로 알려드립니다.</p>
          <div className="gdpGuideExamples">
            {POLICY_RANKING_EXAMPLES.map((question) => (
              <button type="button" key={question} onClick={() => onExample(question)}>
                {question}
              </button>
            ))}
          </div>
        </section>

        <section className="gdpGuideFeature">
          <div className="gdpGuideFeatureTitle">
            <span>기능 4</span>
            <strong>모델·데이터 문서 질문</strong>
          </div>
          <p>지표의 뜻과 계산식, 모델 학습 방법과 한계를 문서에서 찾아 답합니다.</p>
          <div className="gdpGuideExamples">
            {DOC_SEARCH_EXAMPLES.map((question) => (
              <button type="button" key={question} onClick={() => onExample(question)}>
                {question}
              </button>
            ))}
          </div>
        </section>

        {/* 창을 옮길 수 있다는 걸 알려주는 곳이 헤더 title 뿐이라, 마우스를
            올려보지 않으면 모른다. 기능 설명 뒤에 짧게 덧붙인다.
            칸 너비가 180px 이라 문구를 길게 쓰면 네 줄로 늘어진다. 두 줄에
            맞춘 길이다(재보면 각각 154px·111px). 문구를 고칠 때 주의. */}
        <p className="gdpGuideTip">
          제목줄을 끌면 창을 옮길 수 있어요
          <br />
          두 번 누르면 원래 자리로
        </p>

        <details className="gdpGuideCatalog">
          <summary>
            조회 가능한 데이터 {features.length || 18}개
          </summary>
          {loading && <p role="status">데이터 목록을 불러오는 중입니다…</p>}
          {error && <p className="gdpGuideCatalogError">{error}</p>}
          {!loading &&
            !error &&
            FEATURE_CATEGORY_ORDER.map((category) => {
              const items = features.filter((feature) => feature.category === category);
              if (items.length === 0) return null;
              return (
                <section key={category}>
                  <strong>{category}</strong>
                  <ul>
                    {items.map((feature) => (
                      <li key={feature.name}>
                        <span>
                          {feature.label}
                          {feature.unit ? ` (${feature.unit})` : ''}
                        </span>
                        <small>{feature.description}</small>
                      </li>
                    ))}
                  </ul>
                </section>
              );
            })}
        </details>
      </div>
    </aside>
  );
}

export default function AiChatView({
  isActive,
  selectedGridId,
  selectedDisplayGridId,
  selectedGuName,
  onBack,
  onBackgroundReply
}: AiChatViewProps) {
  const [messages, setMessages] = useState<AiChatMessage[]>(restoreMessages);
  const [input, setInput] = useState('');
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [draftNotice, setDraftNotice] = useState<string | null>(null);
  const [guideCollapsed, setGuideCollapsed] = useState(restoreGuideCollapsed);
  const [mobileGuideOpen, setMobileGuideOpen] = useState(false);
  const [features, setFeatures] = useState<AiFeatureCatalogItem[]>([]);
  const [featuresLoading, setFeaturesLoading] = useState(false);
  const [featuresError, setFeaturesError] = useState<string | null>(null);
  const messagesEndRef = useRef<HTMLDivElement | null>(null);
  const textareaRef = useRef<HTMLTextAreaElement | null>(null);
  const inFlightRef = useRef(false);
  const requestSequenceRef = useRef(0);
  const activeControllerRef = useRef<AbortController | null>(null);
  // 응답이 돌아오는 시점의 열림/닫힘 상태를 알아야 한다.
  // submitMessage의 클로저에 갇힌 isActive는 '보낼 때' 값이라 항상 true다.
  const isActiveRef = useRef(isActive);
  useEffect(() => {
    isActiveRef.current = isActive;
  }, [isActive]);
  const contextGridId = nonEmptyString(selectedGridId) ?? null;
  const displayGridId = nonEmptyString(selectedDisplayGridId) ?? null;

  useEffect(() => {
    storeMessages(messages);
  }, [messages]);

  useEffect(() => {
    storeGuideCollapsed(guideCollapsed);
  }, [guideCollapsed]);

  useEffect(() => {
    if (!isActive || features.length > 0) return;
    let active = true;
    setFeaturesLoading(true);
    setFeaturesError(null);
    getAiFeatureCatalog()
      .then((response) => {
        if (!active) return;
        const sanitized = sanitizeFeatureCatalog(response);
        if (sanitized.length === 0) {
          throw new Error('조회 데이터 목록이 비어 있습니다.');
        }
        setFeatures(sanitized);
      })
      .catch(() => {
        if (active) {
          setFeaturesError('조회 데이터 목록을 불러오지 못했습니다.');
        }
      })
      .finally(() => {
        if (active) setFeaturesLoading(false);
      });
    return () => {
      active = false;
    };
  }, [features.length, isActive]);

  useEffect(() => {
    if (!isActive) {
      // ★ 창을 닫아도 진행 중인 요청은 건드리지 않는다.
      //   예전에는 여기서 abort + requestSequence 증가로 응답을 버렸다. 그때는 채팅이
      //   상세 패널의 '탭'이라 다른 탭으로 넘어가는 것 = 화면을 떠나는 것이었기 때문이다.
      //   지금은 떠 있는 창을 잠시 접는 것뿐이라, 답이 도착하면 목록에 쌓여 있어야 한다.
      //   (요청 취소는 '대화 초기화'와 언마운트에서만 한다)
      textareaRef.current?.blur();
      setMobileGuideOpen(false);
      return;
    }
    messagesEndRef.current?.scrollIntoView({ block: 'end' });
  }, [isActive]);

  useEffect(() => {
    if (!isActive || !mobileGuideOpen) return;
    const handleKeyDown = (event: globalThis.KeyboardEvent) => {
      if (event.key === 'Escape') setMobileGuideOpen(false);
    };
    window.addEventListener('keydown', handleKeyDown);
    return () => window.removeEventListener('keydown', handleKeyDown);
  }, [isActive, mobileGuideOpen]);

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

  function focusComposer(): void {
    window.requestAnimationFrame(() => textareaRef.current?.focus());
  }

  function fillExample(question: string): void {
    const currentDraft = input.trim();
    if (currentDraft && currentDraft !== question) {
      setDraftNotice('작성 중인 질문이 있어 예시로 덮어쓰지 않았습니다.');
      focusComposer();
      return;
    }
    setInput(question);
    setDraftNotice(null);
    setMobileGuideOpen(false);
    focusComposer();
  }

  function toggleGuide(): void {
    if (usesDrawerGuideLayout()) {
      setMobileGuideOpen((current) => !current);
      return;
    }
    setGuideCollapsed((current) => !current);
  }

  function closeGuide(): void {
    if (usesDrawerGuideLayout()) {
      setMobileGuideOpen(false);
      return;
    }
    setGuideCollapsed(true);
  }

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
    setDraftNotice(null);
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
      if (!isActiveRef.current) onBackgroundReply?.();
    } catch (requestError) {
      if (
        requestSequence !== requestSequenceRef.current ||
        (requestError instanceof DOMException && requestError.name === 'AbortError')
      ) {
        return;
      }
      setError(userFacingError(requestError));
      if (!isActiveRef.current) onBackgroundReply?.();
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
    setDraftNotice(null);
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
  return (
    <section
      className={`gdpAiWorkspace${guideCollapsed ? ' guideCollapsed' : ''}`}
      hidden={!isActive}
      aria-hidden={!isActive}
    >
      {mobileGuideOpen && (
        <button
          className="gdpAiGuideBackdrop"
          type="button"
          aria-label="사용 가이드 닫기"
          onClick={() => setMobileGuideOpen(false)}
        />
      )}
      <AiUsageGuide
        features={features}
        loading={featuresLoading}
        error={featuresError}
        selectedGridId={contextGridId}
        selectedGuName={selectedGuName}
        mobileOpen={mobileGuideOpen}
        onClose={closeGuide}
        onExample={fillExample}
      />

      <section className="gdpChat">
      {/* 이 헤더가 창을 옮기는 손잡이다. 실제 드래그는 AiChatLauncher 가 붙인다
          (창 위치는 창을 소유한 쪽이 관리해야 해서). */}
      <header
        className="gdpChatHeader"
        title="끌어서 옮기기 · 두 번 누르면 원래 자리로"
      >
        <button
          className="gdpChatBack"
          type="button"
          aria-label="채팅 닫기"
          onClick={onBack}
        >
          ✕
        </button>
        <div className="gdpChatTitle">
          <strong>GA:ON AI</strong>
          <p className="gdpChatContext">{selectedContext}</p>
        </div>
        <button className="gdpGuideToggle" type="button" onClick={toggleGuide}>
          사용 가이드
        </button>
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
                질문을 실행하려면 지도에서 분석 가능한 100m 격자를 먼저 선택해 주세요.
              </p>
            )}
            <p>사용 가이드의 예시는 자동 전송 없이 입력창에 채워집니다.</p>
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

      {draftNotice && (
        <p className="gdpDraftNotice" role="status">
          {draftNotice}
        </p>
      )}
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
    </section>
  );
}
