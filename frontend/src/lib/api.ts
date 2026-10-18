/**
 * NDJSON streaming client.
 *
 * Reads `POST /api/v1/trips/analyze/stream/` as a `ReadableStream` and hands
 * each decoded event to a callback, so the map can draw routes while the 19 s
 * weather tail is still in flight. Without this the UI would stare at a blank
 * screen for the whole request (D8/D20).
 *
 * Aborts cleanly on unmount or re-submit, which matters because a re-submit
 * mid-flight would otherwise leave the previous response writing events into a
 * store the user has already navigated away from.
 */

import type { Analysis, ApiErrorBody, CheckpointBundle, RouteOption, RouteSummary } from '@/types/api';

/** One decoded line of the NDJSON stream. */
export type StreamEvent =
  | { type: 'meta'; [key: string]: unknown }
  | ({ type: 'route' } & RouteOption)
  | ({ type: 'weather_plan' } & WeatherPlanEvent)
  | { type: 'weather'; locations: number; hours: number }
  | ({ type: 'checkpoints' } & CheckpointBundle)
  | ({ type: 'summary'; route_id: string } & RouteSummary)
  | { type: 'recommendation'; route_id: string | null; label: string | null; ranking: string[]; rationale: string[]; criterion_tally: Record<string, Record<string, number>>; all_blocked: boolean }
  | { type: 'done'; warnings: string[]; weather_locations: number; elapsed_ms: number; cached?: boolean }
  | ({ type: 'error'; status: number } & ApiErrorBody)
  | { type: string; [key: string]: unknown };

export interface WeatherPlanEvent {
  hour_window_start: string;
  hour_window_end: string;
  hour_window_hours: number;
  checkpoint_count: number;
  weather_locations: number;
  deduped: number;
  coarsened: boolean;
}

export interface TripRequestBody {
  origin: string;
  destination: string;
  departure: string;
  load_lb: number;
  spacing_mi?: number;
  slider_hours?: number;
  truck_speed_factor?: number;
  truck_min_avg_mph?: number;
  truck_max_avg_mph?: number;
}

/** Failure carrying the server's structured error, so the UI can switch on `code`. */
export class ApiError extends Error {
  readonly status: number;
  readonly code: string;
  readonly details: Record<string, unknown>;

  constructor(status: number, body: ApiErrorBody) {
    super(body.error?.message ?? 'Request failed');
    this.name = 'ApiError';
    this.status = status;
    this.code = body.error?.code ?? 'internal_error';
    this.details = body.error?.details ?? {};
  }

  /** Field-level validation messages, when the server sent them. */
  get fieldErrors(): Record<string, string[]> {
    const fields = this.details.fields;
    return typeof fields === 'object' && fields !== null ? (fields as Record<string, string[]>) : {};
  }
}

/** An empty analysis, used before the first request completes. */
export function emptyAnalysis(): Analysis {
  return {
    meta: {
      origin: { lat: 0, lon: 0, display_name: '', country_code: null, admin1: null, source: '' },
      destination: { lat: 0, lon: 0, display_name: '', country_code: null, admin1: null, source: '' },
      departure: '',
      load_lb: 0,
      spacing_mi: 25,
      slider_hours: 48,
      hour_window_start: '',
      hour_window_end: '',
      hour_window_hours: 0,
      time_snap: 'floor',
      weather_locations: 0,
      elapsed_ms: 0,
      cached: false,
      truck: { speed_factor: 0.85, min_avg_mph: 25, max_avg_mph: 65 },
    },
    routes: [],
    summaries: [],
    checkpoints: [],
    recommendation: {
      route_id: null,
      label: null,
      ranking: [],
      rationale: [],
      criterion_tally: {},
      all_blocked: false,
      safety_gate_enabled: true,
    },
    warnings: [],
    progress: { routesReady: false, checkpointsLoaded: 0, checkpointTotal: 0 },
  };
}

/**
 * POST an analysis and stream the events back.
 *
 * @param body      Request payload.
 * @param onEvent   Called per decoded event, in arrival order.
 * @param signal    Aborts the request when the caller navigates or re-submits.
 * @param baseUrl   API origin; defaults to same-origin (the Vite dev proxy).
 */
export async function streamAnalysis(
  body: TripRequestBody,
  onEvent: (event: StreamEvent) => void,
  signal?: AbortSignal,
  baseUrl = '',
): Promise<void> {
  const response = await fetch(`${baseUrl}/api/v1/trips/analyze/stream/`, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      Accept: 'application/x-ndjson',
    },
    body: JSON.stringify(body),
    signal,
  });

  if (!response.ok) {
    // Validation and other pre-stream failures come back as ordinary JSON.
    let parsed: ApiErrorBody;
    try {
      parsed = (await response.json()) as ApiErrorBody;
    } catch {
      parsed = {
        error: { code: 'internal_error', message: `Request failed (HTTP ${response.status})` },
      };
    }
    throw new ApiError(response.status, parsed);
  }

  if (!response.body) {
    throw new ApiError(500, {
      error: { code: 'no_stream', message: 'The server did not return a stream.' },
    });
  }

  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = '';
  let completed = false;

  const acceptLine = (line: string): void => {
    const event = JSON.parse(line) as StreamEvent;
    // Once a streaming response has started its HTTP status is fixed at 200;
    // the server sends a terminal error event instead of changing the status.
    if (event.type === 'error' && 'error' in event) {
      throw new ApiError(Number(event.status) || 500, event as ApiErrorBody);
    }
    if (event.type === 'done') completed = true;
    onEvent(event);
  };

  try {
    for (;;) {
      const { done, value } = await reader.read();
      if (done) break;
      // NDJSON chunks split mid-line, so keep a buffer and split on newlines.
      buffer += decoder.decode(value, { stream: true });

      let newline = buffer.indexOf('\n');
      while (newline !== -1) {
        const line = buffer.slice(0, newline).trim();
        buffer = buffer.slice(newline + 1);
        if (line) acceptLine(line);
        newline = buffer.indexOf('\n');
      }
    }
    // A final line without a trailing newline is still a valid event.
    const tail = buffer.trim();
    if (tail) acceptLine(tail);
    if (!completed && !signal?.aborted) {
      throw new ApiError(502, {
        error: { code: 'incomplete_stream', message: 'The analysis ended before it was complete.' },
      });
    }
  } catch (error) {
    if (error instanceof DOMException && error.name === 'AbortError') return;
    throw error;
  } finally {
    reader.releaseLock();
  }
}

/** Fetch the rule configuration. The UI holds no thresholds of its own (D12). */
export async function fetchConfig(baseUrl = ''): Promise<import('@/types/api').AppConfig> {
  const response = await fetch(`${baseUrl}/api/v1/config/`);
  if (!response.ok) {
    throw new ApiError(response.status, {
      error: { code: 'config_unavailable', message: 'Could not load the rule configuration.' },
    });
  }
  return (await response.json()) as import('@/types/api').AppConfig;
}
