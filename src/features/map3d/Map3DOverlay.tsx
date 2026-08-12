import { useState } from 'react';
import type { FeatureCollection } from 'geojson';
import 'maplibre-gl/dist/maplibre-gl.css';
import type { GridResolution } from '../../types/dashboard';
import Heat3DMap from './Heat3DMap';
import Map3DToggle from './Map3DToggle';
import './map3d.css';

interface Map3DOverlayProps {
  gridData: FeatureCollection | null;
  resolution: GridResolution;
  selectedDistrict: string;
}

export default function Map3DOverlay({
  gridData,
  resolution,
  selectedDistrict
}: Map3DOverlayProps) {
  const [is3DOpen, setIs3DOpen] = useState(false);

  return (
    <div className="map3dOverlay">
      {is3DOpen && (
        <div className="map3dSurface">
          <Heat3DMap
            gridData={gridData}
            resolution={resolution}
            selectedDistrict={selectedDistrict}
          />
        </div>
      )}
      <Map3DToggle
        is3DOpen={is3DOpen}
        onToggle={() => setIs3DOpen((isOpen) => !isOpen)}
      />
    </div>
  );
}
