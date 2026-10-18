/**
 * Selector tests.
 *
 * The important one is `the slider reads hourly_risk directly`, which pins the
 * invariant the whole frontend rests on: the level at departure offset `h` is
 * `hourly_risk[eta_hour_index + h]`. It is checked against the *real* recorded
 * payload from the backend, so a change to indexing cannot drift into the UI
 * unnoticed (D7/D12).
 */

import { describe, expect, it } from 'vitest';

import contract from '@/types/contract.json';
import { RiskLevel, type Analysis, type Checkpoint, type RouteOption, type RouteSummary } from '@/types/api';
import {
  formatAvgRisk,
  formatDuration,
  formatOffset,
  etaAtOffset,
  riskAtOffset,
  routeViews,
  segmentMiles,
  shiftedDeparture,
  summaryAtOffset,
} from '@/lib/selectors';
import { buildCheckpointsCollection, buildCorridorCollection } from '@/lib/mapData';

const example = contract.example.response as unknown as Analysis;

function checkpointAt(routeIndex: number, index: number): Checkpoint {
  const bundle = example.checkpoints[routeIndex];
  return bundle.points[index];
}

describe('riskAtOffset', () => {
  it('reads hourly_risk at eta_hour_index for offset 0', () => {
    for (let r = 0; r < example.checkpoints.length; r += 1) {
      for (const point of example.checkpoints[r].points) {
        expect(riskAtOffset(point, 0)).toBe(point.hourly_risk[point.eta_hour_index]);
        expect(riskAtOffset(point, 0)).toBe(point.risk.level_value);
      }
    }
  });

  it('is exactly an array lookup at any offset', () => {
    const point = checkpointAt(0, 5);
    for (let offset = 0; offset < 20; offset += 1) {
      expect(riskAtOffset(point, offset)).toBe(point.hourly_risk[point.eta_hour_index + offset]);
    }
  });

  it('clamps rather than wrapping', () => {
    const point = checkpointAt(0, 0);
    const last = point.hourly_risk.length - 1;
    expect(riskAtOffset(point, 10_000)).toBe(point.hourly_risk[last]);
    expect(riskAtOffset(point, -10)).toBe(point.hourly_risk[0]);
  });

  it('handles an empty hourly_risk without throwing', () => {
    const point = { ...checkpointAt(0, 0), hourly_risk: [] };
    expect(riskAtOffset(point, 6)).toBe(point.risk.level_value);
  });
});

describe('segmentMiles', () => {
  it('matches the backend midpoint attribution', () => {
    const bundle = example.checkpoints[0];
    const route = example.routes[0];
    const miles = segmentMiles(bundle.points, route.summary!.total_miles);

    // Endpoints take half an interval; the interior takes a whole one.
    expect(miles[0]).toBeCloseTo((bundle.points[1].distance_mi - bundle.points[0].distance_mi) / 2, 5);
    expect(miles[miles.length - 1]).toBeCloseTo(
      route.summary!.total_miles - (bundle.points[miles.length - 1].distance_mi + bundle.points[miles.length - 2].distance_mi) / 2,
      5,
    );
  });

  it('partitions the route exactly', () => {
    const bundle = example.checkpoints[0];
    const route = example.routes[0];
    const miles = segmentMiles(bundle.points, route.summary!.total_miles);
    expect(miles.reduce((a, b) => a + b, 0)).toBeCloseTo(route.summary!.total_miles, 4);
  });

  it('returns nothing for an empty route', () => {
    expect(segmentMiles([], 100)).toEqual([]);
  });
});

describe('summaryAtOffset', () => {
  it('agrees with the server at offset 0', () => {
    for (const route of example.routes) {
      const bundle = example.checkpoints.find((c) => c.route_id === route.id);
      const summary = summaryAtOffset(bundle, route, 0);
      expect(summary.severe_miles).toBeCloseTo(route.summary!.severe_miles, 1);
      expect(summary.high_miles).toBeCloseTo(route.summary!.high_miles, 1);
      expect(summary.no_travel_miles).toBeCloseTo(route.summary!.no_travel_miles, 1);
      expect(summary.avg_risk).toBeCloseTo(route.summary!.avg_risk, 2);
    }
  });

  it('returns a coherent empty summary for missing input', () => {
    const summary = summaryAtOffset(undefined, undefined, 6);
    expect(summary.total_miles).toBe(0);
    expect(summary.avg_risk).toBe(0);
  });

  it('survives a route that has arrived before its summary', () => {
    // Regression: the stream emits `route` events before `summary` events, so
    // the slider renders while `route.summary` is still undefined. Reading it
    // unguarded threw "Cannot read properties of undefined (reading
    // 'total_miles')" and blanked the whole app mid-stream.
    const route = { ...example.routes[0], summary: undefined } as unknown as RouteOption;
    const bundle = example.checkpoints.find((c) => c.route_id === route.id);

    expect(() => summaryAtOffset(bundle, route, 0)).not.toThrow();
    const summary = summaryAtOffset(bundle, route, 0);
    expect(summary.total_miles).toBe(0);
    expect(summary.avg_risk).toBe(0);

    expect(() => routeViews({ ...example, routes: [route] }, 0)).not.toThrow();
  });

  it('always accounts for every mile of the route', () => {
    const route = example.routes[0];
    const bundle = example.checkpoints.find((c) => c.route_id === route.id)!;
    for (const offset of [0, 6, 12, 24, 36, 48]) {
      const summary = summaryAtOffset(bundle, route, offset);
      const accounted =
        summary.low_miles + summary.moderate_miles + summary.high_miles +
        summary.severe_miles + summary.no_travel_miles;
      expect(accounted).toBeCloseTo(summary.total_miles, 4);
    }
  });

  it('derives each offset only from values present in the hourly arrays', () => {
    const route = example.routes[0];
    const bundle = example.checkpoints.find((c) => c.route_id === route.id)!;
    const allowed = new Set(bundle.points.flatMap((p) => p.hourly_risk));
    expect(allowed.size).toBeGreaterThan(1); // the fixture must actually vary

    for (const offset of [0, 12, 24]) {
      const summary = summaryAtOffset(bundle, route, offset);
      const observed = new Set(
        bundle.points.map((p) => {
          const i = Math.min(Math.max(p.eta_hour_index + offset, 0), p.hourly_risk.length - 1);
          return p.hourly_risk[i];
        }),
      );
      for (const level of observed) {
        expect(allowed.has(level as RiskLevel)).toBe(true);
      }
      expect(summary.avg_risk).toBeGreaterThanOrEqual(1);
      expect(summary.avg_risk).toBeLessThanOrEqual(5);
    }
  });
});

describe('backend parity at departure offsets', () => {
  const reference = contract.example.offset_summaries as Record<string, Record<string, RouteSummary>>;

  it.each([0, 6, 24, 48])('matches backend mileage and risk at +%ih', (offset) => {
    for (const route of example.routes) {
      const bundle = example.checkpoints.find((c) => c.route_id === route.id);
      const actual = summaryAtOffset(bundle, route, offset);
      const expected = reference[String(offset)][route.id];
      expect(expected).toBeDefined();
      expect(actual.total_miles).toBeCloseTo(expected.total_miles, 1);
      for (const field of ['no_travel_miles', 'severe_miles', 'high_miles', 'moderate_miles', 'low_miles'] as const) {
        expect(actual[field]).toBeCloseTo(expected[field], 1);
      }
      expect(actual.avg_risk).toBeCloseTo(expected.avg_risk, 2);
      expect(actual.worst_level).toBe(expected.worst_level);
    }
  });

  it('shifts every checkpoint ETA uniformly without altering route duration', () => {
    for (const route of example.routes) {
      const bundle = example.checkpoints.find((c) => c.route_id === route.id)!;
      for (const offset of [0, 6, 24, 48]) {
        const departureDelta = shiftedDeparture(example, offset).getTime() - shiftedDeparture(example, 0).getTime();
        expect(departureDelta).toBe(offset * 3_600_000);
        for (const point of bundle.points) {
          expect(etaAtOffset(point, offset).getTime() - etaAtOffset(point, 0).getTime()).toBe(departureDelta);
        }
        expect(route.duration_s).toBe(example.routes[route.index].duration_s);
      }
    }
  });
});

describe('map data', () => {
  it('recolours checkpoints from the offset', () => {
    const route = example.routes[0];
    const bundle = example.checkpoints.find((c) => c.route_id === route.id);
    const at0 = buildCheckpointsCollection(bundle, route, 0);
    const at18 = buildCheckpointsCollection(bundle, route, 18);

    expect(at0.features).toHaveLength(bundle!.points.length);
    expect(at18.features).toHaveLength(bundle!.points.length);

    const levelsAt = (collection: typeof at0) =>
      collection.features.map((f) => f.properties?.level as RiskLevel);
    expect(levelsAt(at0)).toEqual(bundle!.points.map((p) => p.hourly_risk[p.eta_hour_index]));
    expect(levelsAt(at18)).toEqual(
      bundle!.points.map((p) => {
        const i = Math.min(p.eta_hour_index + 18, p.hourly_risk.length - 1);
        return p.hourly_risk[i];
      }),
    );
  });

  it('emits a polygon per checkpoint for the heatmap, weighted by severity', () => {
    const route = example.routes[0];
    const bundle = example.checkpoints.find((c) => c.route_id === route.id);
    const corridor = buildCorridorCollection(bundle, route, 0);

    expect(corridor.features).toHaveLength(bundle!.points.length);
    for (const feature of corridor.features) {
      expect(feature.geometry.type).toBe('Polygon');
      expect(feature.properties!.weight).toBeGreaterThan(0);
    }
  });

  it('returns empty collections rather than throwing on missing input', () => {
    expect(buildCheckpointsCollection(undefined, undefined, 0).features).toEqual([]);
    expect(buildCorridorCollection(undefined, undefined, 0).features).toEqual([]);
  });

  it('builds the corridor when the route has no summary yet', () => {
    // Regression: `route` events arrive before `summary` events, so the map can
    // be asked to draw a corridor while `route.summary` is still undefined.
    const route = { ...example.routes[0], summary: undefined } as unknown as RouteOption;
    const bundle = example.checkpoints.find((c) => c.route_id === route.id);

    expect(() => buildCorridorCollection(bundle, route, 0)).not.toThrow();
    const corridor = buildCorridorCollection(bundle, route, 0);
    expect(corridor.features).toHaveLength(bundle!.points.length);
  });
});

describe('formatting', () => {
  it('formats the offset as a duration the driver recognises', () => {
    expect(formatOffset(0)).toBe('now');
    expect(formatOffset(6)).toBe('+6 h');
    expect(formatOffset(1)).toBe('+1 h');
  });

  it('formats durations as h/m', () => {
    expect(formatDuration(3600)).toBe('1h 00m');
    expect(formatDuration(5400)).toBe('1h 30m');
    expect(formatDuration(600)).toBe('10m');
  });

  it('formats average risk at the ranking precision', () => {
    expect(formatAvgRisk(2.75, 1)).toBe('2.8');
  });
});
