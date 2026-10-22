// Applying map actions to map state. The server validates first (scoop/agents/visual.py);
// the browser checks again because it is the renderer and the user may have changed things.
import { matches } from './filter';
import { SHAPES } from './types';
import type { Camera, Clause, Field, Layer, MapAction, MapState, Origin, Row, Style } from './types';

const DEFAULT_STYLE: Style = { color: '#ffd84d', shape: 'circle', size: 12, dash: false, hollow: false };
const finite = (v: unknown): v is number => typeof v === 'number' && Number.isFinite(v);
const inSky = (ra: unknown, dec: unknown) => finite(ra) && ra >= 0 && ra < 360 && finite(dec) && dec >= -90 && dec <= 90;

function checkStyle(style: Partial<Style> | undefined): string | null {
  if (!style) return null;
  if (style.color !== undefined && !/^#[0-9a-fA-F]{6}$/.test(style.color)) return 'style.color must be #rrggbb';
  if (style.shape !== undefined && !SHAPES.includes(style.shape)) return `style.shape must be one of ${SHAPES.join(', ')}`;
  if (style.size !== undefined && !(Number.isInteger(style.size) && style.size >= 2 && style.size <= 40)) return 'style.size must be 2–40';
  return null;
}

export function rowsOf(field: Field | null, table: string): Row[] {
  return field?.tables[table]?.rows ?? [];
}

export function layerRows(field: Field | null, layer: Pick<Layer, 'table' | 'filter'>): Row[] {
  return rowsOf(field, layer.table).filter((row) => matches(row, layer.filter));
}

/** Why the action cannot apply, or null. */
export function checkAction(action: MapAction, field: Field | null, state: MapState): string | null {
  if (!action || typeof action !== 'object') return 'not an action';
  const table = 'table' in action ? action.table : undefined;
  if (table !== undefined && !field?.tables[table]) return `unknown table ${table}`;
  const ids = (t: string, list: string[]) => {
    const known = new Set(rowsOf(field, t).map((row) => row.id));
    const unknown = list.filter((id) => !known.has(id));
    return unknown.length ? `unknown ${t} ids: ${unknown.slice(0, 5).join(', ')}` : null;
  };
  const layer = 'layerId' in action ? state.layers.find((l) => l.id === action.layerId) : undefined;
  switch (action.type) {
    case 'layer.add':
      if (state.layers.some((l) => l.id === action.id)) return `layer ${action.id} exists`;
      return checkStyle(action.style);
    case 'layer.update':
      return layer ? checkStyle(action.style) : `unknown layer ${action.layerId}`;
    case 'layer.remove':
      if (!layer) return `unknown layer ${action.layerId}`;
      return layer.origin === 'pipeline' ? 'pipeline layers can only be hidden' : null;
    case 'focus':
      return inSky(action.ra, action.dec) && finite(action.fov) && action.fov > 0 && action.fov <= 180 ? null : 'invalid focus';
    case 'focus.rows':
      return action.ids.length ? ids(action.table, action.ids) : 'no rows to focus';
    case 'highlight':
      return ids(action.table, action.ids);
    case 'select':
      return ids(action.table, [action.id]);
    case 'annotate':
      if (!inSky(action.ra, action.dec) || !action.text?.trim()) return 'invalid annotation';
      if (action.shape === 'circle' && !(finite(action.radius) && action.radius > 0 && action.radius <= 180)) return 'circle needs a radius';
      if (action.shape === 'polygon' && !(Array.isArray(action.points) && action.points.length >= 3 && action.points.length <= 64
        && action.points.every((p) => inSky(p?.[0], p?.[1])))) return 'polygon needs 3–64 [ra, dec] points';
      if (action.rowId !== undefined) return action.table ? ids(action.table, [action.rowId]) : 'anchored annotation needs a table';
      return null;
    case 'annotations.clear':
    case 'plot.hide':
      return null;
    case 'plot.show': {
      const rows = rowsOf(field, action.table);
      for (const column of [action.x, action.y].filter((c): c is string => c !== undefined)) {
        if (!rows.some((row) => typeof row[column] === 'number')) return `${column} is not a numeric column of ${action.table}`;
      }
      return null;
    }
    default:
      return 'unknown action type';
  }
}

// Names for map colours, so the agent can match "the orange triangles" to a layer.
const COLOR_NAMES: [string, [number, number, number]][] = [
  ['white', [240, 240, 236]], ['grey', [160, 160, 160]], ['blue', [57, 135, 229]], ['light blue', [159, 211, 255]],
  ['orange', [217, 89, 38]], ['green', [47, 211, 155]], ['purple', [144, 133, 233]], ['amber', [201, 133, 0]],
  ['pink', [213, 81, 129]], ['yellow', [255, 216, 77]], ['sand', [241, 203, 130]], ['red', [208, 70, 60]]
];

export function colorName(hex: string): string {
  const m = /^#?([0-9a-f]{2})([0-9a-f]{2})([0-9a-f]{2})$/i.exec(hex);
  if (!m) return hex;
  const rgb = [m[1], m[2], m[3]].map((h) => parseInt(h, 16));
  let best = '';
  let bestDistance = Infinity;
  for (const [name, ref] of COLOR_NAMES) {
    const distance = ref.reduce((sum, v, i) => sum + (v - rgb[i]) ** 2, 0);
    if (distance < bestDistance) [best, bestDistance] = [name, distance];
  }
  return best;
}

/** How a layer looks, in words: "orange #d95926 triangle, hollow". */
export function describeStyle(style: Style, outline: boolean): string {
  const shape = outline ? (style.dash ? 'dashed outline' : 'outline') : style.shape;
  return `${colorName(style.color)} ${style.color} ${shape}${style.hollow && !outline ? ', hollow' : ''}`;
}

/** Visible layers' rows inside a region clause: instant feedback for a drawn shape. */
export function regionCounts(field: Field | null, state: MapState, region: Clause) {
  return state.layers
    .filter((layer) => layer.visible && field?.tables[layer.table]?.kind !== 'annotation')
    .map((layer) => ({ id: layer.id, label: layer.label, count: layerRows(field, { ...layer, filter: [...layer.filter, region] }).length }))
    .filter((item) => item.count > 0);
}

export function circleCounts(field: Field | null, state: MapState, ra: number, dec: number, radius: number) {
  return regionCounts(field, state, { column: 'position', op: 'within', value: { ra, dec, radius } });
}

/** Label for a drawn region, e.g. "2′ · 37 rows in 3 layers" or "3′ × 2′ · …". */
export function regionLabel(size: string, counts: { count: number }[]): string {
  const total = counts.reduce((sum, item) => sum + item.count, 0);
  return `${size} · ${total} row${total === 1 ? '' : 's'} in ${counts.length} layer${counts.length === 1 ? '' : 's'}`;
}

export const circleLabel = (radiusArcmin: number, counts: { count: number }[]) =>
  regionLabel(`${+radiusArcmin.toPrecision(3)}′`, counts);

/** Frame rows: center on their mean position (RA unwrapped) with some margin. */
export function frame(rows: Row[]): Camera | null {
  const points = rows.filter((row) => finite(row.ra) && finite(row.dec)) as (Row & { ra: number; dec: number })[];
  if (!points.length) return null;
  const ref = points[0].ra;
  const ras = points.map((p) => ref + ((((p.ra - ref) % 360) + 540) % 360) - 180);
  const decs = points.map((p) => p.dec);
  const dec = (Math.min(...decs) + Math.max(...decs)) / 2;
  const ra = (((Math.min(...ras) + Math.max(...ras)) / 2) % 360 + 360) % 360;
  const cos = Math.max(Math.cos((dec * Math.PI) / 180), 0.05);
  const span = Math.max((Math.max(...ras) - Math.min(...ras)) * cos, Math.max(...decs) - Math.min(...decs));
  return { ra, dec, fov: Math.min(Math.max(span * 1.5, 0.02), 180) };
}

/**
 * Apply a checked action to a copy of the state. Returns the new state and,
 * for focus actions, where the camera should go.
 */
export function applyAction(action: MapAction, field: Field | null, state: MapState, origin: Origin,
  nextId: () => number): { state: MapState; camera: Camera | null } {
  const next = clone(state);
  let camera: Camera | null = null;
  switch (action.type) {
    case 'layer.add': {
      const layer: Layer = { id: action.id, table: action.table, filter: action.filter ?? [],
        group: action.group ?? 'SCOOP layers', label: action.label, style: { ...DEFAULT_STYLE, ...action.style },
        visible: true, added: true, origin, note: action.note ?? null, count: 0 };
      layer.count = layerRows(field, layer).length;
      next.layers.push(layer);
      break;
    }
    case 'layer.update': {
      const layer = next.layers.find((l) => l.id === action.layerId)!;
      if (action.added !== undefined) layer.added = action.added;
      if (action.visible !== undefined) layer.visible = action.visible;
      // Showing a layer puts it on the list; taking it off the list hides it.
      if (action.visible === true) layer.added = true;
      if (action.added === false) layer.visible = false;
      if (action.filter !== undefined) layer.filter = action.filter;
      if (action.style !== undefined) layer.style = { ...layer.style, ...action.style };
      if (action.label !== undefined) layer.label = action.label;
      if (action.note !== undefined) layer.note = action.note;
      layer.count = layerRows(field, layer).length;
      break;
    }
    case 'layer.remove': next.layers = next.layers.filter((l) => l.id !== action.layerId); break;
    case 'focus': camera = { ra: action.ra, dec: action.dec, fov: action.fov }; break;
    case 'focus.rows': {
      const wanted = new Set(action.ids);
      camera = frame(rowsOf(field, action.table).filter((row) => wanted.has(row.id)));
      break;
    }
    case 'highlight':
      next.highlight = action.ids.length ? { table: action.table, ids: [...action.ids], reason: action.reason ?? null, origin } : null;
      break;
    case 'annotate':
      next.annotations.push({ id: nextId(), shape: action.shape, ra: action.ra, dec: action.dec, text: action.text,
        ...(action.radius !== undefined ? { radius: action.radius } : {}),
        ...(action.points !== undefined ? { points: action.points.map(([r, d]) => [r, d] as [number, number]) } : {}),
        ...(action.table !== undefined ? { table: action.table } : {}),
        ...(action.rowId !== undefined ? { rowId: action.rowId } : {}), origin });
      break;
    case 'annotations.clear': next.annotations = []; break;
    case 'select': next.selection = { table: action.table, id: action.id }; break;
    case 'plot.show': {
      const { type: _, ...spec } = action;
      next.plot = spec;
      break;
    }
    case 'plot.hide': next.plot = null; break;
  }
  return { state: next, camera };
}

// Svelte $state proxies cannot be structuredClone'd; map state is plain JSON.
export function clone<T>(value: T): T {
  return JSON.parse(JSON.stringify(value));
}
