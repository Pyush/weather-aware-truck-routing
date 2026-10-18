import { afterEach, describe, expect, it, vi } from 'vitest';
import { act, cleanup, render } from '@testing-library/react';

import { MapView } from '@/components/MapView';
import { useStore } from '@/store/useStore';
import contract from '@/types/contract.json';
import type { Analysis } from '@/types/api';

const fake = vi.hoisted(() => {
  const listeners = new Map<string, Set<(event?: unknown) => void>>();
  const layers = new Set<string>();
  const sources = new Map<string, { setData: ReturnType<typeof vi.fn> }>();
  const setLayoutProperty = vi.fn();
  const setPaintProperty = vi.fn();
  const fitBounds = vi.fn();
  const queryRenderedFeatures = vi.fn<(...args: unknown[]) => Array<{ properties: { id: string } }>>(() => []);
  const map = {
    on: vi.fn((name: string, listener: (event?: unknown) => void) => {
      if (!listeners.has(name)) listeners.set(name, new Set());
      listeners.get(name)!.add(listener);
    }),
    off: vi.fn((name: string, listener: (event?: unknown) => void) => listeners.get(name)?.delete(listener)),
    addSource: vi.fn((name: string) => sources.set(name, { setData: vi.fn() })),
    getSource: vi.fn((name: string) => sources.get(name)),
    addLayer: vi.fn((layer: { id: string }) => layers.add(layer.id)),
    getLayer: vi.fn((name: string) => layers.has(name)),
    setLayoutProperty,
    setPaintProperty,
    fitBounds,
    queryRenderedFeatures,
    getCanvas: () => ({ style: { cursor: '' } }),
    addControl: vi.fn(),
    remove: vi.fn(),
  };
  return { listeners, layers, sources, map, setLayoutProperty, setPaintProperty, fitBounds, queryRenderedFeatures };
});

vi.mock('maplibre-gl', () => ({
  Map: vi.fn(function MockMap() { return fake.map; }),
  NavigationControl: vi.fn(),
  ScaleControl: vi.fn(),
}));

const analysis = contract.example.response as unknown as Analysis;

afterEach(() => {
  cleanup();
  fake.listeners.clear();
  fake.layers.clear();
  fake.sources.clear();
  vi.clearAllMocks();
  useStore.getState().reset();
});

describe('map style load', () => {
  it('attaches interactions and applies toggles that were set before load', () => {
    const onSelectRoute = vi.fn();
    fake.queryRenderedFeatures.mockReturnValue([{ properties: { id: analysis.routes[0].id } }]);
    render(
      <MapView
        analysis={analysis}
        config={contract.config as never}
        selectedRouteId={analysis.routes[0].id}
        showHeatmap={false}
        showCheckpoints={false}
        onSelectRoute={onSelectRoute}
        onHoverCheckpoint={vi.fn()}
      />,
    );

    expect(fake.listeners.get('click')?.size ?? 0).toBe(0);
    act(() => { fake.listeners.get('load')?.forEach((listener) => listener()); });

    expect(fake.setLayoutProperty).toHaveBeenCalledWith('corridor-heat', 'visibility', 'none');
    expect(fake.setLayoutProperty).toHaveBeenCalledWith('checkpoints', 'visibility', 'none');
    expect(fake.listeners.get('click')?.size).toBe(1);
    act(() => { fake.listeners.get('click')?.forEach((listener) => listener({ point: { x: 0, y: 0 } })); });
    expect(onSelectRoute).toHaveBeenCalledWith(analysis.routes[0].id);
    expect(fake.fitBounds).toHaveBeenCalled();
  });
});
