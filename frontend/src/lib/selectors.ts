/**
 * Pure derived selectors.
 *
 * Everything here is a pure function of the analysis payload plus a departure
 * offset. Two consequences that matter:
 *
 * 1. **The slider costs nothing.** Level lookup is an array index; the summary is
 *    a weighted sum over checkpoints. No network call, no React re-render of the
 *    map, no rule logic duplicated into the browser (D7/D12/D13).
 * 2. **They are trivially testable.** No store, no React, no fetch.
 */

import {
  RiskLevel,
  type Analysis,
  type Checkpoint,
  type CheckpointBundle,
  type RouteOption,
  type RouteSummary,
} from '@/types/api';

/**
 * Risk at a given departure offset.
 *
 * The offset shifts the *departure*, so the truck reaches this point `offset`
 * hours later and is exposed to whatever the weather is doing then. That is
 * exactly `hourly_risk[eta_hour_index + offset]`.
 */
export function riskAtOffset(checkpoint: Checkpoint, offsetHours: number): RiskLevel {
  const levels = checkpoint.hourly_risk;
  if (levels.length === 0) return checkpoint.risk.level_value;
  const index = checkpoint.eta_hour_index + offsetHours;
  // Clamp rather than wrap: a checkpoint stays at the last known hour rather
  // than teleporting back to the start of the window.
  const clamped = Math.max(0, Math.min(index, levels.length - 1));
  return levels[clamped];
}

/** ETA shifted by the departure offset. Uniform across every checkpoint (D13). */
export function etaAtOffset(checkpoint: Checkpoint, offsetHours: number): Date {
  return new Date(new Date(checkpoint.eta).getTime() + offsetHours * 3_600_000);
}

/**
 * Segment miles owned by a checkpoint.
 *
 * Must match the backend's midpoint attribution exactly (D4), or the live
 * summary would disagree with the server's. The backend hands us total route
 * miles and the checkpoint distances; the boundaries are the midpoints between
 * consecutive checkpoints, which is reproducible here.
 */
export function segmentMiles(points: Checkpoint[], totalMiles: number): number[] {
  if (points.length === 0) return [];
  const distances = points.map((p) => p.distance_mi);
  return distances.map((distance, index) => {
    const start =
      index === 0 ? 0 : (distances[index - 1] + distance) / 2;
    const end =
      index === points.length - 1
        ? totalMiles
        : (distance + distances[index + 1]) / 2;
    return Math.max(0, end - start);
  });
}

/** The summary for one route at a given departure offset. */
export function summaryAtOffset(
  bundle: CheckpointBundle | undefined,
  route: RouteOption | undefined,
  offsetHours: number,
): RouteSummary {
  const empty: RouteSummary = {
    // `summary` arrives as a separate stream event, so a route can exist without
    // one while the response is still in flight. Every read of it is optional.
    total_miles: route?.summary?.total_miles ?? 0,
    miles_by_level: {},
    no_travel_miles: 0,
    severe_miles: 0,
    high_miles: 0,
    moderate_miles: 0,
    low_miles: 0,
    avg_risk: 0,
    worst_level: 'LOW',
    adverse_miles: 0,
  };
  // Without the server's total route miles the weighted average cannot be
  // computed meaningfully, so report "nothing yet" rather than a half-filled
  // summary that would disagree with the numbers the server sends later.
  if (!bundle || !route || !route.summary || bundle.points.length === 0) return empty;

  const miles = segmentMiles(bundle.points, route.summary?.total_miles ?? 0);
  const byLevel: Record<string, number> = {};
  let weighted = 0;
  let worst = RiskLevel.LOW;

  bundle.points.forEach((point, index) => {
    const level = riskAtOffset(point, offsetHours);
    const owned = miles[index] ?? 0;
    byLevel[levelName(level)] = (byLevel[levelName(level)] ?? 0) + owned;
    weighted += owned * level;
    if (level > worst) worst = level;
  });

  const total = miles.reduce((sum, m) => sum + m, 0) || 1;
  const get = (name: string) => byLevel[name] ?? 0;
  const noTravel = get('NO_TRAVEL');
  const severe = get('SEVERE');
  const high = get('HIGH');

  return {
    total_miles: route.summary?.total_miles ?? 0,
    miles_by_level: byLevel,
    no_travel_miles: noTravel,
    severe_miles: severe,
    high_miles: high,
    moderate_miles: get('MODERATE'),
    low_miles: get('LOW'),
    avg_risk: weighted / total,
    worst_level: levelName(worst),
    adverse_miles: noTravel + severe + high,
  };
}

const LEVEL_NAMES: Record<number, string> = {
  [RiskLevel.LOW]: 'LOW',
  [RiskLevel.MODERATE]: 'MODERATE',
  [RiskLevel.HIGH]: 'HIGH',
  [RiskLevel.SEVERE]: 'SEVERE',
  [RiskLevel.NO_TRAVEL]: 'NO_TRAVEL',
};

export function levelName(level: RiskLevel): string {
  return LEVEL_NAMES[level] ?? 'LOW';
}

/** All checkpoints for a route id. */
export function checkpointsFor(analysis: Analysis, routeId: string): Checkpoint[] {
  return analysis.checkpoints.find((c) => c.route_id === routeId)?.points ?? [];
}

export interface RouteView {
  route: RouteOption;
  bundle: CheckpointBundle | undefined;
  summary: RouteSummary;
  /** True when the offset changes this route's summary at all. */
  differsFromBase: boolean;
}

/**
 * Every route rendered at a given offset.
 *
 * `differsFromBase` drives the "N miles worse at this offset" hints, so the UI
 * can point at the routes the slider is penalising rather than leaving the
 * change unexplained.
 */
export function routeViews(analysis: Analysis, offsetHours: number): RouteView[] {
  return analysis.routes.map((route) => {
    const bundle = analysis.checkpoints.find((c) => c.route_id === route.id);
    const summary = summaryAtOffset(bundle, route, offsetHours);
    const differs =
      route.summary !== undefined &&
      (Math.abs(summary.severe_miles - route.summary.severe_miles) > 0.5 ||
        Math.abs(summary.high_miles - route.summary.high_miles) > 0.5 ||
        Math.abs(summary.no_travel_miles - route.summary.no_travel_miles) > 0.5);
    return { route, bundle, summary, differsFromBase: differs };
  });
}

/** Human-readable departure time for a given offset. */
export function shiftedDeparture(analysis: Analysis, offsetHours: number): Date {
  return new Date(
    new Date(analysis.meta.departure).getTime() + offsetHours * 3_600_000,
  );
}

/** `+6 h` style label for the slider. */
export function formatOffset(offsetHours: number): string {
  if (offsetHours === 0) return 'now';
  return offsetHours < 60 ? `+${offsetHours} h` : `+${Math.floor(offsetHours / 60)}h ${offsetHours % 60}m`;
}

/** Distance in miles, to one decimal. */
export function formatMiles(miles: number): string {
  return `${miles.toFixed(miles < 10 ? 1 : 0)} mi`;
}

/** Duration in seconds as `5h 12m`. */
export function formatDuration(seconds: number): string {
  const total = Math.round(seconds / 60);
  const hours = Math.floor(total / 60);
  const minutes = total % 60;
  return hours > 0 ? `${hours}h ${String(minutes).padStart(2, '0')}m` : `${minutes}m`;
}

/** Local clock time, e.g. `14:05`. */
export function formatClock(date: Date): string {
  return date.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
}

/** Day + clock, used in tooltips where the date matters. */
export function formatDateTime(date: Date): string {
  return date.toLocaleString([], {
    weekday: 'short',
    hour: '2-digit',
    minute: '2-digit',
  });
}

/** Avg risk to the same precision the server ranks on, so the two agree. */
export function formatAvgRisk(avgRisk: number, precision = 1): string {
  return avgRisk.toFixed(precision);
}
