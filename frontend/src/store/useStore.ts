/**
 * Application store.
 *
 * Zustand rather than context because the slider updates at 60 fps while the
 * user drags: every consumer of `offsetHours` re-renders, and routing that
 * through a context provider would re-render the whole tree each frame. With
 * Zustand, selectors subscribe independently, so the map does not re-render when
 * only the offset changes (FE-17).
 */

import { create } from 'zustand';

import { emptyAnalysis, type StreamEvent, type TripRequestBody } from '@/lib/api';
import type { Analysis, AppConfig, RiskLevel, RouteSummaryBundle } from '@/types/api';

export type Phase = 'idle' | 'streaming' | 'complete' | 'error';

interface AppState {
  // --- request ---
  origin: string;
  destination: string;
  departure: string;
  loadLb: number;
  spacingMi: number;
  truckSpeedFactor: number;
  truckMinAvgMph: number;
  truckMaxAvgMph: number;

  // --- server state ---
  phase: Phase;
  analysis: Analysis;
  config: AppConfig | null;
  error: { code: string; message: string; fieldErrors: Record<string, string[]> } | null;

  // --- UI state ---
  offsetHours: number;
  selectedRouteId: string | null;
  hoveredCheckpointId: string | null;
  showHeatmap: boolean;
  showCheckpoints: boolean;
  showRiskStrip: boolean;

  // --- actions ---
  setField: <K extends keyof AppState>(key: K, value: AppState[K]) => void;
  setConfig: (config: AppConfig) => void;
  beginStream: () => void;
  applyEvent: (event: StreamEvent) => void;
  finishStream: () => void;
  failStream: (error: { code: string; message: string; fieldErrors?: Record<string, string[]> }) => void;
  setOffset: (hours: number) => void;
  selectRoute: (routeId: string | null) => void;
  hoverCheckpoint: (id: string | null) => void;
  toggleHeatmap: () => void;
  toggleCheckpoints: () => void;
  toggleRiskStrip: () => void;
  reset: () => void;
}

const INITIAL = {
  origin: 'Chicago, IL',
  destination: 'Detroit, MI',
  loadLb: 40_000,
  spacingMi: 25,
  truckSpeedFactor: 0.85,
  truckMinAvgMph: 25,
  truckMaxAvgMph: 65,
  // Default departure: three hours' time, rounded to the next half hour, in
  // local time so the form shows something a driver recognises.
  departure: defaultDeparture(),
};

/** A sensible default departure: the next :00 or :30, at least 2 h out. */
function defaultDeparture(): string {
  const when = new Date(Date.now() + 3 * 3_600_000);
  when.setMinutes(when.getMinutes() > 30 ? 60 : 30, 0, 0);
  // `datetime-local` needs a local-time string without a zone suffix.
  const pad = (n: number) => String(n).padStart(2, '0');
  return `${when.getFullYear()}-${pad(when.getMonth() + 1)}-${pad(when.getDate())}T${pad(when.getHours())}:${pad(when.getMinutes())}`;
}

export const useStore = create<AppState>((set, get) => ({
  ...INITIAL,
  phase: 'idle',
  analysis: emptyAnalysis(),
  config: null,
  error: null,
  offsetHours: 0,
  selectedRouteId: null,
  hoveredCheckpointId: null,
  showHeatmap: true,
  showCheckpoints: true,
  // Off by default: the strip is a detail for a driver studying the corridor,
  // and three overlapping overlays at once is noise rather than information.
  showRiskStrip: false,

  setField: (key, value) => set({ [key]: value } as Partial<AppState>),

  setConfig: (config) => set({ config }),

  beginStream: () => set({ phase: 'streaming', error: null, analysis: emptyAnalysis() }),

  /**
   * Fold one stream event into the analysis.
   *
   * Each branch replaces rather than merges its slice, so replaying a cached
   * stream and a live one produce the same state (D19).
   */
  applyEvent: (event) =>
    set((state) => {
      const analysis = state.analysis;

      switch (event.type) {
        case 'meta': {
          const e = event as Extract<StreamEvent, { type: 'meta' }> & Record<string, unknown>;
          return {
            analysis: {
              ...analysis,
              meta: {
                ...analysis.meta,
                origin: (e.origin ?? analysis.meta.origin) as Analysis['meta']['origin'],
                destination: (e.destination ?? analysis.meta.destination) as Analysis['meta']['destination'],
                departure: (e.departure ?? analysis.meta.departure) as string,
                load_lb: (e.load_lb ?? analysis.meta.load_lb) as number,
                spacing_mi: (e.spacing_mi ?? analysis.meta.spacing_mi) as number,
                slider_hours: (e.slider_hours ?? analysis.meta.slider_hours) as number,
                time_snap: (e.time_snap ?? analysis.meta.time_snap) as string,
                cached: (e.cached ?? false) as boolean,
              },
            },
          };
        }

        case 'route': {
          const e = event as Extract<StreamEvent, { type: 'route' }>;
          // Routes stream in order; a duplicate id (cached replay) replaces.
          const routes = analysis.routes.filter((r) => r.id !== e.id);
          routes.push(e);
          routes.sort((a, b) => a.index - b.index);
          return {
            analysis: {
              ...analysis,
              routes,
              progress: {
                ...analysis.progress,
                routesReady: routes.length >= 1,
              },
            },
            // Auto-select the first route so the map has a focus.
            selectedRouteId: state.selectedRouteId ?? e.id,
          };
        }

        case 'weather_plan': {
          const e = event as Extract<StreamEvent, { type: 'weather_plan' }>;
          return {
            analysis: {
              ...analysis,
              meta: {
                ...analysis.meta,
                hour_window_start: e.hour_window_start,
                hour_window_end: e.hour_window_end,
                hour_window_hours: e.hour_window_hours,
                weather_locations: e.weather_locations,
              },
              progress: { ...analysis.progress, checkpointTotal: e.checkpoint_count },
            },
          };
        }

        case 'weather':
          return { analysis };

        case 'checkpoints': {
          const e = event as Extract<StreamEvent, { type: 'checkpoints' }>;
          const checkpoints = analysis.checkpoints.filter((c) => c.route_id !== e.route_id);
          checkpoints.push(e);
          checkpoints.sort(
            (a, b) =>
              (analysis.routes.find((r) => r.id === a.route_id)?.index ?? 0) -
              (analysis.routes.find((r) => r.id === b.route_id)?.index ?? 0),
          );
          const loaded = checkpoints.reduce((sum, c) => sum + c.points.length, 0);
          return {
            analysis: {
              ...analysis,
              checkpoints,
              progress: { ...analysis.progress, checkpointsLoaded: loaded },
            },
          };
        }

        case 'summary': {
          const e = event as Extract<StreamEvent, { type: 'summary' }>;
          const { route_id: routeId, type: _type, ...summary } = e;
          const summaries = analysis.summaries.filter((s) => s.route_id !== routeId);
          summaries.push({ ...summary, route_id: routeId } as RouteSummaryBundle);
          summaries.sort(
            (a, b) =>
              (analysis.routes.find((r) => r.id === a.route_id)?.index ?? 0) -
              (analysis.routes.find((r) => r.id === b.route_id)?.index ?? 0),
          );
          // The stream's route event has geometry but no summary. Attach the
          // separate summary event to its route so selectors and map builders
          // see the same shape as the complete JSON payload.
          const routes = analysis.routes.map((route) =>
            route.id === routeId ? { ...route, summary } : route,
          );
          return { analysis: { ...analysis, routes, summaries } };
        }

        case 'recommendation': {
          const e = event as Extract<StreamEvent, { type: 'recommendation' }>;
          return {
            analysis: {
              ...analysis,
              recommendation: {
                route_id: e.route_id,
                label: e.label,
                ranking: e.ranking,
                rationale: e.rationale,
                criterion_tally: e.criterion_tally,
                all_blocked: e.all_blocked,
                safety_gate_enabled: true,
              },
            },
            // Follow the server's ranking unless the user has chosen a route.
            selectedRouteId: state.selectedRouteId ?? e.route_id,
          };
        }

        case 'done': {
          const e = event as Extract<StreamEvent, { type: 'done' }>;
          return {
            analysis: {
              ...analysis,
              warnings: e.warnings,
              meta: {
                ...analysis.meta,
                weather_locations: e.weather_locations,
                elapsed_ms: e.elapsed_ms,
                cached: e.cached ?? false,
              },
            },
            phase: 'complete',
          };
        }

        default:
          return { analysis };
      }
    }),

  finishStream: () => set({ phase: 'complete' }),

  failStream: (error) =>
    set({
      phase: 'error',
      error: { code: error.code, message: error.message, fieldErrors: error.fieldErrors ?? {} },
    }),

  /** Clamped to the configured slider horizon so the UI cannot ask for nonsense. */
  setOffset: (hours) => {
    const max = get().config?.slider.max_offset_hours ?? 48;
    set({ offsetHours: Math.max(0, Math.min(Math.round(hours), max)) });
  },

  selectRoute: (routeId) => set({ selectedRouteId: routeId }),
  hoverCheckpoint: (id) => set({ hoveredCheckpointId: id }),
  toggleHeatmap: () => set((s) => ({ showHeatmap: !s.showHeatmap })),
  toggleCheckpoints: () => set((s) => ({ showCheckpoints: !s.showCheckpoints })),
  toggleRiskStrip: () => set((s) => ({ showRiskStrip: !s.showRiskStrip })),

  reset: () =>
    set({
      phase: 'idle',
      analysis: emptyAnalysis(),
      error: null,
      offsetHours: 0,
      selectedRouteId: null,
      hoveredCheckpointId: null,
    }),
}));

/** Request body for the current form state. */
export function requestBody(state: Pick<
  AppState,
  'origin' | 'destination' | 'departure' | 'loadLb' | 'spacingMi' | 'truckSpeedFactor' | 'truckMinAvgMph' | 'truckMaxAvgMph'
>): TripRequestBody {
  return {
    origin: state.origin,
    destination: state.destination,
    // The form holds a local time; send it as UTC so the server and the ETA
    // display agree without the browser guessing a zone twice.
    departure: new Date(state.departure).toISOString(),
    load_lb: state.loadLb,
    spacing_mi: state.spacingMi,
    slider_hours: 48,
    truck_speed_factor: state.truckSpeedFactor,
    truck_min_avg_mph: state.truckMinAvgMph,
    truck_max_avg_mph: state.truckMaxAvgMph,
  };
}

/** Colour for a level, from the server-provided palette (never hard-coded). */
export function levelColor(config: AppConfig | null, level: RiskLevel): string {
  const meta = config?.levels.find((l) => l.value === level);
  return meta?.hex ?? '#94a3b8';
}
