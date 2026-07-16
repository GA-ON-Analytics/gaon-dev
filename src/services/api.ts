import type { FeatureCollection } from 'geojson';
import type { GridResolution, SimulationRequest, SimulationResponse } from '../types/dashboard';

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

async function fetchJsonWithFallback<T>(path: string, fallbackPath: string): Promise<T> {
  try {
    return await fetchJson<T>(path);
  } catch {
    return fetchJson<T>(fallbackPath);
  }
}

async function postJson<TResponse, TBody>(path: string, body: TBody): Promise<TResponse> {
  const response = await fetch(`${API_BASE_URL}${path}`, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json'
    },
    body: JSON.stringify(body)
  });

  if (!response.ok) {
    throw new ApiRequestError(path, response.status, await readResponseBody(response));
  }

  return response.json() as Promise<TResponse>;
}

export function getGeoJson(): Promise<FeatureCollection> {
  return fetchJsonWithFallback<FeatureCollection>('/api/seoul-gu', '/seoul_gu.geojson');
}

export function getDistrictGrid(sigCode: string): Promise<FeatureCollection> {
  return fetchJson<FeatureCollection>(`/api/grids/${sigCode}`);
}

export function getResolutionGrid(resolution: GridResolution): Promise<FeatureCollection> {
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
    return getDistrictGrid(sigCode);
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
