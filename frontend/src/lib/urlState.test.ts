import { describe, expect, it } from 'vitest';

import { buildUrlSearch, parseUrlState } from '@/lib/urlState';

const query = {
  origin: 'Chicago, IL',
  destination: 'Detroit, MI',
  departure: '2026-10-17T14:00:00Z',
  loadLb: 40_000,
  spacingMi: 25,
  offsetHours: 12,
};

describe('shareable URL state', () => {
  it('round-trips every field it carries', () => {
    expect(parseUrlState(buildUrlSearch(query))).toEqual(query);
  });

  it('encodes spaces and commas without corrupting them', () => {
    const search = buildUrlSearch(query);
    expect(search).toContain('from=Chicago%2C+IL');
    expect(parseUrlState(search).origin).toBe('Chicago, IL');
  });

  it('omits the offset when it is zero but keeps it when set', () => {
    expect(buildUrlSearch({ ...query, offsetHours: 0 })).not.toContain('offset=');
    expect(buildUrlSearch({ ...query, offsetHours: 1 })).toContain('offset=1');
  });

  it('omits absent fields rather than writing empty parameters', () => {
    const search = buildUrlSearch({ origin: 'Chicago, IL' });
    expect(search).toBe('from=Chicago%2C+IL');
  });

  it('ignores unparseable numbers instead of producing NaN', () => {
    const state = parseUrlState('load=heavy&every=&offset=12');
    expect(state.loadLb).toBeUndefined();
    expect(state.spacingMi).toBeUndefined();
    expect(state.offsetHours).toBe(12);
  });

  it('returns an empty object for a bare URL', () => {
    expect(parseUrlState('')).toEqual({});
  });
});