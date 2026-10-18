/**
 * Map data builders.
 *
 * Turn analysis state into GeoJSON. Kept separate from the map component so the
 * shape can be unit-tested without a WebGL context, and so the slider's
 * re-colouring is a pure "rebuild these three small objects" rather than a
 * layer-by-layer mutation.
 */

import { RiskLevel, type Analysis, type CheckpointBundle, type RouteOption } from '@/types/api';
import { levelName, riskAtOffset, segmentMiles } from '@/lib/selectors';

export type FeatureCollection = GeoJSON.FeatureCollection;

export interface RouteProperties {
  id: string;
  label: string;
  index: number;
  rank: number;
  recommended: boolean;
  miles: number;
  hours: number;
}

export interface CheckpointProperties {
  id: string;
  route_id: string;
  distance_mi: number;
  eta_hour_index: number;
  level: number;
  level_name: string;
  dominant: string;
  wind: number | null;
  rain: number | null;
  snow: number | null;
  reasons: string[];
  data_quality: string;
}

export interface CorridorProperties {
  route_id: string;
  level: number;
  /** Weighted by severity so the heatmap concentrates on the bad stretches. */
  weight: number;
}

/** Heatmap weight per level. Severity should dominate the density. */
const HEAT_WEIGHT: Record<number, number> = {
  [RiskLevel.LOW]: 0.05,
  [RiskLevel.MODERATE]: 0.3,
  [RiskLevel.HIGH]: 0.55,
  [RiskLevel.SEVERE]: 0.85,
  [RiskLevel.NO_TRAVEL]: 1,
};

/** Route lines for all three options. */
export function buildRoutesCollection(analysis: Analysis): FeatureCollection {
  const ranking = analysis.recommendation.ranking;
  const features: GeoJSON.Feature[] = analysis.routes.map((route) => {
    const props: RouteProperties = {
      id: route.id,
      label: route.label,
      index: route.index,
      rank: ranking.indexOf(route.id),
      recommended: route.id === analysis.recommendation.route_id,
      miles: route.road_miles,
      hours: route.duration_s / 3600,
    };
    return {
      type: 'Feature',
      id: route.id,
      properties: props,
      geometry: { type: 'LineString', coordinates: route.geometry },
    };
  });
  return { type: 'FeatureCollection', features };
}

/**
 * Checkpoints at a departure offset, recoloured.
 *
 * `level` is recomputed from `hourly_risk` at the offset, which is what makes
 * dragging the slider repaint the dots without any new request.
 */
export function buildCheckpointsCollection(
  bundle: CheckpointBundle | undefined,
  route: RouteOption | undefined,
  offsetHours: number,
): FeatureCollection {
  if (!bundle || !route) return { type: 'FeatureCollection', features: [] };
  const features: GeoJSON.Feature[] = bundle.points.map((point, index) => {
    const level = riskAtOffset(point, offsetHours);
    const props: CheckpointProperties = {
      id: `${route.id}:${index}`,
      route_id: route.id,
      distance_mi: point.distance_mi,
      eta_hour_index: point.eta_hour_index,
      level,
      level_name: levelName(level),
      dominant: point.risk.dominant_factor,
      wind: point.risk.wind.value,
      rain: point.risk.rain.value,
      snow: point.risk.snow.value,
      reasons: point.risk.reasons,
      data_quality: point.risk.data_quality,
    };
    return {
      type: 'Feature',
      id: props.id,
      properties: props,
      geometry: { type: 'Point', coordinates: [point.lon, point.lat] },
    };
  });
  return { type: 'FeatureCollection', features };
}

/**
 * The corridor heatmap: one weighted line per checkpoint-owned stretch.
 *
 * A heatmap needs *area*, and a route is a hairline, so each checkpoint's owned
 * miles are emitted as a small square centred on it. Weighting by severity makes
 * the heat concentrate on genuinely dangerous stretches instead of smearing
 * evenly along the whole corridor.
 */
export function buildCorridorCollection(
  bundle: CheckpointBundle | undefined,
  route: RouteOption | undefined,
  offsetHours: number,
  halfWidthDeg = 0.035,
): FeatureCollection {
  if (!bundle || !route || bundle.points.length === 0) {
    return { type: 'FeatureCollection', features: [] };
  }
  // `summary` arrives as a separate stream event, so it can be missing while the
  // response is still in flight. Fall back to the last checkpoint's distance,
  // which is the route length to within one sampling interval.
  const miles = segmentMiles(bundle.points, route.summary?.total_miles ?? bundle.points.at(-1)?.distance_mi ?? 0);
  const widest = miles.length ? Math.max(...miles) : 25;

  const features: GeoJSON.Feature[] = bundle.points.map((point, index) => {
    const level = riskAtOffset(point, offsetHours);
    const weight = HEAT_WEIGHT[level] ?? 0;
    // Widen the cell for long stretches so density stays even along the route.
    const scale = (halfWidthDeg * Math.min(miles[index] ?? 0, widest)) / (widest || 1) + halfWidthDeg * 0.35;
    const props: CorridorProperties = {
      route_id: route.id,
      level,
      weight: weight * scale,
    };
    const [lon, lat] = [point.lon, point.lat];
    return {
      type: 'Feature',
      properties: props,
      geometry: {
        type: 'Polygon',
        coordinates: [
          [
            [lon - scale, lat - scale * 0.6],
            [lon + scale, lat - scale * 0.6],
            [lon + scale, lat + scale * 0.6],
            [lon - scale, lat + scale * 0.6],
            [lon - scale, lat - scale * 0.6],
          ],
        ],
      },
    };
  });
  return { type: 'FeatureCollection', features };
}

/** Origin/destination markers. */
export function buildEndpointsCollection(analysis: Analysis): FeatureCollection {
  const { origin, destination } = analysis.meta;
  if (!origin.display_name && !destination.display_name) {
    return { type: 'FeatureCollection', features: [] };
  }
  return {
    type: 'FeatureCollection',
    features: [
      {
        type: 'Feature',
        properties: { kind: 'origin', label: origin.display_name },
        geometry: { type: 'Point', coordinates: [origin.lon, origin.lat] },
      },
      {
        type: 'Feature',
        properties: { kind: 'destination', label: destination.display_name },
        geometry: { type: 'Point', coordinates: [destination.lon, destination.lat] },
      },
    ],
  };
}

/** `[west, south, east, north]` for a set of routes, or `null` when empty. */
export function boundsFor(analysis: Analysis): [number, number, number, number] | null {
  const coords = analysis.routes.flatMap((r) => r.geometry);
  if (coords.length === 0) return null;
  let west = Infinity;
  let south = Infinity;
  let east = -Infinity;
  let north = -Infinity;
  for (const [lon, lat] of coords) {
    west = Math.min(west, lon);
    east = Math.max(east, lon);
    south = Math.min(south, lat);
    north = Math.max(north, lat);
  }
  return [west, south, east, north];
}

// ---------------------------------------------------------------------------
// Risk strip (FE-15)
// ---------------------------------------------------------------------------

/** Great-circle distance in miles between two `[lon, lat]` points. */
function haversineMiles(a: [number, number], b: [number, number]): number {
  const R = 3958.7613;
  const toRad = (deg: number) => (deg * Math.PI) / 180;
  const dLat = toRad(b[1] - a[1]);
  const dLon = toRad(b[0] - a[0]);
  const h =
    Math.sin(dLat / 2) ** 2 +
    Math.cos(toRad(a[1])) * Math.cos(toRad(b[1])) * Math.sin(dLon / 2) ** 2;
  return 2 * R * Math.asin(Math.min(1, Math.sqrt(h)));
}

/** Total great-circle length of a geometry, in miles. */
function geometryMiles(geometry: [number, number][]): number {
  let total = 0;
  for (let i = 1; i < geometry.length; i += 1) {
    total += haversineMiles(geometry[i - 1], geometry[i]);
  }
  return total;
}

/**
 * Cumulative fraction along a geometry for each vertex.
 *
 * MapLibre's `line-progress` runs 0..1 along the drawn line, so a checkpoint's
 * position on the gradient has to be expressed as a fraction of the *geometry's*
 * length -- not of the checkpoint count, which is only even if the road bends
 * that way.
 */
export function lineProgress(geometry: [number, number][]): number[] {
  if (geometry.length < 2) return geometry.map(() => 0);
  const cumulative: number[] = [0];
  for (let i = 1; i < geometry.length; i += 1) {
    cumulative.push(cumulative[i - 1] + haversineMiles(geometry[i - 1], geometry[i]));
  }
  const total = cumulative[cumulative.length - 1] || 1;
  return cumulative.map((d) => d / total);
}

/** A single `line-gradient` stop: a fraction along the line, and its level. */
export interface RiskStop {
  progress: number;
  level: RiskLevel;
}

/**
 * Gradient stops for one route at a departure offset.
 *
 * Each checkpoint contributes a stop at its position along the line, coloured
 * by the level the truck meets there at `offsetHours`. Stop positions are
 * merged when two checkpoints land on the same fraction, otherwise a road
 * doubling back on itself would produce stops out of order and MapLibre would
 * silently drop the malformed expression.
 */
export function riskStripStops(
  bundle: CheckpointBundle | undefined,
  geometry: [number, number][],
  offsetHours: number,
): RiskStop[] {
  if (!bundle || bundle.points.length === 0 || geometry.length < 2) return [];

  const fractions = lineProgress(geometry);
  // Both are miles, so a checkpoint's road distance maps straight onto a
  // fraction of the drawn line.
  const lastFraction = fractions[fractions.length - 1] || 1;
  const roadToFraction = (distanceMi: number): number =>
    Math.min(1, Math.max(0, distanceMi * lastFraction / (geometryMiles(geometry) || 1)));

  const byProgress = new Map<number, RiskLevel>();
  for (const point of bundle.points) {
    const snapped = nearestStop(fractions, roadToFraction(point.distance_mi));
    byProgress.set(snapped, riskAtOffset(point, offsetHours));
  }

  // MapLibre requires strictly ascending stops.
  return [...byProgress.entries()]
    .map(([progress, level]) => ({ progress, level }))
    .sort((a, b) => a.progress - b.progress)
    .filter(
      (stop, index, all) => index === 0 || stop.progress > all[index - 1].progress,
    );
}

/** Nearest vertex fraction, snapped to 3 decimals so stops can be deduped. */
function nearestStop(cumulativeFractions: number[], target: number): number {
  let best = cumulativeFractions[0];
  let bestDelta = Math.abs(best - target);
  for (const candidate of cumulativeFractions) {
    const delta = Math.abs(candidate - target);
    if (delta < bestDelta) {
      best = candidate;
      bestDelta = delta;
    }
  }
  return Number(best.toFixed(3));
}

/**
 * The selected route's centreline, for the graduated risk strip (FE-15).
 *
 * A dedicated collection rather than reusing `routes`: `line-gradient` needs
 * `lineMetrics`, and enabling it on the shared source would disable line
 * simplification for all three routes. Paying that cost to colour one of them
 * would slow down the lines that matter most.
 */
export function buildRiskStripCollection(route: RouteOption | undefined): FeatureCollection {
  if (!route || route.geometry.length < 2) {
    return { type: 'FeatureCollection', features: [] };
  }
  return {
    type: 'FeatureCollection',
    features: [
      {
        type: 'Feature',
        id: route.id,
        properties: { id: route.id },
        geometry: { type: 'LineString', coordinates: route.geometry },
      },
    ],
  };
}
