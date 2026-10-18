/**
 * Test setup.
 *
 * jsdom has no WebGL, so MapLibre cannot initialise. Rather than mocking it
 * (which would test the mock), the map is simply excluded from unit coverage and
 * verified in a real browser instead -- see FE-32/FE-33.
 */

import '@testing-library/jest-dom/vitest';

// jsdom lacks these; MapLibre and ResizeObserver probe for them on mount.
if (!('ResizeObserver' in globalThis)) {
  class ResizeObserverStub {
    observe() {}
    unobserve() {}
    disconnect() {}
  }
  globalThis.ResizeObserver = ResizeObserverStub as unknown as typeof ResizeObserver;
}
