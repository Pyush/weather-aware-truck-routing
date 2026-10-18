import { useEffect, useRef } from 'react';

import type { AppConfig } from '@/types/api';

interface MethodologyModalProps {
  config: AppConfig | null;
  onClose: () => void;
}

/**
 * Methodology and caveats.
 *
 * This panel exists because the honest limits of the data have to be visible to
 * the person relying on it (D14/D15). The three that matter most:
 *
 * - **Hourly weather.** 10-mile sampling is finer than the forecast grid, so
 *   denser sampling refines *where* risk is measured, not how precisely the
 *   weather is known.
 * - **Precipitation is a preceding-hour sum**, and snow is *depth* while rain is
 *   *liquid equivalent* -- they are not interchangeable.
 * - **This is not an official weather alert.** A driver still has to check.
 */
export function MethodologyModal({ config, onClose }: MethodologyModalProps) {
  const dialog = useRef<HTMLDialogElement | null>(null);

  useEffect(() => {
    const node = dialog.current;
    if (!node) return;
    if (!node.open) node.showModal();
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose();
    };
    node.addEventListener('keydown', onKey);
    return () => node.removeEventListener('keydown', onKey);
  }, [onClose]);

  return (
    <dialog
      ref={dialog}
      onClose={onClose}
      style={{
        maxWidth: 720,
        width: '92vw',
        padding: 0,
        border: '1px solid var(--border-strong)',
        borderRadius: 'var(--radius-lg)',
        background: 'var(--bg-raised)',
        color: 'var(--text)',
        boxShadow: 'var(--shadow-pop)',
      }}
    >
      <div style={{ padding: 'var(--space-5)', display: 'grid', gap: 'var(--space-4)' }}>
        <header style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'start' }}>
          <h2 style={{ fontSize: 16 }}>How this is calculated</h2>
          <button className="btn btn-ghost" type="button" onClick={onClose} aria-label="Close">
            ✕
          </button>
        </header>

        <Section title="Risk rules">
          {config ? (
            config.thresholds.map((table) => (
              <div key={table.variable} style={{ marginBottom: 12 }}>
                <strong style={{ textTransform: 'capitalize' }}>
                  {table.variable} <span className="dim">({table.unit})</span>
                </strong>
                <table style={{ width: '100%', marginTop: 4, borderCollapse: 'collapse', fontSize: 12 }}>
                  <thead>
                    <tr>
                      <Cell head>Range</Cell>
                      <Cell head>Rating</Cell>
                    </tr>
                  </thead>
                  <tbody>
                    {table.buckets.map((bucket) => (
                      <tr key={bucket.label}>
                        <Cell mono>{bucket.range}</Cell>
                        <Cell>{bucket.label}</Cell>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            ))
          ) : (
            <p className="dim">Loading…</p>
          )}
        </Section>

        {config && (
          <Section title="Load rules">
            <ul style={{ margin: 0, paddingLeft: 18, fontSize: 13, lineHeight: 1.7 }}>
              <li>
                Wind at or above {config.load_policy.no_travel_wind_mph} mph is No Travel for any
                load.
              </li>
              <li>
                Wind {config.load_policy.heavy_load_wind_mph}–{config.load_policy.no_travel_wind_mph} mph
                with more than {config.load_policy.heavy_load_threshold_lb.toLocaleString()} lb is No
                Travel.
              </li>
              <li>
                Wind {config.load_policy.extra_heavy_wind_mph}–{config.load_policy.heavy_load_wind_mph} mph
                with more than {config.load_policy.extra_heavy_threshold_lb.toLocaleString()} lb is at
                least Severe.
              </li>
            </ul>
            <p className="dim" style={{ fontSize: 12, marginBottom: 0 }}>
              Load comparisons are {config.load_policy.boundary_inclusive ? 'inclusive' : 'strict'}{' '}
              (exactly {config.load_policy.heavy_load_threshold_lb.toLocaleString()} lb does{' '}
              {config.load_policy.boundary_inclusive ? '' : 'not '}escalate).
            </p>
          </Section>
        )}

        {config && (
          <Section title="How a route is ranked">
            <ol style={{ margin: 0, paddingLeft: 18, fontSize: 13, lineHeight: 1.7 }}>
              <li>Fewest untravable miles</li>
              <li>Fewest Severe miles</li>
              <li>Fewest High miles</li>
              <li>
                Lowest average risk, compared to{' '}
                {config.ranking.avg_risk_precision} decimal place
                {config.ranking.avg_risk_precision === 1 ? '' : 's'} so rounding noise cannot decide
                the outcome
              </li>
              <li>Shortest travel time</li>
            </ol>
          </Section>
        )}

        {config && (
          <Section title="Timing assumptions">
            <ul style={{ margin: 0, paddingLeft: 18, fontSize: 13, lineHeight: 1.7 }}>
              <li>
                The routing service returns a driving-car estimate. Truck speed is modelled at{' '}
                {config.assumptions.truck_speed_factor}× that, clamped to{' '}
                {config.assumptions.truck_min_avg_mph}–{config.assumptions.truck_max_avg_mph} mph.
              </li>
              <li>
                Each checkpoint's weather is the forecast hour containing its arrival time (
                {config.assumptions.checkpoint_time_snap}).
              </li>
              <li>
                Moving the slider shifts the <em>departure</em> time, so every checkpoint is
                re-read at its later arrival hour.
              </li>
            </ul>
          </Section>
        )}

        <Section title="Limits worth knowing">
          <ul style={{ margin: 0, paddingLeft: 18, fontSize: 13, lineHeight: 1.7 }}>
            <li>
              <strong>Forecasts are hourly.</strong> Sampling every 10 miles is finer than the
              forecast grid, so denser sampling changes how precisely risk is located, not how well
              the weather is known.
            </li>
            <li>
              <strong>Rain and snow are different quantities.</strong> Rain is liquid equivalent;
              snow is accumulation depth, roughly seven times larger for the same storm. They are
              measured against their own thresholds and must not be compared directly.
            </li>
            <li>
              <strong>Precipitation values are the preceding hour's total</strong>, so a reading is
              at most an hour away from the moment it describes.
            </li>
            <li>
              <strong>Not a substitute for official weather alerts</strong>, road closures, or a
              driver's judgement. Treat No Travel as a hard stop and re-check conditions before
              leaving.
            </li>
          </ul>
        </Section>

        <Section title="Data sources">
          <p className="muted" style={{ fontSize: 13, margin: 0 }}>
            Routing and geocoding by OpenStreetMap via OSRM and Open-Meteo; hourly forecasts by
            Open-Meteo; base map tiles by OpenFreeMap. All keyless and free to use with attribution.
          </p>
        </Section>
      </div>
    </dialog>
  );
}

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <section>
      <h3 style={{ fontSize: 13, marginBottom: 6, color: 'var(--accent)' }}>{title}</h3>
      {children}
    </section>
  );
}

function Cell({
  children,
  head,
  mono,
}: {
  children: React.ReactNode;
  head?: boolean;
  mono?: boolean;
}) {
  return (
    <td
      className={mono ? 'mono' : undefined}
      style={{
        padding: head ? '4px 8px' : '3px 8px',
        fontSize: 12,
        color: head ? 'var(--text-dim)' : 'var(--text)',
        borderBottom: '1px solid var(--bg)',
        fontWeight: head ? 600 : 400,
      }}
    >
      {children}
    </td>
  );
}
