import { useEffect, useState } from 'react';
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
  gridData: FeatureCollection | null;
  batchSimulationResult: BatchSimulationResponse | null;
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
  gridData,
  batchSimulationResult,
  resolution,
  selectedDistrict,
  selectedGridId,
  gridInfoSign,
  shelterSign,
  onGridFeatureClick,
  onDistrictDrillDown,
  onClearSelectedGrid
}: Map3DOverlayProps) {
  const [is3DOpen, setIs3DOpen] = useState(false);
  const [viewMode, setViewMode] = useState<Map3DViewMode>('before');
  const supports3DResolution =
    resolution === '100m' || resolution === '250m' || resolution === '500m';
  const has3DGridData = Boolean(gridData?.features.length);
  const canOpen3D = supports3DResolution && has3DGridData;
  // 정책 결과와 Before/After는 실제 100m ML context에서만 유효하다.
  const activeBatchSimulationResult =
    resolution === '100m' ? batchSimulationResult : null;
  const contextLabel = `${
    selectedDistrict === '전체' ? '서울시 전체' : selectedDistrict
  } · ${resolution} 열 분포`;

  useEffect(() => {
    // 새 시뮬레이션 결과는 즉시 After로 보이고, 결과 reset 시 Before로 복귀한다.
    setViewMode(activeBatchSimulationResult ? 'after' : 'before');
  }, [activeBatchSimulationResult]);

  useEffect(() => {
    if (!supports3DResolution) setIs3DOpen(false);
  }, [supports3DResolution]);

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
          <div className="map3dViewHeader">
            <div className="map3dContextLabel" aria-label={`현재 3D 분석 범위: ${contextLabel}`}>
              {contextLabel}
            </div>
            {activeBatchSimulationResult && (
              <>
                <div
                  className="map3dPolicyViewControl"
                  role="group"
                  aria-label="정책 시뮬레이션 전후 비교"
                >
                  <button
                    type="button"
                    className={viewMode === 'before' ? 'isActive' : ''}
                    aria-pressed={viewMode === 'before'}
                    onClick={() => setViewMode('before')}
                  >
                    현재
                  </button>
                  <button
                    type="button"
                    className={viewMode === 'after' ? 'isActive' : ''}
                    aria-pressed={viewMode === 'after'}
                    onClick={() => setViewMode('after')}
                  >
                    정책 적용 후
                  </button>
                </div>
                <p className="map3dPolicyScenarioNote">
                  정책 적용 후는 현재 관측 LST에 모델 예측 변화량을 반영한 시나리오이며
                  실제 미래 관측값이 아닙니다.
                </p>
              </>
            )}
          </div>
        </div>
      )}
      {supports3DResolution && (canOpen3D || is3DOpen) && (
        <Map3DToggle
          is3DOpen={is3DOpen}
          onToggle={() => setIs3DOpen((isOpen) => !isOpen)}
        />
      )}
    </div>
  );
}
