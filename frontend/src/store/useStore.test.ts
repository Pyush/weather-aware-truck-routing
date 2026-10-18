import { beforeEach, describe, expect, it } from 'vitest';

import contract from '@/types/contract.json';
import { routeViews } from '@/lib/selectors';
import { useStore } from '@/store/useStore';
import type { StreamEvent } from '@/lib/api';
import type { Analysis } from '@/types/api';

const example = contract.example.response as unknown as Analysis;

beforeEach(() => useStore.getState().reset());

describe('streamed route summaries', () => {
  it('shows a route before weather, then joins its summary by route id', () => {
    const route = example.routes[0];
    const { summary, ...geometry } = route;
    expect(summary).toBeDefined();
    const bundle = example.checkpoints.find((item) => item.route_id === route.id)!;
    const store = useStore.getState();

    store.beginStream();
    store.applyEvent({ type: 'route', ...geometry } as StreamEvent);
    expect(useStore.getState().analysis.routes[0].summary).toBeUndefined();
    expect(routeViews(useStore.getState().analysis, 0)[0].summary.avg_risk).toBe(0);

    store.applyEvent({ type: 'checkpoints', ...bundle });
    store.applyEvent({ type: 'summary', route_id: route.id, ...summary });

    const analysis = useStore.getState().analysis;
    expect(analysis.routes[0].summary).toEqual(summary);
    expect(analysis.summaries[0]).toMatchObject({ route_id: route.id, ...summary });
    expect(routeViews(analysis, 0)[0].summary.avg_risk).toBeCloseTo(summary!.avg_risk, 2);
    expect(routeViews(analysis, 0)[0].summary.severe_miles).toBeCloseTo(summary!.severe_miles, 1);
  });
});
