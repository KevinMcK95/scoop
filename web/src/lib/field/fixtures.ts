// Test-only: fixtures shared with the Python tests (scoop/tests/fixtures).
import { readFileSync } from 'node:fs';
import type { Field, Row, Clause } from './types';

const read = (name: string) => JSON.parse(readFileSync(new URL(`../../../../tests/fixtures/${name}`, import.meta.url), 'utf8'));

export const miniField = (): Field => read('mini_field.json');
// Every layer on the map, for tests about drawing and counting rather than defaults.
export const miniFieldAllOn = (): Field => {
  const field = miniField();
  field.layers = field.layers.map((layer) => ({ ...layer, visible: true, added: true }));
  return field;
};
export const filterCases = (): { rows: Row[]; cases: { filter: Clause[]; ids: string[] }[] } => read('filter_cases.json');
