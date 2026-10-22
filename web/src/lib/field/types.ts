// Mirrors scoop/science/fields.py and scoop/agents/visual.py. See docs/map_interaction.md.

export type Value = string | number | boolean | null;
export type Op = '==' | '!=' | '<' | '<=' | '>' | '>=' | 'in' | 'exists' | 'within' | 'inside' | 'lasso';
// 'within' and 'inside' use column 'position': {ra, dec, radius} or {polygon: [[ra, dec], ...]}, degrees.
// 'lasso' uses the plot's x column: {y: column, polygons: [[[x, y], ...], ...], missing?: 'keep' | 'reject'}.
export interface Circle { ra: number; dec: number; radius: number }
export interface Lasso { y: string; polygons: [number, number][][]; missing?: 'keep' | 'reject' }
export interface Clause { column: string; op: Op; value?: Value | Value[] | Circle | { polygon: [number, number][] } | Lasso }

export interface Row {
  id: string;
  ra: number | null;
  dec: number | null;
  s_region?: string;
  polygon?: [number, number][];
  // Display hooks filled by the row's data source (not filterable columns).
  _links?: [string, string][];
  _details?: Record<string, Detail[]>;
  [column: string]: unknown;
}

// One display-ready record in the Selected panel, e.g. one spectrum.
export interface Detail { title: string; fields: Record<string, unknown>; links: [string, string][] }
export interface Preset { label: string; x: string; y?: string; invert_x?: boolean; invert_y?: boolean; equal?: boolean; title?: string }

// One data source's table, with the hooks it fills in the interface.
export interface Table {
  label: string;
  provenance: string;
  kind?: 'catalog' | 'footprints' | 'annotation';
  presets?: Preset[]; // plot panel
  browse?: string[]; // table browser columns; empty = not listed
  columns: string[];
  units?: Record<string, string>;
  descriptions?: Record<string, string>;
  rows: Row[];
}

export const SHAPES = ['circle', 'square', 'plus', 'cross', 'rhomb', 'triangle'] as const;
export type Shape = (typeof SHAPES)[number];
export interface Style { color: string; shape: Shape; size: number; dash: boolean; hollow: boolean }

export interface Layer {
  id: string;
  table: string;
  filter: Clause[];
  group: string;
  label: string;
  style: Style;
  visible: boolean;
  // On the user's layer list; others are offered under "Add data".
  added?: boolean;
  origin: 'pipeline' | 'agent' | 'user';
  note: string | null;
  count: number;
}

export interface Field {
  key: string;
  target: { name: string; ra: number; dec: number; rhalf_arcmin: number; ellipticity: number;
    pa_deg: number; ref_structure: string; distance_kpc: number | null; pmra?: number | null;
    pmdec?: number | null; ref_proper_motion?: string | null; kind?: 'lvdb' | 'cone'; references?: Record<string, string> };
  n_rhalf: number;
  fov: number;
  region?: [number, number][];
  tables: Record<string, Table>;
  // Small tables the data sources provide about their rows (e.g. MAST imaging coverage).
  summaries?: { source: string; title: string; columns: string[]; rows: unknown[][]; note?: string }[];
  decisions: string[];
  surveys: [string, string][];
  layers: Layer[];
}

export interface Highlight { table: string; ids: string[]; reason: string | null; origin: Origin }
export interface Annotation {
  id: number;
  shape: 'circle' | 'label' | 'polygon';
  ra: number;
  dec: number;
  text: string;
  radius?: number;
  points?: [number, number][];
  table?: string;
  rowId?: string;
  origin: Origin;
}
export type Origin = 'agent' | 'user';
export interface RowRef { table: string; id: string }

export type MapAction =
  | { type: 'layer.add'; id: string; table: string; filter?: Clause[]; label: string; group?: string;
      style?: Partial<Style>; note?: string }
  | { type: 'layer.update'; layerId: string; visible?: boolean; added?: boolean; filter?: Clause[]; style?: Partial<Style>;
      label?: string; note?: string }
  | { type: 'layer.remove'; layerId: string }
  | { type: 'focus'; ra: number; dec: number; fov: number }
  | { type: 'focus.rows'; table: string; ids: string[] }
  | { type: 'highlight'; table: string; ids: string[]; reason?: string }
  | { type: 'annotate'; shape: 'circle' | 'label' | 'polygon'; ra: number; dec: number; text: string; radius?: number;
      points?: [number, number][]; table?: string; rowId?: string }
  | { type: 'annotations.clear' }
  | { type: 'select'; table: string; id: string }
  | ({ type: 'plot.show' } & PlotSpec)
  | { type: 'plot.hide' };

// A scatter (x, y) or histogram (x only) of one table, drawn with the map's layers.
// A preset drawn from one table.
export type PlotSpec = Omit<Preset, 'label'> & { table: string };

export interface Camera { ra: number; dec: number; fov: number }

// Everything the map shows besides the field data: what scenes and undo restore.
export interface MapState {
  layers: Layer[];
  highlight: Highlight | null;
  annotations: Annotation[];
  selection: RowRef | null;
  plot?: PlotSpec | null;
}
