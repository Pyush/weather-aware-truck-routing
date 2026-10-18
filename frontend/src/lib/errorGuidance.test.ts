import { describe, expect, it } from 'vitest';

import contract from '@/types/contract.json';
import { ERROR_GUIDANCE, SERVER_ERROR_CODES, guidanceFor } from '@/lib/errorGuidance';

/** Generated from the backend taxonomy by `make gen-types`. */
const serverCodes = Object.keys(contract.error_codes);

describe('error guidance', () => {
  it('has distinct, actionable copy for every code the server can emit', () => {
    const messages = SERVER_ERROR_CODES.map(guidanceFor);
    expect(new Set(messages).size).toBe(SERVER_ERROR_CODES.length);
    for (const code of SERVER_ERROR_CODES) {
      // "Something went wrong." is the fallback; reaching it is the bug.
      expect(guidanceFor(code)).not.toBe('Something went wrong.');
      expect(guidanceFor(code).length).toBeGreaterThan(20);
    }
  });

  it('covers every error code published in the backend contract', () => {
    // From the generated contract, so adding a backend code without UI copy
    // fails here rather than degrading silently in the browser.
    expect(serverCodes.length).toBeGreaterThan(5);
    for (const code of serverCodes) {
      expect(ERROR_GUIDANCE[code], `no guidance copy for ${code}`).toBeDefined();
    }
    for (const code of SERVER_ERROR_CODES) {
      expect(serverCodes, `${code} is handled in the UI but not published by the server`).toContain(code);
    }
  });

  it('falls back to a generic message for an unrecognised code', () => {
    expect(guidanceFor('something_new')).toBe('Something went wrong.');
  });
});