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
