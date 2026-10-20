// Same semantics as scoop/fields.py:matches; both run tests/fixtures/filter_cases.json.
import type { Clause, Row } from './types';

const isNumber = (v: unknown): v is number => typeof v === 'number';
const RAD = Math.PI / 180;

/** Great-circle separation in degrees (haversine), as scoop/spatial.py. */
export function separation(ra1: number, dec1: number, ra2: number, dec2: number): number {
  const h = Math.sin(((dec2 - dec1) * RAD) / 2) ** 2 +
    Math.cos(dec1 * RAD) * Math.cos(dec2 * RAD) * Math.sin(((ra2 - ra1) * RAD) / 2) ** 2;
  return (2 * Math.asin(Math.min(1, Math.sqrt(h)))) / RAD;
}
const equal = (a: unknown, b: unknown) => a === b;

/** Point-in-polygon on the sky, tested in a tangent plane about the point (as scoop/spatial.py). */
export function polygonContains(points: [number, number][], ra: number, dec: number): boolean {
  const [a0, d0] = [ra * RAD, dec * RAD];
  const xy: [number, number][] = [];
  for (const [r, d] of points) {
    const [a, dd] = [r * RAD, d * RAD];
    const cos = Math.sin(d0) * Math.sin(dd) + Math.cos(d0) * Math.cos(dd) * Math.cos(a - a0);
    if (cos <= 0) return false;
    xy.push([Math.cos(dd) * Math.sin(a - a0) / cos,
      (Math.cos(d0) * Math.sin(dd) - Math.sin(d0) * Math.cos(dd) * Math.cos(a - a0)) / cos]);
  }
  let inside = false;
  for (let i = 0; i < xy.length; i++) {
    const [x1, y1] = xy[i];
    const [x2, y2] = xy[(i + 1) % xy.length];
    if ((y1 > 0) !== (y2 > 0) && x1 + (0 - y1) * (x2 - x1) / (y2 - y1) > 0) inside = !inside;
  }
  return inside;
}

function clause(row: Row, c: Clause): boolean {
  const value = row[c.column];
  const target = c.value;
  switch (c.op) {
    case 'inside': {
      const poly = (target as unknown as { polygon: [number, number][] }).polygon;
      return isNumber(row.ra) && isNumber(row.dec) && polygonContains(poly, row.ra, row.dec);
    }
    case 'within': {
      const w = target as unknown as { ra: number; dec: number; radius: number };
      return isNumber(row.ra) && isNumber(row.dec) && separation(row.ra, row.dec, w.ra, w.dec) <= w.radius;
    }
    case 'exists': return (value !== null && value !== undefined && value !== '') === ('value' in c ? Boolean(target) : true);
    case '==': return equal(value, target);
    case '!=': return !equal(value, target);
    case 'in': return Array.isArray(target) && target.some((item) => equal(value, item));
  }
  // Ordered comparisons only between two numbers or two strings; missing never matches.
  const comparable = (isNumber(value) && isNumber(target)) || (typeof value === 'string' && typeof target === 'string');
  if (!comparable) return false;
  const [a, b] = [value as number | string, target as number | string];
  switch (c.op) {
    case '<': return a < b;
    case '<=': return a <= b;
    case '>': return a > b;
    case '>=': return a >= b;
  }
  return false;
}

export function matches(row: Row, filter: Clause[]): boolean {
  return filter.every((c) => clause(row, c));
}
