import { useMemo, useState } from 'react';

import {
  etaAtOffset,
  formatAvgRisk,
  formatDateTime,
  formatDuration,
  formatMiles,
  levelName,
  riskAtOffset,
  routeViews,
  shiftedDeparture,
  type RouteView,
} from '@/lib/selectors';
import { useStore } from '@/store/useStore';
import type { AppConfig, Checkpoint } from '@/types/api';

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
  const [sort, setSort] = useState<{ column: Column; direction: 1 | -1 }>({
    column: 'distance',
    direction: 1,
  });
  const [scrollTop, setScrollTop] = useState(0);
  if (!route?.bundle) return null;

  const routeId = route.route.id;
  const bundle = analysis.checkpoints.find((c) => c.route_id === routeId);
  if (!bundle) return null;

  // The level is read at the departure offset, not from `point.risk`, which is
  // the level at the *planned* departure. Reading that base value made the
  // table contradict the map and the slider, which both recolour by offset.
  const rows = bundle.points.map((point, index) => ({
    id: `${routeId}:${index}`,
    point,
    level: riskAtOffset(point, offsetHours),
    eta: etaAtOffset(point, offsetHours),
  }));

  const sorted = [...rows].sort((a, b) => {
    const key = sort.column;
    const left = valueFor(a, key);
    const right = valueFor(b, key);
    const order = typeof left === 'number' && typeof right === 'number' ? left - right : String(left).localeCompare(String(right));
    return order * sort.direction;
  });

  return (
    <div style={{ marginTop: 'var(--space-4)' }}>
      <h3 style={{ fontSize: 12, marginBottom: 6 }}>
        Checkpoints{' '}
        <span className="dim">
          ({bundle.points.length} @ every {analysis.meta.spacing_mi} mi
          {offsetHours > 0 ? ` · shifted +${offsetHours} h` : ''})
        </span>
      </h3>
      <div
        style={{
          maxHeight: 260,
          overflowY: 'auto',
          border: '1px solid var(--border)',
          borderRadius: 'var(--radius-sm)',
        }}
        onScroll={(event) => {
          setScrollTop(event.currentTarget.scrollTop);
        }}
      >
        <table
          aria-label="Checkpoints"
          style={{ width: '100%', borderCollapse: 'collapse', fontSize: 12 }}
        >
          <thead style={{ position: 'sticky', top: 0, background: 'var(--bg-overlay)', zIndex: 1 }}>
            <tr>
              <Th
                column="distance"
                sort={sort}
                onSort={setSort}
              >
                Mi
              </Th>
              <Th
                column="eta"
                sort={sort}
                onSort={setSort}
              >
                ETA
              </Th>
              <Th
                column="wind"
                sort={sort}
                onSort={setSort}
              >
                Wind
              </Th>
              <Th
                column="rain"
                sort={sort}
                onSort={setSort}
              >
                Rain
              </Th>
              <Th
                column="snow"
                sort={sort}
                onSort={setSort}
              >
                Snow
              </Th>
              <Th
                column="level"
                sort={sort}
                onSort={setSort}
              >
                Risk
              </Th>
              <Th>Driver</Th>
            </tr>
          </thead>
          <tbody>
            {visibleRows(sorted, scrollTop).map((row) => (
              <tr
                key={row.id}
                tabIndex={0}
                onMouseEnter={() => onHover(row.id)}
                onMouseLeave={() => onHover(null)}
                onFocus={() => onHover(row.id)}
                onBlur={() => onHover(null)}
                style={{
                  background: hoveredId === row.id ? 'var(--bg-overlay)' : 'transparent',
                }}
              >
                <Td mono>{row.point.distance_mi.toFixed(0)}</Td>
                <Td mono>
                  {row.eta.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}
                </Td>
                <Td mono>{fmt(row.point.risk.wind.value, 0)}</Td>
                <Td mono>{fmt(row.point.risk.rain.value, 2)}</Td>
                <Td mono>{fmt(row.point.risk.snow.value, 2)}</Td>
                <Td>
                  <span className="risk-badge" data-level={levelName(row.level)}>
                    {levelName(row.level).replace('_', ' ')}
                  </span>
                </Td>
                <Td dim>{row.point.risk.dominant_factor}</Td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

const ROW_HEIGHT = 24;
/** Rows rendered beyond the viewport, so scrolling does not reveal blanks. */
const OVERSCAN = 8;
const TABLE_HEIGHT = 260;
/**
 * Row count above which the table windows its rows (FE-20).
 *
 * A 1,000-mile route at 10-mile spacing is ~100 checkpoints and each row is a
 * handful of elements, so rendering all of them is cheap. The windowing is kept
 * deliberately simple and is skipped entirely for small tables: a hand-rolled
 * scheme is easy to get subtly wrong (blank rows, lost focus, broken sticky
 * header) and the common case does not justify that risk.
 */
const VIRTUALISE_ABOVE = 200;

function visibleRows<T>(rows: T[], scrollTop: number): T[] {
  if (rows.length <= VIRTUALISE_ABOVE) return rows;
  const first = Math.max(0, Math.floor(scrollTop / ROW_HEIGHT) - OVERSCAN);
  const count = Math.ceil(TABLE_HEIGHT / ROW_HEIGHT) + OVERSCAN * 2;
  return rows.slice(first, first + count);
}

type Column = 'distance' | 'eta' | 'wind' | 'rain' | 'snow' | 'level';

function valueFor(row: { point: Checkpoint; level: number; eta: Date }, column: Column): number {
  switch (column) {
    case 'distance':
      return row.point.distance_mi;
    case 'eta':
      return row.eta.getTime();
    case 'wind':
      return row.point.risk.wind.value ?? -1;
    case 'rain':
      return row.point.risk.rain.value ?? -1;
    case 'snow':
      return row.point.risk.snow.value ?? -1;
    case 'level':
      return row.level;
    default:
      return 0;
  }
}

function fmt(value: number | null, digits: number): string {
  return value === null ? '—' : value.toFixed(digits);
}

function Th({
  children,
  column,
  sort,
  onSort,
}: {
  children: React.ReactNode;
  column?: Column;
  sort?: { column: Column; direction: 1 | -1 };
  onSort?: (next: { column: Column; direction: 1 | -1 }) => void;
}) {
  const active = column && sort?.column === column;
  return (
    <th
      scope="col"
      aria-sort={active ? (sort!.direction === 1 ? 'ascending' : 'descending') : column ? 'none' : undefined}
      style={{
        textAlign: 'left',
        padding: '4px 8px',
        fontSize: 10,
        textTransform: 'uppercase',
        letterSpacing: '0.05em',
        color: active ? 'var(--text)' : 'var(--text-dim)',
        borderBottom: '1px solid var(--border)',
      }}
    >
      {column && onSort ? (
        <button
          type="button"
          className="btn-ghost"
          style={{ background: 'none', border: 'none', padding: 0, cursor: 'pointer', font: 'inherit' }}
          onClick={() =>
            onSort({ column, direction: active && sort!.direction === 1 ? -1 : 1 })
          }
        >
          {children}
          {active ? <span aria-hidden="true">{sort!.direction === 1 ? ' ▲' : ' ▼'}</span> : null}
        </button>
      ) : (
        children
      )}
    </th>
  );
}

function Td({ children, mono, dim }: { children: React.ReactNode; mono?: boolean; dim?: boolean }) {
  return (
    <td
      className={mono ? 'mono' : dim ? 'dim' : undefined}
      style={{ padding: '3px 8px', borderBottom: '1px solid var(--bg)' }}
    >
      {children}
    </td>
  );
}
