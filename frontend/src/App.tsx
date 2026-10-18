import { Suspense, lazy, useCallback, useEffect, useRef, useState } from 'react';

import { ApiError, fetchConfig, streamAnalysis } from '@/lib/api';
import { buildUrlSearch, parseUrlState } from '@/lib/urlState';
import { guidanceFor } from '@/lib/errorGuidance';
import { applyRiskColors } from '@/lib/theme';
import { requestBody, useStore } from '@/store/useStore';
import { TripForm } from '@/components/TripForm';
import { EmptyState } from '@/components/EmptyState';
import { RoutePanel } from '@/components/RoutePanel';
import { ForecastSlider } from '@/components/ForecastSlider';
import { MethodologyModal } from '@/components/MethodologyModal';
import { RiskLegend } from '@/components/RiskLegend';

/**
 * MapLibre is ~1 MB, and nothing about the form or the route comparison needs
 * it. Loaded on demand so the first paint ships the app, not the renderer; the
 * placeholder below occupies the same space, so there is no layout shift.
 */
const MapView = lazy(() =>
  import('@/components/MapView').then((module) => ({ default: module.MapView })),
);

export default function App() {
  const phase = useStore((s) => s.phase);
  const analysis = useStore((s) => s.analysis);
  const config = useStore((s) => s.config);
  const error = useStore((s) => s.error);
  // `offsetHours` is deliberately NOT subscribed here. Each component that needs
  // it subscribes itself, so a slider tick does not re-render the whole shell
  // (and with it the map, which owns a WebGL context). See MapView.
  const selectedRouteId = useStore((s) => s.selectedRouteId);
  const showHeatmap = useStore((s) => s.showHeatmap);
  const showCheckpoints = useStore((s) => s.showCheckpoints);

  const selectRoute = useStore((s) => s.selectRoute);
  const hoverCheckpoint = useStore((s) => s.hoverCheckpoint);

  const [showMethodology, setShowMethodology] = useState(false);
const [copied, setCopied] = useState(false);
  const abort = useRef<AbortController | null>(null);
  const restored = useRef(false);

  /**
   * Stable identity, so the modal's effect does not re-run on every render.
   *
   * An inline `() => setShowMethodology(false)` is a new function each time, which
   * re-fires the modal's mount effect and calls `showModal()` again -- so the
   * dialog would re-open the instant it was closed.
   */
  const closeMethodology = useCallback(() => setShowMethodology(false), []);

  /**
   * Copy the current view as a link.
   *
   * Built from store state rather than read from the address bar: URL syncing
   * only starts once the user arrived *via* a link, so on a fresh visit the
   * address bar is bare and copying it would produce a link that reproduces
   * nothing.
   */
  const copyLink = useCallback(() => {
    const store = useStore.getState();
    const search = buildUrlSearch({
      origin: store.origin,
      destination: store.destination,
      departure: store.departure,
      loadLb: store.loadLb,
      spacingMi: store.spacingMi,
      offsetHours: store.offsetHours,
    });
    const url = `${window.location.origin}${window.location.pathname}${search ? `?${search}` : ''}`;
    const write = navigator.clipboard?.writeText(url);
    void write?.then(
      () => setCopied(true),
      () => setCopied(false),
    );
  }, []);

  // Load the rule configuration once. The UI has no thresholds of its own, so a
  // failure here must be loud rather than silently falling back to guesses.
  useEffect(() => {
    let cancelled = false;
    fetchConfig()
      .then((cfg) => {
        if (cancelled) return;
        applyRiskColors(cfg);
        useStore.getState().setConfig(cfg);
      })
      .catch(() => {
        /* the legend and badges fall back to CSS defaults; analysis still works */
      });
    return () => {
      cancelled = true;
    };
  }, []);

  // Abort an in-flight request when the component unmounts.
  useEffect(() => () => abort.current?.abort(), []);

  // Restore a shared link (FE-24). Runs once, before any analysis: the query is
  // the input to that analysis, so restoring after a submit would race it.
  useEffect(() => {
    const state = parseUrlState(window.location.search);
    const store = useStore.getState();
    for (const [key, value] of Object.entries(state)) {
      if (value === undefined) continue;
      // `departure` arrives as a datetime-local string, not an ISO instant.
      if (key === 'offsetHours') store.setOffset(value);
      else if (key === 'departure') store.setField('departure', value.slice(0, 16));
      else store.setField(key as never, value as never);
    }
    if (state.origin || state.destination) restored.current = true;
  }, []);

  // Keep the URL in step with the query and the slider, so copying the address
  // bar reproduces the view. `replaceState` rather than pushing, so dragging the
  // slider does not fill the back button with 48 entries.
  //
  // Subscribed rather than dependency-driven: the query lives in the store, and
  // reading it through `getState` would not re-run this effect when it changes.
  useEffect(() => {
    if (!restored.current) return;
    const write = () => {
      const store = useStore.getState();
      const search = buildUrlSearch({
        origin: store.origin,
        destination: store.destination,
        departure: store.departure,
        loadLb: store.loadLb,
        spacingMi: store.spacingMi,
        offsetHours: store.offsetHours,
      });
      window.history.replaceState(null, '', `${window.location.pathname}${search ? `?${search}` : ''}`);
    };
    write();
    return useStore.subscribe(write);
  }, []);

  const submit = useCallback(async () => {
    const store = useStore.getState();
    abort.current?.abort();
    const controller = new AbortController();
    abort.current = controller;

    store.beginStream();
    // The offset survives a re-plan: it is a departure choice, not a property of
    // the previous analysis, and resetting it would silently discard the offset
    // a shared link was opened with. `setOffset` re-clamps to the server's horizon.
    useStore.getState().setOffset(useStore.getState().offsetHours);

    try {
      await streamAnalysis(requestBody(store), (event) => {
        useStore.getState().applyEvent(event);
      }, controller.signal);
      if (controller.signal.aborted) return;
      useStore.getState().finishStream();
    } catch (err) {
      if (controller.signal.aborted) return;
      if (err instanceof ApiError) {
        useStore.getState().failStream({
          code: err.code,
          message: err.message,
          fieldErrors: err.fieldErrors,
        });
      } else {
        useStore.getState().failStream({
          code: 'network_error',
          message: err instanceof Error ? err.message : 'The request failed.',
        });
      }
    }
  }, []);

  return (
    <div className="app">
      <header
        style={{
          display: 'flex',
          alignItems: 'center',
          gap: 'var(--space-4)',
          padding: 'var(--space-3) var(--space-4)',
          borderBottom: '1px solid var(--border)',
          background: 'var(--bg-raised)',
        }}
      >
        <div style={{ display: 'flex', flexDirection: 'column' }}>
          <h1 style={{ fontSize: 15 }}>Weather-Aware Truck Routing</h1>
          <span className="dim" style={{ fontSize: 11 }}>
            Pick the safest route for your load, hour by hour.
          </span>
        </div>

        <div style={{ marginLeft: 'auto', display: 'flex', alignItems: 'center', gap: 12 }}>
          {phase === 'streaming' && (
            <span className="dim" style={{ display: 'flex', alignItems: 'center', gap: 6, fontSize: 12 }}>
              <span className="streaming-dot" />
              {analysis.progress.routesReady
                ? `loading weather for ${analysis.progress.checkpointTotal} checkpoints…`
                : 'finding routes…'}
            </span>
          )}
          {phase === 'complete' && (
            <span className="dim" style={{ fontSize: 12 }}>
              {analysis.meta.cached ? 'cached · ' : ''}
              {(analysis.meta.elapsed_ms / 1000).toFixed(1)} s ·{' '}
              {analysis.meta.weather_locations} weather points
            </span>
          )}
          <RiskLegend config={config} />
          <button className="btn btn-ghost" type="button" onClick={copyLink}>
            {copied ? 'Link copied' : 'Copy link'}
          </button>
          <button className="btn btn-ghost" type="button" onClick={() => setShowMethodology(true)}>
            Methodology
          </button>
        </div>
      </header>

      <div className="app-body">
        <aside className="panel">
          <TripForm onSubmit={submit} />
          {error && <ErrorBanner error={error} />}
          {analysis.warnings.length > 0 && <WarningList warnings={analysis.warnings} />}
          {phase === 'idle' && <EmptyState onStart={submit} />}
          <ForecastSlider config={config} />
          <RoutePanel config={config} />
        </aside>

        <div className="map-wrap">
          {/* Mounted only once there is something to draw. An empty map is a
              1 MB download the user gets no value from; they get it as soon as
              they ask for routes, by which point they are waiting anyway. */}
          {analysis.routes.length > 0 ? (
            <Suspense fallback={<MapPlaceholder />}>
              <MapView
                analysis={analysis}
                config={config}
                selectedRouteId={selectedRouteId}
                showHeatmap={showHeatmap}
                showCheckpoints={showCheckpoints}
                onSelectRoute={selectRoute}
                onHoverCheckpoint={hoverCheckpoint}
              />
            </Suspense>
          ) : (
            <MapPlaceholder />
          )}
        </div>
      </div>

      {showMethodology && <MethodologyModal config={config} onClose={closeMethodology} />}
    </div>
  );
}

/** Holds the map's space while the 1 MB renderer loads. */
function MapPlaceholder() {
  return (
    <div
      className="map-wrap"
      role="status"
      style={{ display: 'grid', placeItems: 'center' }}
    >
      <span className="dim" style={{ fontSize: 12 }}>
        Loading map…
      </span>
    </div>
  );
}

function ErrorBanner({
  error,
}: {
  error: { code: string; message: string; fieldErrors: Record<string, string[]> };
}) {
  // Distinct copy per code: a driver can act on all of these, but only if the
  // message says what to actually do (BE-55).
  return (
    <div
      className="card"
      role="alert"
      style={{ padding: 'var(--space-3)', borderColor: 'var(--risk-severe)' }}
    >
      <strong style={{ color: 'var(--risk-severe)' }}>{error.message}</strong>
      <p className="muted" style={{ margin: '4px 0 0', fontSize: 12 }}>
        {guidanceFor(error.code)}
      </p>
    </div>
  );
}

function WarningList({ warnings }: { warnings: string[] }) {
  return (
    <div
      className="card"
      role="status"
      style={{ padding: 'var(--space-3)', borderColor: 'var(--risk-moderate)' }}
    >
      {warnings.map((warning) => (
        <p key={warning} className="muted" style={{ margin: 0, fontSize: 12 }}>
          {warning}
        </p>
      ))}
    </div>
  );
}
