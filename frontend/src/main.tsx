import { StrictMode } from 'react';
import { createRoot } from 'react-dom/client';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { setWorkerUrl } from 'maplibre-gl';

// MapLibre locates its worker relative to its own module (`new URL('./…',
// import.meta.url)`). That works when the package ships unbundled, but Vite
// rewrites the module into `/assets/maplibre-<hash>.js`, so the worker lookup
// 404s -- and the SPA fallback answers with HTML, which fails MIME checking.
// `?worker&url` bundles the worker (resolving its own imports) and returns a
// URL that exists in `dist`. Dev never showed this: Vite's pre-bundling is
// excluded for MapLibre, so the real dist path resolves there.
import maplibreWorkerUrl from 'maplibre-gl/dist/maplibre-gl-worker.mjs?worker&url';

setWorkerUrl(maplibreWorkerUrl);

import App from '@/App';
import '@/index.css';

const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      // The analysis is streamed, not fetched through the query cache, but the
      // config is a normal query and should not refetch on every mount.
      staleTime: 10 * 60 * 1000,
      retry: 1,
      refetchOnWindowFocus: false,
    },
  },
});

const container = document.getElementById('root');
if (!container) throw new Error('#root is missing from index.html');

createRoot(container).render(
  <StrictMode>
    <QueryClientProvider client={queryClient}>
      <App />
    </QueryClientProvider>
  </StrictMode>,
);
