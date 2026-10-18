import { useState } from 'react';

import { useStore } from '@/store/useStore';

/**
 * Module-level, so the selector returns a *stable* reference.
 *
 * `useStore((s) => s.error?.fieldErrors ?? {})` allocates a fresh `{}` on every
 * call whenever there is no error. Zustand compares snapshots with `Object.is`,
 * so that reads as a change on every render and drives an infinite update loop
 * ("The result of getSnapshot should be cached"). One shared constant removes
 * the allocation entirely.
 */
const NO_FIELD_ERRORS: Record<string, string[]> = {};

interface TripFormProps {
  onSubmit: () => void;
}

export function TripForm({ onSubmit }: TripFormProps) {
  const origin = useStore((s) => s.origin);
  const destination = useStore((s) => s.destination);
  const departure = useStore((s) => s.departure);
  const loadLb = useStore((s) => s.loadLb);
  const spacingMi = useStore((s) => s.spacingMi);
  const phase = useStore((s) => s.phase);
  const fieldErrors = useStore((s) => s.error?.fieldErrors ?? NO_FIELD_ERRORS);
  const setField = useStore((s) => s.setField);

  const [showAdvanced, setShowAdvanced] = useState(false);
  const [localErrors, setLocalErrors] = useState<Record<string, string>>({});

  const spacings = [10, 25, 50];

  function validate(): Record<string, string> {
    const errors: Record<string, string> = {};
    if (!origin.trim()) errors.origin = 'Enter a starting point.';
    if (!destination.trim()) errors.destination = 'Enter a destination.';
    if (origin.trim() && destination.trim() && origin.trim() === destination.trim()) {
      errors.destination = 'Origin and destination are the same.';
    }
    if (!departure) errors.departure = 'Choose a departure time.';
    else if (new Date(departure).getTime() < Date.now() - 3_600_000) {
      errors.departure = 'Departure is in the past.';
    }
    if (!Number.isFinite(loadLb) || loadLb < 0) errors.loadLb = 'Load must be zero or more.';
    else if (loadLb > 80_000) errors.loadLb = 'Load must be 80,000 lb or less.';
    return errors;
  }

  function handleSubmit(event: React.FormEvent) {
    event.preventDefault();
    const errors = validate();
    setLocalErrors(errors);
    if (Object.keys(errors).length > 0) return;
    onSubmit();
  }

  const errors = { ...localErrors, ...fieldErrors };
  const busy = phase === 'streaming';

  return (
    <form className="card" style={{ padding: 'var(--space-4)' }} onSubmit={handleSubmit}>
      <h2 style={{ fontSize: 13, marginBottom: 'var(--space-3)' }}>Plan a trip</h2>

      <div style={{ display: 'grid', gap: 'var(--space-3)' }}>
        <div className="field">
          <label htmlFor="origin">Origin</label>
          <input
            id="origin"
            className="input"
            value={origin}
            onChange={(e) => setField('origin', e.target.value)}
            aria-invalid={Boolean(errors.origin)}
            aria-describedby={errors.origin ? 'origin-error' : undefined}
            placeholder="Chicago, IL"
            autoComplete="off"
          />
          {errors.origin && (
            <span className="field-error" id="origin-error">
              {errors.origin}
            </span>
          )}
        </div>

        <div className="field">
          <label htmlFor="destination">Destination</label>
          <input
            id="destination"
            className="input"
            value={destination}
            onChange={(e) => setField('destination', e.target.value)}
            aria-invalid={Boolean(errors.destination)}
            aria-describedby={errors.destination ? 'destination-error' : undefined}
            placeholder="Detroit, MI"
            autoComplete="off"
          />
          {errors.destination && (
            <span className="field-error" id="destination-error">
              {errors.destination}
            </span>
          )}
        </div>

        <div style={{ display: 'grid', gridTemplateColumns: 'minmax(0, 3fr) minmax(0, 2fr)', gap: 'var(--space-3)' }}>
          <div className="field">
            <label htmlFor="departure">Departure</label>
            <input
              id="departure"
              type="datetime-local"
              className="input"
              value={departure}
              onChange={(e) => setField('departure', e.target.value)}
              aria-invalid={Boolean(errors.departure)}
            />
            {errors.departure && <span className="field-error">{errors.departure}</span>}
          </div>

          <div className="field">
            <label htmlFor="loadLb">Load (lb)</label>
            <input
              id="loadLb"
              type="number"
              className="input"
              value={loadLb}
              min={0}
              max={80_000}
              step={500}
              onChange={(e) => setField('loadLb', Number(e.target.value))}
              aria-invalid={Boolean(errors.loadLb)}
              // A number input sizes to its widest legal value, which overflows a
              // two-column grid and clips the real number to "5.".
              style={{ width: '100%', minWidth: 0 }}
            />
            {errors.loadLb && <span className="field-error">{errors.loadLb}</span>}
          </div>
        </div>

        <div className="field">
          <label htmlFor="spacing">Weather sampling</label>
          <select
            id="spacing"
            className="input"
            value={spacingMi}
            onChange={(e) => setField('spacingMi', Number(e.target.value))}
          >
            {spacings.map((value) => (
              <option key={value} value={value}>
                Every {value} miles
                {value === 25 ? ' (default)' : ''}
              </option>
            ))}
          </select>
          <span className="dim" style={{ fontSize: 11 }}>
            More checkpoints means more precise risk, and a slower forecast.
          </span>
        </div>

        <details open={showAdvanced} onToggle={(e) => setShowAdvanced(e.currentTarget.open)}>
          <summary className="dim" style={{ fontSize: 12, cursor: 'pointer' }}>
            Advanced truck assumptions
          </summary>
          <div style={{ display: 'grid', gap: 'var(--space-2)', marginTop: 'var(--space-2)' }}>
            <AssumptionField
              label="Speed factor"
              hint="Multiplier on the routing service's car speed."
              value={useStore.getState().truckSpeedFactor}
              step={0.05}
              onChange={(v) => setField('truckSpeedFactor', v)}
            />
            <AssumptionField
              label="Min average mph"
              value={useStore.getState().truckMinAvgMph}
              step={1}
              onChange={(v) => setField('truckMinAvgMph', v)}
            />
            <AssumptionField
              label="Max average mph"
              value={useStore.getState().truckMaxAvgMph}
              step={1}
              onChange={(v) => setField('truckMaxAvgMph', v)}
            />
          </div>
        </details>

        <button className="btn btn-primary" type="submit" disabled={busy}>
          {busy ? 'Analysing…' : 'Find safest routes'}
        </button>
      </div>
    </form>
  );
}

function AssumptionField({
  label,
  hint,
  value,
  step,
  onChange,
}: {
  label: string;
  hint?: string;
  value: number;
  step: number;
  onChange: (value: number) => void;
}) {
  const id = `assumption-${label.replace(/\s+/g, '-').toLowerCase()}`;
  return (
    <div className="field">
      <label htmlFor={id}>{label}</label>
      <input
        id={id}
        type="number"
        className="input"
        value={value}
        step={step}
        onChange={(e) => onChange(Number(e.target.value))}
      />
      {hint && <span className="dim" style={{ fontSize: 11 }}>{hint}</span>}
    </div>
  );
}
