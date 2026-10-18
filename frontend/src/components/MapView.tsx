import { useCallback, useEffect, useRef, useState } from 'react';
import * as maplibregl from 'maplibre-gl';
import type {
  ExpressionSpecification,
  GeoJSONSource,
  LngLatBoundsLike,
  Map as MapLibreMap,
} from 'maplibre-gl';

import 'maplibre-gl/dist/maplibre-gl.css';

import { levelColorExpression } from '@/lib/theme';
import {
  buildCheckpointsCollection,
  buildCorridorCollection,
  buildEndpointsCollection,
  buildRiskStripCollection,
  buildRoutesCollection,
  riskStripStops,
} from '@/lib/mapData';
import { formatDateTime, etaAtOffset } from '@/lib/selectors';
import { useStore } from '@/store/useStore';
import { RiskLevel, type Analysis, type AppConfig, type CheckpointBundle, type RouteOption } from '@/types/api';

const STYLE_URL = 'https://tiles.openfreemap.org/styles/liberty';

/** Per-route line colours; the *risk* colours come from the server. */
const ROUTE_COLORS = ['#38bdf8', '#a78bfa', '#fbbf24'];

interface MapViewProps {
  analysis: Analysis;
  /** Rule configuration, for the map's colour palette. */
  config: AppConfig | null;
  selectedRouteId: string | null;
  showHeatmap: boolean;
  showCheckpoints: boolean;
  onSelectRoute: (routeId: string | null) => void;
  onHoverCheckpoint: (id: string | null) => void;
}

export function MapView({
  analysis,
  config,
  selectedRouteId,
  showHeatmap,
  showCheckpoints,
  onSelectRoute,
  onHoverCheckpoint,
}: MapViewProps) {
  const container = useRef<HTMLDivElement | null>(null);
  const map = useRef<MapLibreMap | null>(null);
  const ready = useRef(false);
  // A ref guards imperative source updates; state retries effects that ran
  // before the asynchronous style load (listeners and layer visibility).
  const [mapReady, setMapReady] = useState(false);
  const fittedRoute = useRef<string | null>(null);
  const [mapError, setMapError] = useState<string | null>(null);
  // Subscribed rather than passed as a prop: a layer toggle is local UI state,
  // and threading it through `App` would re-render the shell with the map.
  const showRiskStrip = useStore((s) => s.showRiskStrip);

  // Keep the latest state in a ref so layer setup can read it without being
  // torn down and rebuilt on every slider tick.
  const latest = useRef({ analysis, selectedRouteId, showHeatmap, showCheckpoints, showRiskStrip, config });
  latest.current = { analysis, selectedRouteId, showHeatmap, showCheckpoints, showRiskStrip, config };

  const onSelectRouteRef = useRef(onSelectRoute);
  onSelectRouteRef.current = onSelectRoute;
  const onHoverRef = useRef(onHoverCheckpoint);
  onHoverRef.current = onHoverCheckpoint;

  /** Fetch a source narrowed to GeoJSONSource, so `setData` is available. */
  const geoSource = useCallback((id: string): GeoJSONSource | undefined => {
    const found = map.current?.getSource(id);
    return found && 'setData' in found ? (found as GeoJSONSource) : undefined;
  }, []);

  /** Push current state into the sources. Cheap: a handful of small objects. */
  const syncSources = useCallback(() => {
    const m = map.current;
    if (!m || !ready.current) return;
    const { analysis: a, selectedRouteId: selected } = latest.current;
    // Read the offset imperatively. A selector hook would re-render this
    // component on every slider tick, and this is the one holding a WebGL
    // context; `setData` needs no render at all (FE-17/FE-33).
    const offset = useStore.getState().offsetHours;
    if (a.routes.length === 0) return;

    const activeId = selected ?? a.routes[0]?.id ?? null;
    const route = a.routes.find((r) => r.id === activeId) ?? a.routes[0];
    const bundle = a.checkpoints.find((c) => c.route_id === route.id);

    geoSource('routes')?.setData(buildRoutesCollection(a));
    geoSource('risk-strip')?.setData(buildRiskStripCollection(route));
    updateRiskStrip(m, bundle, route, offset, latest.current.config, latest.current.showRiskStrip);
    geoSource('endpoints')?.setData(buildEndpointsCollection(a));
    geoSource('checkpoints')?.setData(buildCheckpointsCollection(bundle, route, offset));
    geoSource('corridor')?.setData(buildCorridorCollection(bundle, route, offset));

    // Dim the routes that are not selected.
    const isActive: ExpressionSpecification = ['==', ['get', 'id'], activeId ?? ''];
    m.setPaintProperty('route-casing', 'line-opacity', ['case', isActive, 0.85, 0.3]);
    m.setPaintProperty('route-line', 'line-opacity', ['case', isActive, 1, 0.5]);
    m.setPaintProperty('route-line', 'line-width', ['case', isActive, 4.5, 2.5]);
    m.setPaintProperty('checkpoints', 'circle-opacity', activeId ? 0.95 : 0);
  }, [geoSource]);

  // Create the map once.
  useEffect(() => {
    if (!container.current || map.current) return;
    // WebGL can be unavailable or blocklisted, and the MapLibre constructor
    // throws in that case rather than degrading. Catching it keeps the route
    // numbers usable, which is what the user came for.
    let m: MapLibreMap;
    try {
      m = new maplibregl.Map({
      container: container.current,
        style: STYLE_URL,
        center: [-87.63, 41.88],
        zoom: 5,
        attributionControl: { compact: true },
        cooperativeGestures: true,
      });
    } catch (cause) {
      setMapError(
        `The map could not start: ${cause instanceof Error ? cause.message : 'unknown error'}. The route comparison below is unaffected.`,
      );
      return;
    }
    map.current = m;
    m.addControl(new maplibregl.NavigationControl({ showCompass: false }), 'top-right');
    m.addControl(new maplibregl.ScaleControl({ maxWidth: 120, unit: 'imperial' }), 'bottom-left');

    m.on('load', () => {
      m.addSource('corridor', { type: 'geojson', data: { type: 'FeatureCollection', features: [] } });
      m.addSource('routes', { type: 'geojson', data: { type: 'FeatureCollection', features: [] } });
      m.addSource('endpoints', { type: 'geojson', data: { type: 'FeatureCollection', features: [] } });
      m.addSource('checkpoints', { type: 'geojson', data: { type: 'FeatureCollection', features: [] } });
      // `lineMetrics` is required for `line-gradient`. It costs line
      // simplification, which is why this is a separate source carrying only
      // the selected route.
      m.addSource('risk-strip', {
        type: 'geojson',
        lineMetrics: true,
        data: { type: 'FeatureCollection', features: [] },
      });

      // Heatmap sits beneath the lines so the corridor stays readable.
      m.addLayer({
        id: 'corridor-heat',
        type: 'heatmap',
        source: 'corridor',
        layout: { visibility: 'visible' },
        paint: {
          'heatmap-weight': ['coalesce', ['get', 'weight'], 0] as ExpressionSpecification,
          'heatmap-intensity': ['interpolate', ['linear'], ['zoom'], 3, 0.9, 10, 2.4],
          'heatmap-color': [
            'interpolate',
            ['linear'],
            ['heatmap-density'],
            0, 'rgba(0,0,0,0)',
            0.2, 'rgba(22,163,74,0.45)',
            0.45, 'rgba(202,138,4,0.55)',
            0.7, 'rgba(234,88,12,0.7)',
            0.88, 'rgba(220,38,38,0.82)',
            1, 'rgba(109,40,217,0.9)',
          ],
          'heatmap-radius': ['interpolate', ['linear'], ['zoom'], 3, 10, 8, 28, 12, 52],
          'heatmap-opacity': ['interpolate', ['linear'], ['zoom'], 6, 0.85, 11, 0.35],
        },
      });

      m.addLayer({
        id: 'route-casing',
        type: 'line',
        source: 'routes',
        layout: { 'line-join': 'round', 'line-cap': 'round' },
        paint: {
          'line-color': '#020617',
          // A constant halo: it sits under the coloured line and only needs to
          // be a little wider, so no data property is involved.
          'line-width': 8,
          'line-opacity': 0.55,
        },
      });

      m.addLayer({
        id: 'route-line',
        type: 'line',
        source: 'routes',
        layout: { 'line-join': 'round', 'line-cap': 'round' },
        paint: {
          'line-color': [
            'match',
            ['get', 'index'],
            0, ROUTE_COLORS[0],
            1, ROUTE_COLORS[1],
            2, ROUTE_COLORS[2],
            ROUTE_COLORS[0],
          ],
          'line-width': 3,
          'line-opacity': 0.8,
        },
      });

      m.addLayer({
        id: 'endpoints',
        type: 'circle',
        source: 'endpoints',
        paint: {
          'circle-radius': 7,
          'circle-color': ['match', ['get', 'kind'], 'origin', '#22c55e', '#38bdf8'],
          'circle-stroke-color': '#020617',
          'circle-stroke-width': 2.5,
        },
      });

      m.addLayer({
        id: 'checkpoints',
        type: 'circle',
        source: 'checkpoints',
        paint: {
          'circle-radius': ['interpolate', ['linear'], ['get', 'level'], 1, 3.5, 3, 5, 5, 6.5],
          'circle-color': levelColorExpression(latest.current.config),
          'circle-opacity': 0.95,
          'circle-stroke-color': '#0b1120',
          'circle-stroke-width': 1,
          // No Travel gets a ring so it reads without relying on hue alone.
          'circle-stroke-opacity': ['case', ['==', ['get', 'level'], 5], 1, 0.6],
        },
      });

      m.addLayer({
        id: 'risk-strip',
        type: 'line',
        source: 'risk-strip',
        layout: {
          'line-cap': 'butt',
          'line-join': 'round',
          // Added last, so it sits above the route lines: the gradient is the
          // risk readout, and it is the thing a driver is looking for. Under a
          // 3-4.5px line it is simply invisible.
          visibility: 'none',
        },
        paint: {
          'line-width': ['interpolate', ['linear'], ['zoom'], 4, 2, 10, 5],
          'line-opacity': 0.95,
          'line-gradient': ['interpolate', ['linear'], ['line-progress'], 0, '#16a34a', 1, '#16a34a'],
        },
      });

      ready.current = true;
      syncSources();
      setMapReady(true);
    });

    m.on('error', (event) => {
      // Style or tile failure: surface it rather than leaving a black canvas.
      if (event.error) console.error('map error:', event.error);
    });

    return () => {
      ready.current = false;
      m.remove();
      map.current = null;
    };
    // Intentionally mount-only: state flows through `syncSources`.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [geoSource]);

  /**
   * Fit the viewport to a route (or all routes).
   *
   * Returns whether the fit actually ran, so a caller can tell an aborted fit
   * (style still loading) from a finished one. A `void` return made that
   * indistinguishable, which is how a bailed first fit came to look like a
   * completed one.
   */
  const fit = useCallback((routeId: string | null): boolean => {
    const m = map.current;
    const { analysis: a } = latest.current;
    if (!m || !ready.current) return false;
    const bounds = routeId
      ? a.routes.find((r) => r.id === routeId)?.geometry
      : a.routes.flatMap((r) => r.geometry);
    if (!bounds || bounds.length < 2) return false;
    const lons = bounds.map((c) => c[0]);
    const lats = bounds.map((c) => c[1]);
    const box: LngLatBoundsLike = [
      [Math.min(...lons), Math.min(...lats)],
      [Math.max(...lons), Math.max(...lats)],
    ];
    m.fitBounds(box, { padding: { top: 60, bottom: 60, left: 60, right: 60 }, duration: 700, maxZoom: 11 });
    return true;
  }, []);

  function fitAll() {
    fit(null);
  }

  // Sync data whenever the analysis or the layer toggles change.
  useEffect(() => {
    syncSources();
  }, [analysis, selectedRouteId, showHeatmap, showCheckpoints, showRiskStrip, syncSources]);

  // The departure offset drives a repaint of two small sources and nothing
  // else. Subscribed imperatively so dragging the slider repaints the map
  // without re-rendering the component that owns the WebGL context.
  useEffect(() => useStore.subscribe(syncSources), [syncSources]);

  // Fit once, when routes first arrive.
  //
  // The marker is set only if the fit actually ran. `fit()` bails when the
  // style has not finished loading, which is the common case: MapView mounts as
  // soon as routes exist, while the map's `load` event fires later. Marking
  // first meant a bailed fit was never retried, and the user was left on the
  // default zoom until they clicked "Fit all" by hand.
  useEffect(() => {
    if (analysis.routes.length > 0 && fittedRoute.current === null && fit(null)) {
      fittedRoute.current = 'all';
    }
  }, [analysis.routes.length, fit, mapReady]);

  // Layer visibility toggles.
  useEffect(() => {
    const m = map.current;
    if (!m || !ready.current) return;
    if (m.getLayer('corridor-heat')) {
      m.setLayoutProperty('corridor-heat', 'visibility', showHeatmap ? 'visible' : 'none');
    }
    if (m.getLayer('checkpoints')) {
      m.setLayoutProperty('checkpoints', 'visibility', showCheckpoints ? 'visible' : 'none');
    }
  }, [showHeatmap, showCheckpoints, mapReady]);

  // The map may finish loading before /config/ returns. CSS tokens update when
  // it does, but MapLibre paint expressions need an explicit refresh with the
  // server's literal hex values (they cannot resolve CSS custom properties).
  useEffect(() => {
    const m = map.current;
    if (config && m && ready.current && m.getLayer('checkpoints')) {
      m.setPaintProperty('checkpoints', 'circle-color', levelColorExpression(config));
    }
  }, [config, mapReady]);

  // --- interaction ---------------------------------------------------------

  useEffect(() => {
    const m = map.current;
    if (!m || !ready.current) return;

    const onMove = (event: maplibregl.MapMouseEvent) => {
      const features = m.queryRenderedFeatures(event.point, { layers: ['checkpoints'] });
      if (features.length === 0) {
        m.getCanvas().style.cursor = '';
        onHoverRef.current(null);
        return;
      }
      m.getCanvas().style.cursor = 'pointer';
      const id = features[0].properties?.id as string;
      onHoverRef.current(id);
    };

    const onClick = (event: maplibregl.MapMouseEvent) => {
      const onLine = m.queryRenderedFeatures(event.point, { layers: ['route-line'] });
      if (onLine.length > 0) {
        const id = onLine[0].properties?.id as string;
        onSelectRouteRef.current(id);
        fit(id);
        return;
      }
      const onDot = m.queryRenderedFeatures(event.point, { layers: ['checkpoints'] });
      if (onDot.length > 0) {
        onHoverRef.current(onDot[0].properties?.id as string);
      }
    };

    m.on('mousemove', onMove);
    m.on('click', onClick);
    return () => {
      m.off('mousemove', onMove);
      m.off('click', onClick);
    };
  }, [fit, mapReady]);

  return (
    <div className="map-wrap">
      <div ref={container} style={{ position: 'absolute', inset: 0 }} />
      {mapError ? (
        // Announced, not just drawn: a screen-reader user must learn that the
        // map is missing rather than finding an empty box.
        <div
          role="status"
          style={{
            position: 'absolute',
            inset: 0,
            display: 'grid',
            placeItems: 'center',
            padding: 'var(--space-4)',
            textAlign: 'center',
          }}
        >
          <p className="muted" style={{ margin: 0, maxWidth: 380, fontSize: 12 }}>
            {mapError}
          </p>
        </div>
      ) : (
        <>
          <MapControls onFitAll={fitAll} onFitRoute={() => fit(selectedRouteId)} />
          {/* Mounted only while hovering, so the offset subscription below is
              live only during a hover -- and a hover is exactly when a shifted
              ETA needs to be correct. */}
          <CheckpointTooltip
            analysis={analysis}
            selectedRouteId={selectedRouteId ?? analysis.routes[0]?.id ?? null}
          />
        </>
      )}
    </div>
  );
}

function MapControls({
  onFitAll,
  onFitRoute,
}: {
  onFitAll: () => void;
  onFitRoute: () => void;
}) {
  const showHeatmap = useStoreFlag('showHeatmap', 'toggleHeatmap');
  const showCheckpoints = useStoreFlag('showCheckpoints', 'toggleCheckpoints');
  const showRiskStrip = useStoreFlag('showRiskStrip', 'toggleRiskStrip');

  return (
    <div
      style={{
        position: 'absolute',
        top: 12,
        left: 12,
        display: 'flex',
        gap: 6,
        zIndex: 2,
      }}
    >
      <button className="btn" onClick={onFitAll} type="button">
        Fit all
      </button>
      <button className="btn" onClick={onFitRoute} type="button">
        Fit route
      </button>
      <button
        className="btn"
        onClick={showHeatmap.toggle}
        type="button"
        aria-pressed={showHeatmap.value}
      >
        {showHeatmap.value ? 'Hide heatmap' : 'Show heatmap'}
      </button>
      <button
        className="btn"
        onClick={showCheckpoints.toggle}
        type="button"
        aria-pressed={showCheckpoints.value}
      >
        {showCheckpoints.value ? 'Hide checkpoints' : 'Show checkpoints'}
      </button>
      <button
        className="btn"
        onClick={showRiskStrip.toggle}
        type="button"
        aria-pressed={showRiskStrip.value}
      >
        {showRiskStrip.value ? 'Hide risk strip' : 'Show risk strip'}
      </button>
    </div>
  );
}

function CheckpointTooltip({
  analysis,
  selectedRouteId,
}: {
  analysis: Analysis;
  selectedRouteId: string | null;
}) {
  const hoveredId = useStoreValue('hoveredCheckpointId');
  const offsetHours = useStore((s) => s.offsetHours);
  if (!hoveredId || !selectedRouteId) return null;
  const bundle = analysis.checkpoints.find((c) => c.route_id === selectedRouteId);
  const index = Number(hoveredId.split(':')[1]);
  const point = bundle?.points[index];
  if (!point) return null;

  const eta = etaAtOffset(point, offsetHours);

  return (
    <div
      role="tooltip"
      style={{
        position: 'absolute',
        left: 12,
        bottom: 12,
        zIndex: 2,
        maxWidth: 340,
        padding: 12,
      }}
      className="card"
    >
      <div style={{ display: 'flex', justifyContent: 'space-between', gap: 12, marginBottom: 6 }}>
        <strong>{point.distance_mi.toFixed(0)} mi</strong>
        <span className="dim mono">{formatDateTime(eta)}</span>
      </div>
      <div style={{ display: 'flex', gap: 14, flexWrap: 'wrap', marginBottom: 6 }}>
        <Reading label="Wind" value={point.risk.wind.value} unit="mph" />
        <Reading label="Rain" value={point.risk.rain.value} unit="in/hr" />
        <Reading label="Snow" value={point.risk.snow.value} unit="in/hr" />
      </div>
      <ul style={{ margin: 0, paddingLeft: 16, color: 'var(--text-muted)', fontSize: 12 }}>
        {point.risk.reasons.map((reason) => (
          <li key={reason}>{reason}</li>
        ))}
      </ul>
      {offsetHours > 0 && (
        <p className="dim" style={{ margin: '6px 0 0', fontSize: 11 }}>
          Times shifted by +{offsetHours} h departure offset.
        </p>
      )}
    </div>
  );
}

function Reading({ label, value, unit }: { label: string; value: number | null; unit: string }) {
  return (
    <span className="mono" style={{ fontSize: 12 }}>
      <span className="dim">{label} </span>
      {value === null ? '—' : `${value.toFixed(unit === 'mph' ? 0 : 2)} ${unit}`}
    </span>
  );
}

function useStoreFlag(
  key: 'showHeatmap' | 'showCheckpoints' | 'showRiskStrip',
  toggleKey: 'toggleHeatmap' | 'toggleCheckpoints' | 'toggleRiskStrip',
) {
  const value = useStore((s) => s[key]);
  const toggle = useStore((s) => s[toggleKey]);
  return { value, toggle };
}

function useStoreValue<K extends 'hoveredCheckpointId'>(key: K) {
  return useStore((s) => s[key]);
}

/**
 * Repaint the graduated risk strip (FE-15).
 *
 * MapLibre cannot resolve CSS custom properties in a paint expression, so the
 * colours come from `/config/` as literal hex, the same as the checkpoint dots.
 * The gradient is rebuilt from the checkpoint levels at the current offset,
 * which is why dragging the slider repaints it.
 */
function updateRiskStrip(
  map: MapLibreMap,
  bundle: CheckpointBundle | undefined,
  route: RouteOption,
  offsetHours: number,
  config: AppConfig | null,
  visible: boolean,
): void {
  if (!map.getLayer('risk-strip')) return;
  map.setLayoutProperty('risk-strip', 'visibility', visible ? 'visible' : 'none');
  if (!visible) return;

  const stops = riskStripStops(bundle, route.geometry, offsetHours);
  const color = (level: RiskLevel) => levelColor(config, level);

  if (stops.length < 2) {
    // A single stop has no gradient; fall back to a flat, honest colour rather
    // than emitting a malformed expression MapLibre would reject.
    const level = stops[0]?.level ?? RiskLevel.LOW;
    map.setPaintProperty('risk-strip', 'line-gradient', [
      'interpolate',
      ['linear'],
      ['line-progress'],
      0,
      color(level),
      1,
      color(level),
    ] as ExpressionSpecification);
    return;
  }

  const expression: unknown[] = ['interpolate', ['linear'], ['line-progress']];
  for (const stop of stops) {
    expression.push(stop.progress, color(stop.level));
  }
  map.setPaintProperty('risk-strip', 'line-gradient', expression as ExpressionSpecification);
}

/** Hex for a level, from the server palette; a neutral fallback before it loads. */
function levelColor(config: AppConfig | null, level: RiskLevel): string {
  return config?.levels.find((l) => l.value === level)?.hex ?? '#64748b';
}
