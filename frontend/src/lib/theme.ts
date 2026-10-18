/**
 * Install the server's risk palette onto CSS custom properties.
 *
 * Called once `/config/` resolves. Keeping the colours server-side is what stops
 * the legend, the map layers and the table badges from disagreeing (D12).
 */

import type { ExpressionSpecification } from 'maplibre-gl';

import { RiskLevel, type AppConfig } from '@/types/api';

const LEVEL_VARS: Record<RiskLevel, string> = {
  [RiskLevel.LOW]: '--risk-low',
  [RiskLevel.MODERATE]: '--risk-moderate',
  [RiskLevel.HIGH]: '--risk-high',
  [RiskLevel.SEVERE]: '--risk-severe',
  [RiskLevel.NO_TRAVEL]: '--risk-no-travel',
};

export function applyRiskColors(config: AppConfig): void {
  const root = document.documentElement;
  for (const meta of config.levels) {
    const variable = LEVEL_VARS[meta.value as RiskLevel];
    if (variable) root.style.setProperty(variable, meta.hex);
  }
}

/**
 * A MapLibre `match` expression mapping a numeric level to its colour.
 *
 * The colours come from `/config/`, so the map still holds none of its own -- but
 * they must be *literal hex*, not `var(--risk-low)`. MapLibre parses paint
 * values itself and hands them to WebGL, where a CSS custom property is an
 * unparseable string ("Could not parse color from value 'var(--risk-low)'").
 *
 * @param config Loaded rule configuration; falls back to the CSS defaults.
 */
export function levelColorExpression(config: AppConfig | null): ExpressionSpecification {
  const hex = (level: RiskLevel): string =>
    config?.levels.find((l) => l.value === level)?.hex ?? FALLBACK_HEX[level];

  return [
    'match',
    ['get', 'level'],
    1, hex(RiskLevel.LOW),
    2, hex(RiskLevel.MODERATE),
    3, hex(RiskLevel.HIGH),
    4, hex(RiskLevel.SEVERE),
    5, hex(RiskLevel.NO_TRAVEL),
    '#64748b',
  ];
}

/** Used only until `/config/` resolves. Mirrors the defaults in index.css. */
const FALLBACK_HEX: Record<RiskLevel, string> = {
  [RiskLevel.LOW]: '#16a34a',
  [RiskLevel.MODERATE]: '#ca8a04',
  [RiskLevel.HIGH]: '#ea580c',
  [RiskLevel.SEVERE]: '#dc2626',
  [RiskLevel.NO_TRAVEL]: '#6d28d9',
};
