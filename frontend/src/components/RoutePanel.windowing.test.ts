/**
 * Checkpoint-table windowing (FE-20).
 *
 * The bug this guards: a table that renders only its visible slice is only as
 * tall as its window, so the scrollbar bottoms out after one screen and every
 * row beyond it is unreachable -- the table looks like it simply ends. The
 * spacer rows restore the full height.
 */

import { describe, expect, it } from 'vitest';

import { ROW_HEIGHT, TABLE_HEIGHT, OVERSCAN, visibleRows } from '@/components/RoutePanel';

interface Row {
  id: string;
}

function makeRows(count: number): Row[] {
  return Array.from({ length: count }, (_, i) => ({ id: `r${i}` }));
}

describe('checkpoint table windowing', () => {
  it('renders everything without a spacer for a normal-length route', () => {
    // A 1,000-mile route at 10-mile spacing is ~100 checkpoints. That must be
    // cheap to render whole, and must not be windowed at all.
    const rows = makeRows(100);
    const result = visibleRows(rows, 0);
    expect(result.visible).toHaveLength(100);
    expect(result.before).toBe(0);
    expect(result.after).toBe(0);
  });

  it('keeps the full scroll height when windowed', () => {
    // 500 rows at 24px is 12,000px of table. Without the spacer the body would
    // be only ~27 rows tall and the scrollbar would stop there.
    const rows = makeRows(500);
    const atTop = visibleRows(rows, 0);

    expect(atTop.before).toBe(0);
    expect(atTop.visible.length).toBeLessThan(rows.length);
    // before + visible + after must account for every row, or the scrollbar lies.
    expect(atTop.before + atTop.visible.length + atTop.after).toBe(rows.length);
    expect(atTop.after).toBeGreaterThan(0);
  });

  it('moves the window as the user scrolls', () => {
    const rows = makeRows(500);
    const middle = visibleRows(rows, 5000);

    // The window has scrolled: rows are skipped at the top, and the count of
    // rendered rows is far below the total.
    expect(middle.before).toBeGreaterThan(0);
    expect(middle.before + middle.visible.length + middle.after).toBe(rows.length);
    // The visible slice starts where the spacer says it does.
    expect(middle.visible[0].id).toBe(`r${middle.before}`);
  });

  it('never renders rows past the end', () => {
    const rows = makeRows(500);
    const atBottom = visibleRows(rows, rows.length * ROW_HEIGHT);

    expect(atBottom.after).toBe(0);
    expect(atBottom.visible.at(-1)?.id).toBe(`r${rows.length - 1}`);
    expect(atBottom.before + atBottom.visible.length).toBe(rows.length);
  });

  it('stays within the overscan budget while scrolling', () => {
    const rows = makeRows(500);
    for (let scroll = 0; scroll <= rows.length * ROW_HEIGHT; scroll += ROW_HEIGHT * 7) {
      const result = visibleRows(rows, scroll);
      const expected = Math.ceil(TABLE_HEIGHT / ROW_HEIGHT) + OVERSCAN * 2;
      expect(result.visible.length).toBeLessThanOrEqual(expected);
    }
  });
});