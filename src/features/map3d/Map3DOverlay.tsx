import { useState } from 'react';
import 'maplibre-gl/dist/maplibre-gl.css';
import Heat3DMap from './Heat3DMap';
import Map3DToggle from './Map3DToggle';
import './map3d.css';

export default function Map3DOverlay() {
  const [is3DOpen, setIs3DOpen] = useState(false);

  return (
    <div className="map3dOverlay">
      {is3DOpen && (
        <div className="map3dSurface">
          <Heat3DMap />
        </div>
      )}
      <Map3DToggle
        is3DOpen={is3DOpen}
        onToggle={() => setIs3DOpen((isOpen) => !isOpen)}
      />
    </div>
  );
}
