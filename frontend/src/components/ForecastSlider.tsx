import { useMemo } from 'react';

import {
  formatAvgRisk,
  formatDateTime,
  formatDuration,
  formatMiles,
  routeViews,
  shiftedDeparture,
} from '@/lib/selectors';
import { useStore } from '@/store/useStore';
import type { AppConfig, RiskLevel } from '@/types/api';

interface ForecastSliderProps {
  config: AppConfig | null;
}

/**
 * The departure-offset slider (D13).
 *
 * This is the component the whole architecture was arranged around: dragging it
 * performs an array lookup per checkpoint and a weighted sum -- **no network
 * request, and no map re-render**, because the map subscribes to `analysis` and
 * not to `offsetHours`. Move the slider with the network tab open and confirm
 * there is no traffic.
 */
export function ForecastSlider({ config }: ForecastSliderProps) {
  const analysis = useStore((s) => s.analysis);
  const offsetHours = useStore((s) => s.offsetHours);
  const setOffset = useStore((s) => s.setOffset);
  const selectRoute = useStore((s) => s.selectRoute);

  const max = config?.slider.max_offset_hours ?? 48;
  const hasData = analysis.routes.length > 0;
  const departure = useMemo(
    () => (hasData ? shiftedDeparture(analysis, offsetHours) : null),
    [analysis, offsetHours, hasData],
  );

  const views = useMemo(() => (hasData ? routeViews(analysis, offsetHours) : []), [analysis, offsetHours, hasData]);
  const precision = config?.ranking.avg_risk_precision ?? 1;

  const baseWorst = useMemo(() => {
    if (!hasData) return null;
    return routeViews(analysis, 0).reduce<RiskLevel | null>((worst, view) => {
      const level = worstLevel(view.summary.worst_level);
      return worst === null || level > worst ? level : worst;
    }, null);
  }, [analysis, hasData]);

  const currentWorst = useMemo(() => {
    return views.reduce<RiskLevel | null>((worst, view) => {
      const level = worstLevel(view.summary.worst_level);
      return worst === null || level > worst ? level : worst;
    }, null);
  }, [views]);

  const worstChanged = baseWorst !== null && currentWorst !== null && baseWorst !== currentWorst;
  const worstWorsened = worstChanged && currentWorst! > baseWorst!;

  if (!hasData) return null;

  const recommendedId = analysis.recommendation.route_id;

  return (
    <section className="card" style={{ padding: 'var(--space-4)' }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'baseline', marginBottom: 4 }}>
        <h2 style={{ fontSize: 13 }}>If you leave later</h2>
        <strong className="mono" style={{ color: 'var(--accent)' }}>
          {offsetHours === 0 ? 'now' : `+${offsetHours} h`}
        </strong>
      </div>

      <p className="dim" style={{ margin: '0 0 8px', fontSize: 11 }}>
        Departs {departure ? formatDateTime(departure) : '—'}
      </p>

      <label className="field" style={{ gap: 4 }}>
        <span className="visually-hidden">Departure offset in hours</span>
        <input
          type="range"
          min={0}
          max={max}
          step={1}
          value={offsetHours}
          onChange={(e) => setOffset(Number(e.target.value))}
          style={{ width: '100%' }}
          aria-valuetext={
            offsetHours === 0
              ? 'departing at the planned time'
              : `departing ${offsetHours} hour${offsetHours === 1 ? '' : 's'} later`
          }
          aria-label="Departure offset in hours"
          list="offset-ticks"
        />
        <datalist id="offset-ticks">
          {[0, 6, 12, 24, 36, 48].map((h) => (
            <option key={h} value={h} />
          ))}
        </datalist>
      </label>

      <div
        style={{
          display: 'flex',
          justifyContent: 'space-between',
          fontSize: 10,
          color: 'var(--text-dim)',
          marginTop: -4,
        }}
      >
        {[0, 12, 24, 36, max].map((h) => (
          <span key={h}>{h === 0 ? 'now' : `+${h}h`}</span>
        ))}
      </div>

      {worstChanged && (
        <p
          className="muted"
          style={{ marginTop: 8, marginBottom: 0, fontSize: 12 }}
          role="status"
        >
          At this departure the worst rating on any route becomes{' '}
          <strong style={{ color: `var(--risk-${cssVar(currentWorst!)})` }}>
            {nameOf(currentWorst!)}
          </strong>
          {worstWorsened ? ' — worse than leaving now.' : ' — better than leaving now.'}
        </p>
      )}

      <div style={{ display: 'grid', gap: 6, marginTop: 10 }}>
        {views.map((view) => {
          const isRecommended = view.route.id === recommendedId;
          return (
            <button
              key={view.route.id}
              type="button"
              className="btn"
              onClick={() => selectRoute(view.route.id)}
              style={{
                display: 'grid',
                gridTemplateColumns: 'auto 1fr auto auto',
                gap: 10,
                alignItems: 'center',
                textAlign: 'left',
                borderColor: isRecommended ? 'var(--risk-low)' : 'var(--border)',
              }}
            >
              <span className="mono" style={{ fontSize: 11 }}>
                {view.route.label}
              </span>
              <span
                className="mono dim"
                style={{ fontSize: 11 }}
              >
                {formatMiles(view.route.road_miles)} · {formatDuration(view.route.duration_s)}
              </span>
              <span className="mono" style={{ fontSize: 12 }}>
                {formatAvgRisk(view.summary.avg_risk, precision)}
              </span>
              <span
                className="risk-badge"
                data-level={view.summary.worst_level}
                style={{ fontSize: 10 }}
              >
                {view.summary.worst_level.replace('_', ' ')}
              </span>
            </button>
          );
        })}
      </div>

      <p className="dim" style={{ margin: '8px 0 0', fontSize: 10 }}>
        Forecasts resolve hourly, so each checkpoint is read at the hour it is due.
      </p>
    </section>
  );
}

function worstLevel(name: string): RiskLevel {
  const index = ['LOW', 'MODERATE', 'HIGH', 'SEVERE', 'NO_TRAVEL'].indexOf(name);
  return (index >= 0 ? index + 1 : 1) as RiskLevel;
}

function nameOf(level: RiskLevel): string {
  return ['LOW', 'MODERATE', 'HIGH', 'SEVERE', 'NO_TRAVEL'][level - 1] ?? 'LOW';
}

function cssVar(level: RiskLevel): string {
  return ['low', 'moderate', 'high', 'severe', 'no-travel'][level - 1] ?? 'low';
}
