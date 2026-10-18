import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';
import path from 'node:path';

export default defineConfig({
  plugins: [react()],
  resolve: {
    alias: { '@': path.resolve(import.meta.dirname, './src') },
  },
  optimizeDeps: {
    // MapLibre builds its worker from a blob URL resolved relative to its own
    // module path. Vite's dev pre-bundling rewrites that path to
    // /node_modules/.vite/deps/, where the worker file does not exist, so the
    // worker fails to load ("Worker failed to load"). Excluding the package
    // keeps the real dist path and the relative lookup resolves.
    exclude: ['maplibre-gl'],
  },
  server: {
    port: 5173,
    proxy: {
      // Proxy to Django so the browser sees one origin in development, which
      // keeps CORS out of the picture and matches the production nginx setup.
      '/api': {
        target: 'http://127.0.0.1:8000',
        changeOrigin: true,
      },
    },
  },
  build: {
    target: 'es2022',
    sourcemap: true,
    rollupOptions: {
      output: {
        // MapLibre is large and changes rarely; splitting it keeps the app chunk
        // small enough that a rules change does not invalidate it.
        // A function rather than a map, because Rollup's typed overloads only
        // accept `manualChunks` as a function.
        manualChunks(id: string) {
          if (id.includes('node_modules/maplibre-gl')) return 'maplibre';
          if (id.includes('node_modules/zustand')) return 'state';
          return undefined;
        },
      },
    },
  },
});
