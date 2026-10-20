// "Add data": what this field holds but isn't on the user's list, searchable.
// The unit is a layer group (e.g. "Gaia DR3", "Spectra"); a search that names a
// specific layer (e.g. "RR Lyrae") also offers that layer on its own.
import type { Field, Layer } from './types';

export interface CatalogGroup {
  group: string;
  count: number;
  provenance: string;
  layers: Layer[]; // layers the "＋" on the group adds
  matching: Layer[]; // individual layers the query named
  table?: string; // set for a data source with no layers ("All rows")
  query?: string; // set for a data source the field lacks; adding it queries the archive
}

const words = (text: string) => text.toLowerCase().split(/[^a-z0-9]+/).filter(Boolean);
const hits = (wanted: string[], text: string) => {
  const have = words(text);
  return wanted.every((w) => have.some((h) => h.startsWith(w)));
};

/** Layers grouped as the panel shows them, in field order. */
export function groupLayers(layers: Layer[]): [string, Layer[]][] {
  const out = new Map<string, Layer[]>();
  for (const layer of layers) out.set(layer.group, [...(out.get(layer.group) ?? []), layer]);
  return [...out];
}

export function catalog(field: Field | null, layers: Layer[], query = '',
  queryable: { name: string; label: string; provenance: string }[] = []): CatalogGroup[] {
  if (!field) return [];
  const wanted = words(query);
  const groups: CatalogGroup[] = [];
  const offered = layers.filter((layer) => !layer.added && field.tables[layer.table]?.kind !== 'annotation');
  for (const [group, members] of groupLayers(offered)) {
    const tables = [...new Set(members.map((layer) => layer.table))].map((name) => field.tables[name]);
    const context = `${group} ${tables.map((t) => `${t.label} ${t.provenance}`).join(' ')}`;
    const groupHit = hits(wanted, context);
    const matching = wanted.length ? members.filter((layer) => hits(wanted, `${layer.label} ${context}`)) : [];
    if (!groupHit && !matching.length) continue;
    groups.push({ group, count: members.reduce((sum, layer) => sum + layer.count, 0),
      provenance: [...new Set(tables.map((t) => t.provenance))].join(', '), layers: members,
      matching: groupHit && matching.length === members.length ? [] : matching });
  }
  // A data source with no layers of its own is still reachable as a whole.
  for (const [name, table] of Object.entries(field.tables)) {
    if (table.kind === 'annotation' || !table.rows.length || layers.some((layer) => layer.table === name)) continue;
    if (!hits(wanted, `${table.label} ${table.provenance} ${name}`)) continue;
    groups.push({ group: table.label, count: table.rows.length, provenance: table.provenance, layers: [], matching: [], table: name });
  }
  // Archives this field has not queried yet: adding one queries its region.
  for (const source of queryable) {
    if (field.tables[source.name] || !hits(wanted, `${source.label} ${source.provenance} ${source.name}`)) continue;
    groups.push({ group: source.label, count: 0, provenance: source.provenance, layers: [], matching: [], query: source.name });
  }
  return groups;
}
