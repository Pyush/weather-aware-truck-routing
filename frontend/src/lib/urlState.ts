/**
 * Shareable URL state (FE-24).
 *
 * Pure string in, pure object out, so the round-trip can be tested without a
 * browser. `App` does the actual `history.replaceState` and restore.
 *
 * The URL is the only part of the app state that survives a reload, so it holds
 * the *query* (which produces an analysis) and the slider offset (which is a
 * pure view choice). It deliberately does not hold the selected route: that
 * follows from the server's ranking, which a cached response may change.
 */

import type { TripRequestBody } from '@/lib/api';

/** Query fields the URL carries. Anything else is a server-side concern. */
export interface UrlState {
  origin?: string;
  destination?: string;
  departure?: string;
  loadLb?: number;
  spacingMi?: number;
  offsetHours?: number;
}

/** Round-trips through `URLSearchParams`, so order and encoding are handled. */
export function parseUrlState(search: string): UrlState {
  const params = new URLSearchParams(search);
  const state: UrlState = {};
  const text = (key: string): string | undefined => params.get(key) ?? undefined;
  const number = (key: string): number | undefined => {
    const raw = text(key);
    // An empty value must not become 0: `Number('')` is 0, which would turn a
    // truncated `?every=` link into "sample every 0 miles".
    if (raw === undefined || raw.trim() === '') return undefined;
    const parsed = Number(raw);
    return Number.isFinite(parsed) ? parsed : undefined;
  };

  state.origin = text('from');
  state.destination = text('to');
  state.departure = text('depart');
  state.loadLb = number('load');
  state.spacingMi = number('every');
  state.offsetHours = number('offset');
  return state;
}

/**
 * Build the query string, omitting defaults so a shared link stays readable.
 *
 * `departure` is always included when present: it is the one field that must
 * travel for the server's 16-day forecast window to match the sender's view.
 */
export function buildUrlSearch(state: UrlState): string {
  const params = new URLSearchParams();
  const add = (key: string, value: string | number | undefined, fallback?: string | number) => {
    if (value === undefined || value === '') return;
    if (fallback !== undefined && String(value) === String(fallback)) return;
    params.set(key, String(value));
  };

  add('from', state.origin);
  add('to', state.destination);
  add('depart', state.departure);
  add('load', state.loadLb);
  add('every', state.spacingMi);
  add('offset', state.offsetHours, 0);
  return params.toString();
}

/** The store fields that a shared link can restore, as a request body. */
export function stateFromBody(body: TripRequestBody): UrlState {
  return {
    origin: body.origin,
    destination: body.destination,
    departure: body.departure,
    loadLb: body.load_lb,
    spacingMi: body.spacing_mi,
  };
}