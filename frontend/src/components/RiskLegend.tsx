import type { AppConfig } from '@/types/api';

/**
 * Risk legend.
 *
 * Rendered entirely from `/config/`, including the colours and the ordering, so
 * the legend cannot disagree with the map (D12).
 */
export function RiskLegend({ config }: { config: AppConfig | null }) {
  if (!config) {
    return <div className="dim" style={{ fontSize: 11 }}>loading rules…</div>;
  }

  return (
    <details style={{ position: 'relative' }}>
      <summary
        className="btn btn-ghost"
        style={{ fontSize: 12, cursor: 'pointer', listStyle: 'none' }}
      >
        Legend
      </summary>
      <div
        className="card"
        style={{
          position: 'absolute',
          right: 0,
          top: 'calc(100% + 6px)',
          zIndex: 20,
          padding: 'var(--space-3)',
          width: 300,
          boxShadow: 'var(--shadow-pop)',
        }}
      >
        {config.levels.map((level) => (
          <div key={level.value} style={{ marginBottom: 10 }}>
            <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
              <span
                aria-hidden
                style={{
                  width: 12,
                  height: 12,
                  borderRadius: 3,
                  background: level.hex,
                  flexShrink: 0,
                }}
              />
              <strong style={{ fontSize: 12 }}>{level.label}</strong>
              <span className="dim mono" style={{ fontSize: 10, marginLeft: 'auto' }}>
                {level.symbol}
              </span>
            </div>
            <p className="dim" style={{ margin: '2px 0 0 20px', fontSize: 11 }}>
              {level.description}
            </p>
          </div>
        ))}

        <div style={{ borderTop: '1px solid var(--border)', paddingTop: 8, marginTop: 4 }}>
          <div className="stat-label" style={{ marginBottom: 4 }}>
            Thresholds
          </div>
          {config.thresholds.map((table) => (
            <div key={table.variable} style={{ marginBottom: 6 }}>
              <span style={{ fontSize: 11, textTransform: 'capitalize' }}>{table.variable}</span>
              <span className="dim" style={{ fontSize: 10 }}>
                {' '}
                ({table.unit})
              </span>
              <div className="mono dim" style={{ fontSize: 10 }}>
                {table.buckets.map((b) => b.range).join(' · ')}
              </div>
            </div>
          ))}
        </div>
      </div>
    </details>
  );
}
