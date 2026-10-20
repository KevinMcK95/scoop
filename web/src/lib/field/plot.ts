// Data for the linked plot panel: the same layers, colors and rows as the map.
import { layerRows, rowsOf } from './actions';
import type { Clause, Field, MapState, PlotSpec, Preset } from './types';

export interface Axis { min: number; max: number; ticks: number[] }
export interface PlotPoint { id: string; x: number; y: number; color: string; r: number }

const finite = (v: unknown): v is number => typeof v === 'number' && Number.isFinite(v);

export function niceTicks(min: number, max: number, count = 5): number[] {
  if (!(max > min)) return [min];
  const raw = (max - min) / count;
  const power = 10 ** Math.floor(Math.log10(raw));
  const step = [1, 2, 2.5, 5, 10].map((m) => m * power).find((s) => s >= raw)!;
  const ticks: number[] = [];
  for (let t = Math.ceil(min / step) * step; t <= max + step * 1e-9; t += step) ticks.push(+t.toPrecision(12));
  return ticks;
}

function quantile(sorted: number[], q: number): number {
  const pos = (sorted.length - 1) * q;
  const lo = Math.floor(pos);
  return sorted[lo] + (sorted[Math.ceil(pos)] - sorted[lo]) * (pos - lo);
}

/** Range covering the 1st–99th percentiles plus 5% padding, so outliers don't flatten the plot. */
export function axis(values: number[]): Axis {
  const sorted = values.filter(finite).sort((a, b) => a - b);
  if (!sorted.length) return { min: 0, max: 1, ticks: [0, 1] };
  let [min, max] = sorted.length > 20 ? [quantile(sorted, 0.01), quantile(sorted, 0.99)] : [sorted[0], sorted.at(-1)!];
  if (max === min) { min -= 0.5; max += 0.5; }
  const pad = (max - min) * 0.05;
  [min, max] = [min - pad, max + pad];
  return { min, max, ticks: niceTicks(min, max) };
}

/** A plot's zoomed view: data ranges that replace the automatic axes. */
export interface View { x: [number, number]; y?: [number, number] }

const fixed = ([a, b]: [number, number]): Axis => ({ min: Math.min(a, b), max: Math.max(a, b), ticks: niceTicks(Math.min(a, b), Math.max(a, b)) });

/** Widen the narrower axis so one data unit spans the same pixels on both (sky plots). */
export function equalAxes(x: Axis, y: Axis, width: number, height: number): [Axis, Axis] {
  const unit = Math.max((x.max - x.min) / width, (y.max - y.min) / height);
  const grow = (a: Axis, px: number) => {
    const mid = (a.min + a.max) / 2, half = (unit * px) / 2;
    return fixed([mid - half, mid + half]);
  };
  return [grow(x, width), grow(y, height)];
}

/** `view` zooms in (a range replaces the 1–99% axes); `size` is the plot area in pixels,
 *  used when `spec.equal` asks for the same scale on both axes. */
export interface PlotOptions { view?: View | null; size?: [number, number] }

function visibleLayers(field: Field | null, map: MapState, table: string) {
  return map.layers.filter((layer) => layer.visible && layer.table === table && field?.tables[table]);
}

export function scatter(field: Field | null, map: MapState, spec: PlotSpec & { y: string }, options: PlotOptions = {}) {
  const all = rowsOf(field, spec.table).filter((row) => finite(row[spec.x]) && finite(row[spec.y]));
  const { view, size } = options;
  let x = view ? fixed(view.x) : axis(all.map((row) => row[spec.x] as number));
  let y = view?.y ? fixed(view.y) : axis(all.map((row) => row[spec.y] as number));
  if (spec.equal && size) [x, y] = equalAxes(x, y, size[0], size[1]);
  const inside = (row: Record<string, unknown>) => {
    const [vx, vy] = [row[spec.x] as number, row[spec.y] as number];
    return vx >= x.min && vx <= x.max && vy >= y.min && vy <= y.max;
  };
  const point = (row: Record<string, unknown> & { id: string }, color: string, r: number): PlotPoint =>
    ({ id: row.id, x: row[spec.x] as number, y: row[spec.y] as number, color, r });
  // Rows in no visible layer stay as faint context, as they do on the map.
  const points: PlotPoint[] = all.filter(inside).map((row) => point(row, '#3a4a5c', 1.5));
  for (const layer of visibleLayers(field, map, spec.table)) {
    for (const row of layerRows(field, layer)) {
      if (finite(row[spec.x]) && finite(row[spec.y]) && inside(row)) points.push(point(row, layer.style.color, 2.5));
    }
  }
  return { points, x, y, total: all.length, outside: all.filter((row) => !inside(row)).length, zoomed: !!view };
}

export function histogram(field: Field | null, map: MapState, spec: PlotSpec, bins = 30, options: PlotOptions = {}) {
  const values = rowsOf(field, spec.table).map((row) => row[spec.x]).filter(finite);
  const x = options.view ? fixed(options.view.x) : axis(values);
  const width = (x.max - x.min) / bins;
  const count = (vs: number[]) => {
    const counts = new Array(bins).fill(0);
    for (const v of vs) if (v >= x.min && v <= x.max) counts[Math.min(Math.floor((v - x.min) / width), bins - 1)]++;
    return counts;
  };
  const layers = visibleLayers(field, map, spec.table).map((layer) => ({
    id: layer.id, label: layer.label, color: layer.style.color,
    counts: count(layerRows(field, layer).map((row) => row[spec.x]).filter(finite))
  }));
  return { x, width, total: count(values), layers, n: values.length };
}

/** `view` zoomed by `factor` (< 1 zooms in) about the data point (cx, cy). */
export function zoomView(view: View, factor: number, cx: number, cy?: number): View {
  const scale = ([a, b]: [number, number], c: number): [number, number] => [c + (a - c) * factor, c + (b - c) * factor];
  return { x: scale(view.x, cx), ...(view.y && cy !== undefined ? { y: scale(view.y, cy) } : {}) };
}

/** The rows inside a dragged box, as filter clauses on the plotted columns. */
export function boxFilter(spec: PlotSpec, x0: number, x1: number, y0?: number, y1?: number): Clause[] {
  const round = (v: number) => +v.toPrecision(5);
  const clauses: Clause[] = [
    { column: spec.x, op: '>=', value: round(Math.min(x0, x1)) },
    { column: spec.x, op: '<=', value: round(Math.max(x0, x1)) }
  ];
  if (spec.y && y0 !== undefined && y1 !== undefined) clauses.push(
    { column: spec.y, op: '>=', value: round(Math.min(y0, y1)) },
    { column: spec.y, op: '<=', value: round(Math.max(y0, y1)) }
  );
  return clauses;
}

/** A drawn path (data units) thinned to at most maxVertices points, rounded. */
export function simplifyPath(path: [number, number][], maxVertices = 60): [number, number][] {
  const step = Math.max(1, Math.ceil(path.length / maxVertices));
  const round = (v: number) => +v.toPrecision(6);
  return path.filter((_, i) => i % step === 0).map(([x, y]) => [round(x), round(y)]);
}

/** A box on a scatter plot as a polygon, so boxes and lassos are one kind of selection. */
export function boxPolygon(x0: number, x1: number, y0: number, y1: number): [number, number][] {
  const round = (v: number) => +v.toPrecision(6);
  const [a, b, c, d] = [Math.min(x0, x1), Math.max(x0, x1), Math.min(y0, y1), Math.max(y0, y1)].map(round);
  return [[a, c], [b, c], [b, d], [a, d]];
}

/** Plot presets the field's data sources declare, offered when their columns exist. */
export function presets(field: Field | null): (PlotSpec & Preset)[] {
  return Object.entries(field?.tables ?? {}).flatMap(([table, t]) => (t.presets ?? [])
    .filter((p) => t.columns.includes(p.x) && (!p.y || t.columns.includes(p.y)))
    .map((p) => ({ ...p, table })));
}
