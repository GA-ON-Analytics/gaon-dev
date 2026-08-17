import { useEffect } from 'react';
import type { FeatureCollection } from 'geojson';
import 'maplibre-gl/dist/maplibre-gl.css';
import type { BatchSimulationResponse, GridResolution } from '../../types/dashboard';
import Heat3DMap, {
  type Map3DClearSelectedGridHandler,
  type Map3DDistrictDrillDownHandler,
  type Map3DGridFeatureClickHandler,
  type Map3DGridInfoSign,
  type Map3DShelterSign,
  type Map3DViewMode
} from './Heat3DMap';
import Map3DToggle from './Map3DToggle';
import './map3d.css';

interface Map3DOverlayProps {
  is3DOpen: boolean;
  on3DOpenChange: (isOpen: boolean) => void;
  gridData: FeatureCollection | null;
  batchSimulationResult: BatchSimulationResponse | null;
  viewMode: Map3DViewMode;
  onViewModeChange: (viewMode: Map3DViewMode) => void;
  resolution: GridResolution;
  selectedDistrict: string;
  selectedGridId: string | null;
  gridInfoSign: Map3DGridInfoSign | null;
  shelterSign: Map3DShelterSign | null;
  onGridFeatureClick: Map3DGridFeatureClickHandler;
  onDistrictDrillDown: Map3DDistrictDrillDownHandler;
  onClearSelectedGrid: Map3DClearSelectedGridHandler;
}

export default function Map3DOverlay({
  is3DOpen,
  on3DOpenChange,
  gridData,
  batchSimulationResult,
  viewMode,
  onViewModeChange,
  resolution,
  selectedDistrict,
  selectedGridId,
  gridInfoSign,
  shelterSign,
  onGridFeatureClick,
  onDistrictDrillDown,
  onClearSelectedGrid
}: Map3DOverlayProps) {
  const supports3DResolution =
    resolution === '100m' || resolution === '250m' || resolution === '500m';
  const has3DGridData = Boolean(gridData?.features.length);
  const canOpen3D = supports3DResolution && has3DGridData;
  // 부모가 resolution·district·selected ID까지 검증한 현재 context 결과만 전달한다.
  const activeBatchSimulationResult = batchSimulationResult;
  const contextLabel = `${
    selectedDistrict === '전체' ? '서울시 전체' : selectedDistrict
  } · ${resolution} 열 분포`;

  useEffect(() => {
    if (!supports3DResolution && is3DOpen) on3DOpenChange(false);
  }, [is3DOpen, on3DOpenChange, supports3DResolution]);

  return (
    <div className="map3dOverlay">
      {is3DOpen && supports3DResolution && (
        <div className="map3dSurface">
          <Heat3DMap
            gridData={gridData}
            batchSimulationResult={activeBatchSimulationResult}
            viewMode={viewMode}
            resolution={resolution}
            selectedDistrict={selectedDistrict}
            selectedGridId={selectedGridId}
            gridInfoSign={gridInfoSign}
            shelterSign={shelterSign}
            onGridFeatureClick={onGridFeatureClick}
            onDistrictDrillDown={onDistrictDrillDown}
            onClearSelectedGrid={onClearSelectedGrid}
          />
        </div>
      )}
      {/* 헤더는 3D surface 밖에 둔다. 2D도 같은 결과로 색을 칠하므로 전후 토글이
          두 모드에 다 필요하고, 3D를 열지 않아도 보여야 한다.
          2D에서는 정책 결과가 있을 때만 띄워서 평소 지도를 가리지 않는다. */}
      {(is3DOpen || activeBatchSimulationResult) && (
        <div className="map3dViewHeader">
          <div
            className="map3dContextLabel"
            aria-label={`현재 ${is3DOpen ? '3D' : '2D'} 분석 범위: ${contextLabel}`}
          >
            {contextLabel}
          </div>
          {activeBatchSimulationResult && (
            <div
              className="map3dPolicyViewControl"
              role="group"
              aria-label="정책 시뮬레이션 전후 비교"
            >
              <button
                type="button"
                className={viewMode === 'before' ? 'isActive' : ''}
                aria-pressed={viewMode === 'before'}
                onClick={() => onViewModeChange('before')}
              >
                현재
              </button>
              <button
                type="button"
                className={viewMode === 'after' ? 'isActive' : ''}
                aria-pressed={viewMode === 'after'}
                onClick={() => onViewModeChange('after')}
              >
                정책 적용 후
              </button>
            </div>
          )}
        </div>
      )}
      {supports3DResolution && (canOpen3D || is3DOpen) && (
        <Map3DToggle
          is3DOpen={is3DOpen}
          onToggle={() => on3DOpenChange(!is3DOpen)}
        />
      )}
    </div>
  );
}
