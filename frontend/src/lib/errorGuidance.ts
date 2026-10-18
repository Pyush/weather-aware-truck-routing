/**
 * What to tell the user, per server error code (FE-09).
 *
 * A driver can act on every one of these, but only if the message says what to
 * actually do. Kept out of the component so a test can assert that every code
 * the API can emit has its own copy -- a missing key silently degrades to
 * "Something went wrong", which is the failure mode this table exists to avoid.
 */

/**
 * Also the source of truth for the assertion in `errorGuidance.test.ts`, which
 * enumerates `error_codes` below so a new backend code without copy fails CI.
 */
export const ERROR_GUIDANCE: Record<string, string> = {
  validation_failed: 'Check the highlighted fields and try again.',
  location_not_found: 'Try adding a state or country, for example "Chicago, IL".',
  no_route: 'No drivable route exists between those points.',
  upstream_unavailable: 'A weather or routing service is unavailable. Try again shortly.',
  upstream_timeout: 'A service took too long to answer. Try again.',
  service_unavailable: 'All providers are down. Try again shortly.',
  departure_out_of_range: 'Choose a departure within the next 16 days.',
  internal_error: 'Something failed on the server. This is not your fault — try again shortly.',
  network_error: 'The backend could not be reached. Is it running?',
};

/** Every code the API can emit, including the generic fallback. */
export const SERVER_ERROR_CODES = [
  'validation_failed',
  'location_not_found',
  'no_route',
  'upstream_unavailable',
  'upstream_timeout',
  'service_unavailable',
  'departure_out_of_range',
  'internal_error',
] as const;

/** The copy to show, falling back to the generic message for anything unknown. */
export function guidanceFor(code: string): string {
  return ERROR_GUIDANCE[code] ?? 'Something went wrong.';
}