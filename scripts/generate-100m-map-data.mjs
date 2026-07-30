import { readFile, writeFile } from 'node:fs/promises';

const sourcePath = new URL('../public/dashboard/seoul_grid_100m.geojson', import.meta.url);
const outputPath = new URL('../public/dashboard/seoul_grid_100m_map.geojson', import.meta.url);

const MAP_PROPERTY_KEYS = [
  'grid_id',
  'gu_code',
  'gu_name',
  'area_m2',
  'priority_score',
  'mean_actual_anomaly',
  'mean_actual_lst',
  'green_delta_c',
  'green_ratio',
  'ndvi',
  'building_ratio',
  'impervious_ratio',
  'nearest_shelter_distance_m'
];

const source = JSON.parse(await readFile(sourcePath, 'utf8'));
const mapData = {
  type: 'FeatureCollection',
  features: source.features.map((feature) => ({
    type: 'Feature',
    properties: Object.fromEntries(
      MAP_PROPERTY_KEYS.flatMap((key) =>
        Object.hasOwn(feature.properties, key) ? [[key, feature.properties[key]]] : []
      )
    ),
    geometry: feature.geometry
  }))
};

await writeFile(outputPath, JSON.stringify(mapData));

const megabytes = Buffer.byteLength(JSON.stringify(mapData)) / 1024 / 1024;
console.log(`Generated ${mapData.features.length.toLocaleString()} features (${megabytes.toFixed(1)} MB)`);
