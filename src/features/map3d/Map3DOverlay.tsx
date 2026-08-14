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
  const canOpen3D = resolution === '100m' && Boolean(gridData?.features.length);

  useEffect(() => {
    // 새 시뮬레이션 결과는 즉시 After로 보이고, 결과 reset 시 Before로 복귀한다.
    setViewMode(batchSimulationResult ? 'after' : 'before');
  }, [batchSimulationResult]);

  useEffect(() => {
    if (!canOpen3D) setIs3DOpen(false);
  }, [canOpen3D]);

  return (
    <div className="map3dOverlay">
      {is3DOpen && canOpen3D && (
        <div className="map3dSurface">
          <Heat3DMap
            gridData={gridData}
            batchSimulationResult={batchSimulationResult}
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
          {batchSimulationResult && (
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
          )}
        </div>
      )}
      {canOpen3D && (
        <Map3DToggle
          is3DOpen={is3DOpen}
          onToggle={() => setIs3DOpen((isOpen) => !isOpen)}
        />
      )}
    </div>
  );
}
