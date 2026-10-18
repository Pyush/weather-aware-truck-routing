import { useMemo } from 'react';

import {
  formatAvgRisk,
  formatDateTime,
  formatDuration,
  formatMiles,
  routeViews,
  shiftedDeparture,
  type RouteView,
} from '@/lib/selectors';
import { useStore } from '@/store/useStore';
import type { AppConfig } from '@/types/api';

interface RoutePanelProps {
  config: AppConfig | null;
}

export function RoutePanel({ config }: RoutePanelProps) {
  const analysis = useStore((s) => s.analysis);
  const offsetHours = useStore((s) => s.offsetHours);
  const selectedRouteId = useStore((s) => s.selectedRouteId);
  const selectRoute = useStore((s) => s.selectRoute);
  const hoveredId = useStore((s) => s.hoveredCheckpointId);
  const hoverCheckpoint = useStore((s) => s.hoverCheckpoint);

  const views = useMemo(() => routeViews(analysis, offsetHours), [analysis, offsetHours]);
  const ranking = analysis.recommendation.ranking;
  const ordered = useMemo(
    () => [...views].sort((a, b) => ranking.indexOf(a.route.id) - ranking.indexOf(b.route.id)),
    [views, ranking],
  );

  if (analysis.routes.length === 0) return null;

  return (
    <section className="card" style={{ padding: 'var(--space-4)' }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'baseline' }}>
        <h2 style={{ fontSize: 13 }}>Routes</h2>
        {offsetHours > 0 && (
          <span className="dim" style={{ fontSize: 11 }}>
            departing {formatDateTime(shiftedDeparture(analysis, offsetHours))}
          </span>
        )}
      </div>

      <RecommendationBanner />

      <div style={{ display: 'grid', gap: 'var(--space-3)', marginTop: 'var(--space-3)' }}>
        {ordered.map((view) => (
          <RouteCard
            key={view.route.id}
            view={view}
            config={config}
            selected={view.route.id === (selectedRouteId ?? analysis.recommendation.route_id)}
            rank={ranking.indexOf(view.route.id) + 1}
            tally={analysis.recommendation.criterion_tally[view.route.id]}
            offsetHours={offsetHours}
            onSelect={() => selectRoute(view.route.id)}
          />
        ))}
      </div>

      {selectedRouteId && (
        <CheckpointTable
          route={views.find((v) => v.route.id === selectedRouteId)}
          offsetHours={offsetHours}
          hoveredId={hoveredId}
          onHover={hoverCheckpoint}
        />
      )}
    </section>
  );
}

function RecommendationBanner() {
  const analysis = useStore((s) => s.analysis);
  if (!analysis.recommendation.route_id) return null;
  const blocked = analysis.recommendation.all_blocked;

  return (
    <div
      className="card"
      role="status"
      style={{
        padding: 'var(--space-3)',
        marginTop: 'var(--space-3)',
        borderColor: blocked ? 'var(--risk-severe)' : 'var(--risk-low)',
      }}
    >
      <strong style={{ color: blocked ? 'var(--risk-severe)' : 'var(--risk-low)' }}>
        {blocked ? 'No route is safe right now' : `Recommended: ${analysis.recommendation.label}`}
      </strong>
      <ul style={{ margin: '6px 0 0', paddingLeft: 16, fontSize: 12, color: 'var(--text-muted)' }}>
        {analysis.recommendation.rationale.map((line) => (
          <li key={line}>{line}</li>
        ))}
      </ul>
    </div>
  );
}

function RouteCard({
  view,
  config,
  selected,
  rank,
  tally,
  offsetHours,
  onSelect,
}: {
  view: RouteView;
  config: AppConfig | null;
  selected: boolean;
  rank: number;
  tally?: Record<string, number>;
  offsetHours: number;
  onSelect: () => void;
}) {
  const { route, summary, differsFromBase } = view;
  const analysis = useStore((s) => s.analysis);
  const recommended = analysis.recommendation.route_id === route.id;
  const precision = config?.ranking.avg_risk_precision ?? 1;
  const arrival = shiftedDeparture(analysis, offsetHours);
  const arrivalTime = new Date(arrival.getTime() + route.duration_s * 1000);

  return (
    <article
      className="card"
      style={{
        padding: 'var(--space-3)',
        cursor: 'pointer',
        borderColor: selected ? 'var(--accent)' : 'var(--border)',
        background: selected ? 'var(--bg-overlay)' : 'var(--bg-raised)',
      }}
      onClick={onSelect}
      onKeyDown={(e) => {
        if (e.key === 'Enter' || e.key === ' ') {
          e.preventDefault();
          onSelect();
        }
      }}
      role="button"
      tabIndex={0}
      aria-pressed={selected}
      aria-label={
        `${route.label}, rank ${rank} of ${analysis.routes.length}` +
        `${recommended ? ', recommended' : ''}. ` +
        `${formatMiles(route.road_miles)}, ${formatDuration(route.duration_s)} drive, ` +
        `arrive ${arrivalTime.toLocaleTimeString([], { hour: 'numeric', minute: '2-digit' })}. ` +
        `Average risk ${formatAvgRisk(summary.avg_risk, precision)}, ` +
        `worst ${summary.worst_level.replace('_', ' ').toLowerCase()}.`
      }
    >
      <header style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 6 }}>
        <span className="dim mono" style={{ fontSize: 12 }}>
          #{rank}
        </span>
        <strong>{route.label}</strong>
        {recommended && (
          <span className="risk-badge" data-level="LOW">
            Recommended
          </span>
        )}
        {route.source === 'osrm_via_variant' && (
          <span className="dim" style={{ fontSize: 10 }} title="Generated because the routing API offered fewer alternatives">
            generated
          </span>
        )}
        {differsFromBase && offsetHours > 0 && (
          <span className="dim" style={{ fontSize: 10 }}>
            changed at this offset
          </span>
        )}
      </header>

      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(4, 1fr)', gap: 8, marginBottom: 8 }}>
        <Stat label="Distance" value={formatMiles(route.road_miles)} />
        <Stat label="Drive" value={formatDuration(route.duration_s)} />
        <Stat label="Arrive" value={arrivalTime.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })} />
        <Stat label="Avg risk" value={formatAvgRisk(summary.avg_risk, precision)} />
      </div>

      <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap', marginBottom: 8 }}>
        <Miles label="No Travel" miles={summary.no_travel_miles} level="NO_TRAVEL" />
        <Miles label="Severe" miles={summary.severe_miles} level="SEVERE" />
        <Miles label="High" miles={summary.high_miles} level="HIGH" />
      </div>

      <RiskBar summary={summary} />

      {tally && selected && (
        <details style={{ marginTop: 8 }}>
          <summary className="dim" style={{ fontSize: 11, cursor: 'pointer' }}>
            Criterion tally
          </summary>
          <table style={{ width: '100%', fontSize: 11, marginTop: 4 }}>
            <tbody>
              {Object.entries(tally).map(([criterion, count]) => (
                <tr key={criterion}>
                  <td className="dim">{criterion.replace(/_/g, ' ')}</td>
                  <td className="mono" style={{ textAlign: 'right' }}>
                    {count} win{count === 1 ? '' : 's'}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </details>
      )}
    </article>
  );
}

function Stat({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <div className="stat-value">{value}</div>
      <div className="stat-label">{label}</div>
    </div>
  );
}

function Miles({ label, miles, level }: { label: string; miles: number; level: string }) {
  // Hide clean categories so the eye goes to what is actually wrong.
  if (miles <= 0.5) return null;
  return (
    <span className="risk-badge" data-level={level}>
      {Math.round(miles)} {label.toLowerCase()} mi
    </span>
  );
}

/** Proportional bar of the route by level -- the shape of the risk at a glance. */
function RiskBar({ summary }: { summary: RouteView['summary'] }) {
  const total = summary.total_miles || 1;
  const segments: Array<[string, number]> = [
    ['NO_TRAVEL', summary.no_travel_miles],
    ['SEVERE', summary.severe_miles],
    ['HIGH', summary.high_miles],
    ['MODERATE', summary.moderate_miles],
    ['LOW', summary.low_miles],
  ];
  return (
    <div
      style={{ display: 'flex', height: 6, borderRadius: 999, overflow: 'hidden', background: 'var(--bg)' }}
      role="img"
      aria-label={`Risk mix: ${segments
        .filter(([, v]) => v > 0)
        .map(([k, v]) => `${Math.round(v)} miles ${k.toLowerCase()}`)
        .join(', ')}`}
    >
      {segments.map(([level, miles]) =>
        miles > 0 ? (
          <div
            key={level}
            style={{
              width: `${(miles / total) * 100}%`,
              background: `var(--risk-${level.toLowerCase().replace('_', '-')})`,
            }}
            title={`${Math.round(miles)} mi ${level.toLowerCase()}`}
          />
        ) : null,
      )}
    </div>
  );
}

function CheckpointTable({
  route,
  offsetHours,
  hoveredId,
  onHover,
}: {
  route: RouteView | undefined;
  offsetHours: number;
  hoveredId: string | null;
  onHover: (id: string | null) => void;
}) {
  const analysis = useStore((s) => s.analysis);
  if (!route?.bundle) return null;

  const { routeId } = { routeId: route.route.id };
  const bundle = analysis.checkpoints.find((c) => c.route_id === routeId);
  if (!bundle) return null;

  return (
    <div style={{ marginTop: 'var(--space-4)' }}>
      <h3 style={{ fontSize: 12, marginBottom: 6 }}>
        Checkpoints <span className="dim">({bundle.points.length} @ every {analysis.meta.spacing_mi} mi)</span>
      </h3>
      <div style={{ maxHeight: 260, overflowY: 'auto', border: '1px solid var(--border)', borderRadius: 'var(--radius-sm)' }}>
        <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 12 }}>
          <thead style={{ position: 'sticky', top: 0, background: 'var(--bg-overlay)' }}>
            <tr>
              <Th>Mi</Th>
              <Th>ETA</Th>
              <Th>Wind</Th>
              <Th>Rain</Th>
              <Th>Snow</Th>
              <Th>Risk</Th>
            </tr>
          </thead>
          <tbody>
            {bundle.points.map((point, index) => {
              const id = `${routeId}:${index}`;
              const level = analysis.checkpoints; // placeholder to keep types honest
              void level;
              const name = point.risk.level;
              return (
                <tr
                  key={id}
                  onMouseEnter={() => onHover(id)}
                  onMouseLeave={() => onHover(null)}
                  style={{
                    background: hoveredId === id ? 'var(--bg-overlay)' : 'transparent',
                    cursor: 'default',
                  }}
                >
                  <Td mono>{point.distance_mi.toFixed(0)}</Td>
                  <Td mono>
                    {new Date(
                      new Date(point.eta).getTime() + offsetHours * 3_600_000,
                    ).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}
                  </Td>
                  <Td mono>{point.risk.wind.value === null ? '—' : point.risk.wind.value.toFixed(0)}</Td>
                  <Td mono>{point.risk.rain.value === null ? '—' : point.risk.rain.value.toFixed(2)}</Td>
                  <Td mono>{point.risk.snow.value === null ? '—' : point.risk.snow.value.toFixed(2)}</Td>
                  <Td>
                    <span className="risk-badge" data-level={name}>
                      {name.replace('_', ' ')}
                    </span>
                  </Td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </div>
  );
}

function Th({ children }: { children: React.ReactNode }) {
  return (
    <th
      style={{
        textAlign: 'left',
        padding: '4px 8px',
        fontSize: 10,
        textTransform: 'uppercase',
        letterSpacing: '0.05em',
        color: 'var(--text-dim)',
        borderBottom: '1px solid var(--border)',
      }}
    >
      {children}
    </th>
  );
}

function Td({ children, mono }: { children: React.ReactNode; mono?: boolean }) {
  return (
    <td
      className={mono ? 'mono' : undefined}
      style={{ padding: '3px 8px', borderBottom: '1px solid var(--bg)' }}
    >
      {children}
    </td>
  );
}
