import { afterEach, describe, expect, it, vi } from 'vitest';

import { ApiError, fetchConfig, streamAnalysis } from '@/lib/api';

const request = {
  origin: 'Chicago, IL',
  destination: 'Detroit, MI',
  departure: '2026-10-17T14:00:00Z',
  load_lb: 40_000,
};

afterEach(() => vi.unstubAllGlobals());

function reply(lines: string[]): void {
  vi.stubGlobal('fetch', vi.fn().mockResolvedValue(
    new Response(lines.join(''), { status: 200, headers: { 'Content-Type': 'application/x-ndjson' } }),
  ));
}

describe('streamAnalysis', () => {
  it('uses an external API origin without doubling its trailing slash', async () => {
    reply(['{"type":"done","warnings":[]}\n']);
    await streamAnalysis(request, vi.fn(), undefined, 'https://example-api.onrender.com/');
    expect(fetch).toHaveBeenCalledWith(
      'https://example-api.onrender.com/api/v1/trips/analyze/stream/',
      expect.objectContaining({ method: 'POST' }),
    );
  });

  it('decodes lines split across chunks and completes on done', async () => {
    reply(['{"type":"route","id":"r0"}\n{"type":"do', 'ne","warnings":[]}\n']);
    const events: string[] = [];
    await streamAnalysis(request, (event) => events.push(event.type));
    expect(events).toEqual(['route', 'done']);
  });

  it('turns an in-stream error into an ApiError rather than reporting success', async () => {
    reply(['{"type":"error","status":502,"error":{"code":"upstream_unavailable","message":"Weather unavailable"}}\n']);
    await expect(streamAnalysis(request, vi.fn())).rejects.toMatchObject({
      name: 'ApiError', status: 502, code: 'upstream_unavailable',
    } satisfies Partial<ApiError>);
  });

  it('rejects a truncated stream with no terminal event', async () => {
    reply(['{"type":"route","id":"r0"}\n']);
    await expect(streamAnalysis(request, vi.fn())).rejects.toMatchObject({ code: 'incomplete_stream' });
  });
});

describe('fetchConfig', () => {
  it('uses the same external API origin as the stream', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response('{}', { status: 200 })));
    await fetchConfig('https://example-api.onrender.com/');
    expect(fetch).toHaveBeenCalledWith('https://example-api.onrender.com/api/v1/config/');
  });
});
