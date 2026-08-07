import { readdir, readFile, writeFile } from 'node:fs/promises';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const scriptDirectory = path.dirname(fileURLToPath(import.meta.url));
const projectRoot = path.resolve(scriptDirectory, '..');
const sourceDirectory = path.join(projectRoot, 'public', 'dashboard', '100m');
const outputPath = path.join(projectRoot, 'public', 'dashboard', 'location_search.json');

function createBounds() {
  return {
    minLongitude: Number.POSITIVE_INFINITY,
    maxLongitude: Number.NEGATIVE_INFINITY,
    minLatitude: Number.POSITIVE_INFINITY,
    maxLatitude: Number.NEGATIVE_INFINITY
  };
}

function visitCoordinates(value, bounds) {
  if (!Array.isArray(value)) return;

  if (
    value.length >= 2 &&
    typeof value[0] === 'number' &&
    typeof value[1] === 'number' &&
    Number.isFinite(value[0]) &&
    Number.isFinite(value[1])
  ) {
    const [longitude, latitude] = value;
    bounds.minLongitude = Math.min(bounds.minLongitude, longitude);
    bounds.maxLongitude = Math.max(bounds.maxLongitude, longitude);
    bounds.minLatitude = Math.min(bounds.minLatitude, latitude);
    bounds.maxLatitude = Math.max(bounds.maxLatitude, latitude);
    return;
  }

  value.forEach((item) => visitCoordinates(item, bounds));
}

function hasFiniteBounds(bounds) {
  return (
    Number.isFinite(bounds.minLongitude) &&
    Number.isFinite(bounds.maxLongitude) &&
    Number.isFinite(bounds.minLatitude) &&
    Number.isFinite(bounds.maxLatitude)
  );
}

function boundsCenter(bounds) {
  return {
    longitude: (bounds.minLongitude + bounds.maxLongitude) / 2,
    latitude: (bounds.minLatitude + bounds.maxLatitude) / 2
  };
}

function findCenterGridId(group) {
  const dongCenter = boundsCenter(group.bounds);
  let nearest = null;

  for (const candidate of group.gridCenters) {
    const dx = candidate.longitude - dongCenter.longitude;
    const dy = candidate.latitude - dongCenter.latitude;
    const distanceSquared = dx * dx + dy * dy;

    if (
      nearest === null ||
      distanceSquared < nearest.distanceSquared ||
      (distanceSquared === nearest.distanceSquared && candidate.gridId < nearest.gridId)
    ) {
      nearest = { gridId: candidate.gridId, distanceSquared };
    }
  }

  return nearest?.gridId ?? null;
}

const files = (await readdir(sourceDirectory))
  .filter((file) => file.endsWith('.geojson'))
  .sort((left, right) => left.localeCompare(right, 'ko'));
const groups = new Map();

for (const file of files) {
  const collection = JSON.parse(await readFile(path.join(sourceDirectory, file), 'utf8'));

  for (const feature of collection.features ?? []) {
    const properties = feature.properties ?? {};
    const district = typeof properties.gu_name === 'string' ? properties.gu_name.trim() : '';
    const dongName = typeof properties.dong_name === 'string' ? properties.dong_name.trim() : '';
    const sigCode = String(properties.gu_code ?? '').trim();
    if (!district || !dongName || !/^\d{5}$/.test(sigCode)) continue;

    const gridId = String(properties.grid_id ?? '').trim();
    const featureBounds = createBounds();
    visitCoordinates(feature.geometry?.coordinates, featureBounds);

    const key = `${sigCode}\u0000${dongName}`;
    const group = groups.get(key) ?? {
      district,
      sigCode,
      dongName,
      gridCount: 0,
      bounds: createBounds(),
      gridCenters: []
    };

    visitCoordinates(feature.geometry?.coordinates, group.bounds);
    if (hasFiniteBounds(featureBounds) && /^\d{5}_\d{5}$/.test(gridId) && gridId.startsWith(`${sigCode}_`)) {
      group.gridCenters.push({ gridId, ...boundsCenter(featureBounds) });
    }
    group.gridCount += 1;
    groups.set(key, group);
  }
}

const dongs = [...groups.values()]
  .filter((group) => hasFiniteBounds(group.bounds))
  .map((group) => {
    const center = boundsCenter(group.bounds);
    return {
      district: group.district,
      sig_code: group.sigCode,
      dong_name: group.dongName,
      center: [Number(center.latitude.toFixed(6)), Number(center.longitude.toFixed(6))],
      grid_count: group.gridCount,
      center_grid_id: findCenterGridId(group)
    };
  })
  .sort(
    (left, right) =>
      left.district.localeCompare(right.district, 'ko') ||
      left.dong_name.localeCompare(right.dong_name, 'ko')
  );

const dongsWithoutCenterGrid = dongs.filter((dong) => !dong.center_grid_id);
if (dongsWithoutCenterGrid.length > 0) {
  throw new Error(
    `중심 격자를 찾지 못한 행정동 ${dongsWithoutCenterGrid.length}개: ${dongsWithoutCenterGrid
      .map((dong) => `${dong.sig_code} ${dong.dong_name}`)
      .join(', ')}`
  );
}

await writeFile(outputPath, `${JSON.stringify({ version: 2, dongs }, null, 2)}\n`, 'utf8');
process.stdout.write(`행정동 ${dongs.length}개 검색 인덱스 생성: ${outputPath}\n`);
