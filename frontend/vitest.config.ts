import { defineConfig } from 'vitest/config';
import react from '@vitejs/plugin-react';
import path from 'node:path';

export default defineConfig({
  plugins: [react()],
  resolve: {
    alias: { '@': path.resolve(import.meta.dirname, './src') },
  },
  test: {
    environment: 'jsdom',
    globals: true,
    setupFiles: ['./src/test/setup.ts'],
    include: ['src/**/*.test.{ts,tsx}'],
    coverage: {
      provider: 'v8',
      reporter: ['text', 'html'],
      // The map component is exercised in a browser, not in jsdom; a WebGL
      // context cannot exist here, so it is excluded rather than silently
      // reported as covered when it is not.
      exclude: ['src/components/MapView.tsx', 'src/main.tsx', '**/*.test.ts', '**/*.test.tsx'],
    },
  },
});
