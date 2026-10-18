/**
 * First-run empty state (FE-09).
 *
 * Before any analysis the panel is just a form, which gives a first-time user
 * nothing to act on. This states what the app does, offers two corridors worth
 * trying, and is explicit about the limits -- so nobody reads a green route as
 * a safety guarantee (D14/D15).
 */

import { useStore } from '@/store/useStore';

export interface ExampleTrip {
  id: string;
  label: string;
  hint: string;
  origin: string;
  destination: string;
  loadLb: number;
}

/**
 * Two corridors that behave differently: a long-haul run, and a longer one
 * through a mountain pass. DOC-12 wants one clear and one storm, but that
 * depends on live weather, so these are fixed by geography instead.
 */
const EXAMPLE_TRIPS: ExampleTrip[] = [
  {
    id: 'chi-det',
    label: 'Chicago → Detroit',
    hint: '280 mi · about 5 h · lake-effect snow possible',
    origin: 'Chicago, IL',
    destination: 'Detroit, MI',
    loadLb: 40_000,
  },
  {
    id: 'den-salt',
    label: 'Denver → Salt Lake City',
    hint: '520 mi · mountain pass · high winds',
    origin: 'Denver, CO',
    destination: 'Salt Lake City, UT',
    loadLb: 45_000,
  },
];

export function EmptyState({ onStart }: { onStart: (trip: ExampleTrip) => void }) {
  const setField = useStore((s) => s.setField);

  return (
    <section className="card" style={{ padding: 'var(--space-4)' }}>
      <h2 style={{ fontSize: 13, marginBottom: 6 }}>How this works</h2>
      <p className="muted" style={{ fontSize: 12, margin: '0 0 10px' }}>
        Three routes are compared by road miles at each risk level, worst first. The slider shifts
        the departure time and re-reads the hourly forecast at each checkpoint, without asking the
        server again.
      </p>

      <h3 style={{ fontSize: 12, marginBottom: 6 }}>Try one</h3>
      <div style={{ display: 'grid', gap: 6, marginBottom: 10 }}>
        {EXAMPLE_TRIPS.map((trip) => (
          <button
            key={trip.id}
            type="button"
            className="btn"
            style={{ display: 'block', textAlign: 'left', width: '100%' }}
            onClick={() => {
              setField('origin', trip.origin);
              setField('destination', trip.destination);
              setField('loadLb', trip.loadLb);
              onStart(trip);
            }}
          >
            <strong style={{ display: 'block', fontSize: 12 }}>{trip.label}</strong>
            <span className="dim" style={{ fontSize: 10 }}>
              {trip.hint}
            </span>
          </button>
        ))}
      </div>

      <p className="dim" style={{ fontSize: 11, margin: 0 }}>
        Forecasts are hourly, so a reading can be up to an hour from the moment it describes. This
        is not a substitute for official weather alerts or a driver&rsquo;s judgement.
      </p>
    </section>
  );
}