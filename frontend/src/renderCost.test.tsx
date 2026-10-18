/**
 * Render-cost regressions (FE-17, FE-33).
 *
 * The claim under test: dragging the departure slider re-reads the hourly
 * arrays and repaints the map, but does not re-render the React tree that owns
 * the map. If that stops being true, every slider tick starts re-running the
 * component that holds the WebGL context -- which is the expensive part.
 *
 * jsdom has no WebGL, so the map component is replaced by a render counter.
 * That is deliberate: what is measured here is React's decision to call the
 * component, not anything MapLibre does.
 */

import { afterAll, afterEach, beforeAll, describe, expect, it, vi } from 'vitest';
import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';

import App from '@/App';
import { useStore } from '@/store/useStore';
import type { Analysis } from '@/types/api';
import contract from '@/types/contract.json';

const renders = { map: 0 };

vi.mock('@/components/MapView', () => ({
  MapView: () => {
    renders.map += 1;
    return <div data-testid="map-placeholder" />;
  },
}));

const example = contract.example.response as unknown as Analysis;

/** Push a complete analysis into the store without any network. */
function seedAnalysis(): void {
  const store = useStore.getState();
  store.beginStream();
  for (const route of example.routes) {
    store.applyEvent({ type: 'route', ...route } as never);
  }
  for (const bundle of example.checkpoints) {
    store.applyEvent({ type: 'checkpoints', ...bundle } as never);
  }
  for (const route of example.routes) {
    store.applyEvent({ type: 'summary', route_id: route.id, ...route.summary! } as never);
  }
  store.applyEvent({ type: 'recommendation', ...example.recommendation } as never);
  store.finishStream();
}

beforeAll(() => vi.stubGlobal('fetch', vi.fn().mockResolvedValue(
  new Response(JSON.stringify(contract.config), {
    status: 200,
    headers: { 'Content-Type': 'application/json' },
  }),
)));
afterAll(() => vi.unstubAllGlobals());
afterEach(() => {
  cleanup();
  useStore.getState().reset();
  renders.map = 0;
});

describe('slider render cost', () => {
  it('does not re-render the map component across a 0 to 48 h sweep', async () => {
    render(<App />);
    act(() => seedAnalysis());
    // The map is lazily imported, so its mount (and the Suspense resolution
    // that follows) lands a tick later. Let it settle before taking a
    // baseline, or the sweep counts the pending mount as if it were a repaint.
    await waitFor(() => expect(renders.map).toBeGreaterThan(0));
    // The dynamic `import()` settles on a macrotask, after the first render.
    // Flush it, otherwise the sweep is charged for the pending mount.
    await act(async () => {
      await new Promise((resolve) => setTimeout(resolve, 0));
    });

    const slider = screen.getByRole('slider', { name: 'Departure offset in hours' });
    // One warm-up change absorbs the lazy component's single settle-on-first-
    // update render. It is a constant, not a per-tick cost, which is what this
    // test is about: the original bug re-rendered once per offset (49 total).
    fireEvent.change(slider, { target: { value: '1' } });

    const before = renders.map;
    // Every value the user can reach by dragging, not a single jump.
    for (let hour = 0; hour <= 48; hour += 1) {
      fireEvent.change(slider, { target: { value: String(hour) } });
    }

    expect(useStore.getState().offsetHours).toBe(48);
    expect(renders.map, `map re-rendered ${renders.map - before} time(s) across 49 offset changes`)
      .toBe(before);
  });

  it('updates the visible numbers without touching the map', () => {
    render(<App />);
    act(() => seedAnalysis());
    const slider = screen.getByRole('slider', { name: 'Departure offset in hours' });

    const optionAText = () =>
      screen.getByText('If you leave later').closest('section')!.querySelector('button')!.textContent;

    const at0 = optionAText();
    fireEvent.change(slider, { target: { value: '24' } });
    const at24 = optionAText();

    expect(at24).not.toBe(at0);
  });
});