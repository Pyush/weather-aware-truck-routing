/**
 * Checkpoint table behaviour (FE-18, FE-20).
 *
 * The table is the reader's view of the same data the map draws. When the two
 * disagree the app tells a driver two different things about the same mile of
 * road, which is worse than showing nothing.
 */

import { afterEach, describe, expect, it } from 'vitest';
import { act, cleanup, fireEvent, render, screen, within } from '@testing-library/react';

import { RoutePanel } from '@/components/RoutePanel';
import { useStore } from '@/store/useStore';
import { levelName, riskAtOffset } from '@/lib/selectors';
import contract from '@/types/contract.json';
import type { Analysis } from '@/types/api';

const example = contract.example.response as unknown as Analysis;

function seed(offsetHours = 0): void {
  const store = useStore.getState();
  store.beginStream();
  for (const route of example.routes) store.applyEvent({ type: 'route', ...route } as never);
  for (const bundle of example.checkpoints) store.applyEvent({ type: 'checkpoints', ...bundle } as never);
  for (const route of example.routes) {
    store.applyEvent({ type: 'summary', route_id: route.id, ...route.summary! } as never);
  }
  store.applyEvent({ type: 'recommendation', ...example.recommendation } as never);
  store.finishStream();
  store.selectRoute(example.routes[0].id);
  useStore.setState({ offsetHours });
}

afterEach(() => {
  cleanup();
  useStore.getState().reset();
});

/** The table body rows, as visible text. */
function checkpointTable() {
  return screen.getByRole('table', { name: 'Checkpoints' });
}

function rows(): string[][] {
  const table = checkpointTable();
  const body = within(table).getAllByRole('row').slice(1);
  return body.map((row) => within(row).getAllByRole('cell').map((cell) => cell.textContent ?? ''));
}

describe('checkpoint table', () => {
  it('shows the level at the selected departure offset, not the base level', () => {
    render(<RoutePanel config={null} />);
    act(() => seed(0));

    const bundle = example.checkpoints[0];
    const atZero = rows();
    expect(atZero[0][5]).toBe(levelName(riskAtOffset(bundle.points[0], 0)).replace('_', ' '));

    // Regression: the table used `point.risk.level`, which is fixed at the
    // planned departure, so it never moved while the map and slider did.
    act(() => useStore.setState({ offsetHours: 24 }));
    const at24 = rows();
    expect(at24[0][5]).toBe(levelName(riskAtOffset(bundle.points[0], 24)).replace('_', ' '));
    expect(at24[0][5]).not.toBe(bundle.points[0].risk.level.replace('_', ' '));
  });

  it('names the dominant factor so a level is attributable', () => {
    render(<RoutePanel config={null} />);
    act(() => seed(0));
    expect(rows()[0][6]).toBe(example.checkpoints[0].points[0].risk.dominant_factor);
  });

  it('sorts by the clicked column and reports the direction to assistive tech', () => {
    render(<RoutePanel config={null} />);
    act(() => seed(0));

    // Mile is the default sort, already ascending.
    expect(screen.getByRole('columnheader', { name: /^Mi/ })).toHaveAttribute('aria-sort', 'ascending');

    const header = screen.getByRole('button', { name: /^Mi/ });
    fireEvent.click(header);
    expect(screen.getByRole('columnheader', { name: /^Mi/ })).toHaveAttribute('aria-sort', 'descending');
    // Descending puts the furthest checkpoint first.
    expect(rows()[0][0]).toBe(example.checkpoints[0].points.at(-1)!.distance_mi.toFixed(0));

    fireEvent.click(header);
    expect(screen.getByRole('columnheader', { name: /^Mi/ })).toHaveAttribute('aria-sort', 'ascending');
    expect(rows()[0][0]).toBe(example.checkpoints[0].points[0].distance_mi.toFixed(0));
  });

  it('states the active spacing and the offset in its heading', () => {
    render(<RoutePanel config={null} />);
    act(() => seed(24));
    const heading = screen.getByRole('heading', { name: /Checkpoints/ });
    expect(heading.textContent).toContain(`every ${example.meta.spacing_mi} mi`);
    expect(heading.textContent).toContain('shifted +24 h');
  });

  it('highlights the hovered row and syncs it back to the store', () => {
    render(<RoutePanel config={null} />);
    act(() => seed(0));

    const table = checkpointTable();
    const firstRow = within(table).getAllByRole('row')[1];
    fireEvent.mouseEnter(firstRow);
    expect(useStore.getState().hoveredCheckpointId).toBe(`${example.routes[0].id}:0`);

    fireEvent.mouseLeave(firstRow);
    expect(useStore.getState().hoveredCheckpointId).toBeNull();

    // The other direction: hovering the map sets the same id, and the row
    // must follow (FE-18). Without this the two halves silently diverge.
    act(() => useStore.getState().hoverCheckpoint(`${example.routes[0].id}:2`));
    expect(within(checkpointTable()).getAllByRole('row')[3]).toHaveStyle({
      background: 'var(--bg-overlay)',
    });
  });
});