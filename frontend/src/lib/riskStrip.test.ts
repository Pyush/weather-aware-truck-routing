import { describe, expect, it } from 'vitest';

import contract from '@/types/contract.json';
import { RiskLevel, type Analysis, type RouteOption } from '@/types/api';
import { lineProgress, riskStripStops } from '@/lib/mapData';

const example = contract.example.response as unknown as Analysis;

function fixture(index = 0) {
  const route = example.routes[index];
  const bundle = example.checkpoints.find((c) => c.route_id === route.id)!;
  return { route, bundle };
}

describe('lineProgress', () => {
  it('runs from 0 to 1 across the geometry', () => {
    const geometry: [number, number][] = [
      [-87.6, 41.8],
      [-85, 42],
      [-83, 42.3],
    ];
    const progress = lineProgress(geometry);
    expect(progress[0]).toBe(0);
    expect(progress.at(-1)).toBeCloseTo(1, 6);
    for (let i = 1; i < progress.length; i += 1) {
      expect(progress[i]).toBeGreaterThan(progress[i - 1]);
    }
  });

  it('places an evenly spaced vertex at its proportional position', () => {
    // Three equal 1-degree legs, so the middle vertex is the midpoint.
    const progress = lineProgress([
      [-87, 41],
      [-86, 41],
      [-85, 41],
      [-84, 41],
    ]);
    expect(progress[1]).toBeCloseTo(1 / 3, 6);
    expect(progress[2]).toBeCloseTo(2 / 3, 6);
  });

  it('is total for a degenerate geometry', () => {
    expect(lineProgress([])).toEqual([]);
    expect(lineProgress([[-87, 41]])).toEqual([0]);
  });
});

describe('riskStripStops', () => {
  it('produces strictly ascending stops within 0..1', () => {
    const { route, bundle } = fixture();
    const stops = riskStripStops(bundle, route.geometry, 0);
    expect(stops.length).toBeGreaterThan(1);
    for (let i = 1; i < stops.length; i += 1) {
      expect(stops[i].progress).toBeGreaterThan(stops[i - 1].progress);
    }
    expect(stops[0].progress).toBeGreaterThanOrEqual(0);
    expect(stops.at(-1)!.progress).toBeLessThanOrEqual(1);
  });

  it('colours each stop with the level at that offset', () => {
    const { route, bundle } = fixture();
    const stops = riskStripStops(bundle, route.geometry, 0);

    // Every level in the stops must be one the server actually reported for a
    // checkpoint at that offset -- never an invented value.
    const allowed = new Set(bundle.points.map((p) => p.hourly_risk[p.eta_hour_index]));
    for (const stop of stops) {
      expect(allowed.has(stop.level as RiskLevel)).toBe(true);
    }
  });

  it('changes with the departure offset', () => {
    const { route, bundle } = fixture();
    const at0 = riskStripStops(bundle, route.geometry, 0);
    const at24 = riskStripStops(bundle, route.geometry, 24);
    // The positions are geometry-driven and identical; only the levels move.
    expect(at24.map((s) => s.progress)).toEqual(at0.map((s) => s.progress));
    expect(
      at24.map((s) => s.level).join(),
      'the strip must repaint when the departure moves',
    ).not.toBe(at0.map((s) => s.level).join());
  });

  it('returns nothing rather than a malformed expression on missing input', () => {
    expect(riskStripStops(undefined, [], 0)).toEqual([]);
    const { bundle } = fixture();
    expect(riskStripStops(bundle, [], 0)).toEqual([]);
    expect(riskStripStops(bundle, [[-87, 41]], 0)).toEqual([]);
  });

  it('survives a route whose summary has not arrived yet', () => {
    const { route, bundle } = fixture();
    const partial: RouteOption = { ...route, summary: undefined };
    expect(() => riskStripStops(bundle, partial.geometry, 0)).not.toThrow();
    expect(riskStripStops(bundle, partial.geometry, 0).length).toBeGreaterThan(0);
  });
});