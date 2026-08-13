import type { FeatureCollection } from 'geojson';
import type {
  BatchSimulationResponse,
  DongSearchIndex,
  GridResolution,
  PolicyPresetsResponse,
  SimulationRequest,
  SimulationBatchScope,
  SimulationResponse
} from '../types/dashboard';
import type {
  AiChatRequest,
  AiChatResponse,
  AiFeatureCatalogResponse
} from '../types/aiChat';

const API_BASE_URL = import.meta.env.VITE_API_BASE_URL ?? '';

export class ApiRequestError extends Error {
  status: number;
  body: unknown;

  constructor(path: string, status: number, body: unknown) {
    super(`${path} 요청 실패: ${status}`);
    this.name = 'ApiRequestError';
    this.status = status;
    this.body = body;
  }
}

async function readResponseBody(response: Response): Promise<unknown> {
  const contentType = response.headers.get('content-type') ?? '';

  try {
    if (contentType.includes('application/json')) {
      return await response.json();
    }

    return await response.text();
  } catch {
    return null;
  }
}

async function fetchJson<T>(path: string): Promise<T> {
  const response = await fetch(`${API_BASE_URL}${path}`);

  if (!response.ok) {
    throw new ApiRequestError(path, response.status, await readResponseBody(response));
  }

  return response.json() as Promise<T>;
}

async function fetchPublicJson<T>(path: string): Promise<T> {
  const response = await fetch(path);

  if (!response.ok) {
    throw new ApiRequestError(path, response.status, await readResponseBody(response));
  }

  return response.json() as Promise<T>;
}

async function fetchJsonWithFallback<T>(path: string, fallbackPath: string): Promise<T> {
  try {
    return await fetchJson<T>(path);
  } catch {
    return fetchJson<T>(fallbackPath);
  }
}

async function postJson<TResponse, TBody>(
  path: string,
  body: TBody,
  signal?: AbortSignal
): Promise<TResponse> {
  const response = await fetch(`${API_BASE_URL}${path}`, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json'
    },
    body: JSON.stringify(body),
    signal
  });

  if (!response.ok) {
    throw new ApiRequestError(path, response.status, await readResponseBody(response));
  }

  return response.json() as Promise<TResponse>;
}

export function getGeoJson(): Promise<FeatureCollection> {
  return fetchJsonWithFallback<FeatureCollection>('/api/seoul-gu', '/seoul_gu.geojson');
}

/** 기존 100m 상세 GeoJSON의 행정동 필드로 생성한 정적 검색 인덱스. */
export function getDongSearchIndex(): Promise<DongSearchIndex> {
  return fetchPublicJson<DongSearchIndex>('/dashboard/location_search.json');
}

export function getDistrictGrid(sigCode: string): Promise<FeatureCollection> {
  return fetchJson<FeatureCollection>(`/api/grids/${sigCode}`);
}

export function getResolutionGrid(resolution: GridResolution): Promise<FeatureCollection> {
  if (resolution === '100m') {
    return fetchJsonWithFallback<FeatureCollection>(
      '/api/dashboard/grids/100m/map',
      '/dashboard/seoul_grid_100m_map.geojson'
    );
  }

  return fetchJsonWithFallback<FeatureCollection>(
    `/api/dashboard/grids/${resolution}`,
    `/dashboard/seoul_grid_${resolution}.geojson`
  );
}

export function getDistrictResolutionGrid(
  resolution: GridResolution,
  sigCode: string,
  district: string
): Promise<FeatureCollection> {
  if (resolution === '100m') {
    return fetchJsonWithFallback<FeatureCollection>(
      `/api/grids/${sigCode}`,
      `/dashboard/100m/${sigCode}_${district}.geojson`
    );
  }

  return fetchJsonWithFallback<FeatureCollection>(
    `/api/dashboard/grids/${resolution}/${sigCode}`,
    resolution === '500m'
      ? '/dashboard/seoul_grid_500m.geojson'
      : `/dashboard/${resolution}/${sigCode}_${district}.geojson`
  );
}

export function getOverviewGrid(): Promise<FeatureCollection> {
  return fetchJsonWithFallback<FeatureCollection>(
    '/api/dashboard/500m',
    '/dashboard/seoul_grid_500m.geojson'
  );
}

export function simulateGridPolicy(payload: SimulationRequest): Promise<SimulationResponse> {
  return postJson<SimulationResponse, SimulationRequest>('/api/simulate', payload);
}

export function sendChatMessage(
  payload: AiChatRequest,
  signal?: AbortSignal
): Promise<AiChatResponse> {
  return postJson<AiChatResponse, AiChatRequest>('/api/chat', payload, signal);
}

export function getAiFeatureCatalog(): Promise<AiFeatureCatalogResponse> {
  return fetchJson<AiFeatureCatalogResponse>('/api/features');
}

// 정책 정의는 백엔드가 원본이다. 챗봇도 같은 정의를 읽으므로 화면과 챗봇이
// 서로 다른 변화량을 말할 수 없다.
export function getPolicyPresets(): Promise<PolicyPresetsResponse> {
  return fetchJson<PolicyPresetsResponse>('/api/policies');
}

// 여러 100m 격자에 같은 정책을 적용해 평균 예측 변화를 구한다 (250/500m 집계 격자용).
export function simulateBatchGridPolicy(
  gridIds: string[],
  changes: Record<string, number>,
  coupleLandCover = true
): Promise<BatchSimulationResponse> {
  return postJson<
    BatchSimulationResponse,
    { grid_ids: string[]; changes: Record<string, number>; couple_land_cover: boolean }
  >('/api/simulate/batch', {
    grid_ids: gridIds,
    changes,
    couple_land_cover: coupleLandCover
  });
}

// 중심 100m 격자와 실제 공간 범위를 보내면 backend가 자치구 경계와 무관하게 대상 셀을 찾는다.
export function simulateScopedGridPolicy(
  gridId: string,
  scopeM: SimulationBatchScope,
  changes: Record<string, number>,
  coupleLandCover = true
): Promise<BatchSimulationResponse> {
  return postJson<
    BatchSimulationResponse,
    {
      grid_id: string;
      scope_m: SimulationBatchScope;
      changes: Record<string, number>;
      couple_land_cover: boolean;
    }
  >('/api/simulate/batch', {
    grid_id: gridId,
    scope_m: scopeM,
    changes,
    couple_land_cover: coupleLandCover
  });
}
