import { afterAll, afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest';
import { act, cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { http, HttpResponse } from 'msw';
import { setupServer } from 'msw/node';

import App from '@/App';
import { useStore } from '@/store/useStore';
import contract from '@/types/contract.json';

// WebGL cannot run in jsdom. The real map is checked separately in a browser;
// this test verifies the UI state driven by the real stream reader and store.
vi.mock('@/components/MapView', () => ({ MapView: () => <div data-testid="map-placeholder" /> }));

const server = setupServer(
  http.get('/api/v1/config/', () => HttpResponse.json(contract.config)),
);

beforeAll(() => server.listen());
afterEach(() => {
  cleanup();
  server.resetHandlers();
  useStore.getState().reset();
  useStore.setState({ config: null });
});
afterAll(() => server.close());
beforeEach(() => {
  useStore.getState().reset();
  useStore.getState().setField('origin', 'Chicago, IL');
  useStore.getState().setField('destination', 'Detroit, MI');
});

function line(event: object): string {
  return `${JSON.stringify(event)}\n`;
}

describe('trip stream UI', () => {
  it('shows routes before weather, then updates cards and the slider without another request', async () => {
    const example = contract.example.response;
    const route = example.routes[0];
    const { summary, ...geometry } = route;
    let requests = 0;
    let controller: ReadableStreamDefaultController<Uint8Array>;
    const ready = new Promise<void>((resolve) => {
      server.use(http.post('/api/v1/trips/analyze/stream/', () => {
        requests += 1;
        const body = new ReadableStream<Uint8Array>({
          start(stream) {
            controller = stream;
            resolve();
          },
        });
        return new HttpResponse(body, { headers: { 'Content-Type': 'application/x-ndjson' } });
      }));
    });
    const encoder = new TextEncoder();
    const push = (event: object) => controller.enqueue(encoder.encode(line(event)));

    render(<App />);
    fireEvent.click(screen.getByRole('button', { name: 'Find safest routes' }));
    await ready;

    act(() => {
      push({ type: 'meta', ...example.meta });
      push({ type: 'route', ...geometry });
    });
    await waitFor(() => expect(useStore.getState().analysis.routes).toHaveLength(1));
    expect(await screen.findByText('If you leave later')).toBeInTheDocument();
    expect(screen.queryByText('Recommended: Option A')).not.toBeInTheDocument();

    act(() => {
      push({ type: 'checkpoints', ...example.checkpoints[0] });
      push({ type: 'summary', route_id: route.id, ...summary });
      push({ type: 'recommendation', ...example.recommendation });
      push({ type: 'done', warnings: [], weather_locations: 1, elapsed_ms: 50 });
      controller.close();
    });
    await waitFor(() => expect(useStore.getState().analysis.routes[0].summary?.worst_level).toBe('SEVERE'));
    const forecast = screen.getByText('If you leave later').closest('section')!;
    expect(await within(forecast).findByText('SEVERE')).toBeInTheDocument();
    expect(screen.getByText(`Recommended: ${example.recommendation.label}`)).toBeInTheDocument();

    const slider = screen.getByRole('slider', { name: 'Departure offset in hours' });
    fireEvent.change(slider, { target: { value: '48' } });
    await waitFor(() => expect(slider).toHaveValue('48'));
    expect(within(forecast).getAllByText('LOW').length).toBeGreaterThan(0);
    expect(useStore.getState().offsetHours).toBe(48);
    expect(requests).toBe(1);
  });

  it('restores a shared link and keeps its offset through a re-plan', async () => {
    window.history.replaceState(
      null,
      '',
      '/?from=Chicago%2C+IL&to=Duluth%2C+MN&depart=2026-10-19T09%3A30&load=52000&every=50&offset=18',
    );
    const example = contract.example.response;
    const route = example.routes[0];
    const { summary, ...geometry } = route;
    server.use(http.post('/api/v1/trips/analyze/stream/', () =>
      HttpResponse.text(
        [
          line({ type: 'meta', ...example.meta }),
          line({ type: 'route', ...geometry }),
          line({ type: 'checkpoints', ...example.checkpoints[0] }),
          line({ type: 'summary', route_id: route.id, ...summary }),
          line({ type: 'recommendation', ...example.recommendation }),
          line({ type: 'done', warnings: [], weather_locations: 1, elapsed_ms: 0 }),
        ].join(''),
        { headers: { 'Content-Type': 'application/x-ndjson' } },
      ),
    ));

    render(<App />);
    expect(screen.getByLabelText('Destination')).toHaveValue('Duluth, MN');
    expect(screen.getByLabelText('Load (lb)')).toHaveValue(52_000);
    expect(screen.getByLabelText('Departure')).toHaveValue('2026-10-19T09:30');
    expect(useStore.getState().offsetHours).toBe(18);

    fireEvent.click(screen.getByRole('button', { name: 'Find safest routes' }));
    await screen.findByText(`Recommended: ${example.recommendation.label}`);
    // The departure offset is a view choice, so a re-plan must not discard it.
    expect(useStore.getState().offsetHours).toBe(18);
    expect(window.location.search).toContain('offset=18');
  });

  it('shows inline validation for an empty destination without sending a request', async () => {
    let submitted = false;
    server.use(http.post('/api/v1/trips/analyze/stream/', () => {
      submitted = true;
      return HttpResponse.json({});
    }));
    render(<App />);
    fireEvent.change(screen.getByLabelText('Destination'), { target: { value: '' } });
    fireEvent.click(screen.getByRole('button', { name: 'Find safest routes' }));
    expect(screen.getByText('Enter a destination.')).toBeInTheDocument();
    expect(submitted).toBe(false);
  });

  it('shows a server error event instead of claiming the analysis is complete', async () => {
    server.use(http.post('/api/v1/trips/analyze/stream/', () =>
      HttpResponse.text(line({
        type: 'error', status: 502,
        error: { code: 'upstream_unavailable', message: 'Weather service unavailable.' },
      }), { headers: { 'Content-Type': 'application/x-ndjson' } }),
    ));
    render(<App />);
    fireEvent.click(screen.getByRole('button', { name: 'Find safest routes' }));
    expect(await screen.findByRole('alert')).toHaveTextContent('Weather service unavailable.');
    expect(useStore.getState().phase).toBe('error');
  });
});
