// CSV export of a layer or table, with a provenance header so the file says
// where its rows came from. Display-only keys (_links, _details, polygon) are left out.
import { layerRows } from './actions';
import type { Field, Layer, Row } from './types';

const cell = (value: unknown): string => {
  if (value === null || value === undefined) return '';
  const text = typeof value === 'object' ? JSON.stringify(value) : String(value);
  return /[",\n\r]/.test(text) ? `"${text.replace(/"/g, '""')}"` : text;
};

export function columnsOf(rows: Row[]): string[] {
  const names = new Set<string>();
  for (const row of rows) for (const key of Object.keys(row)) if (!key.startsWith('_') && key !== 'polygon') names.add(key);
  return ['id', ...[...names].filter((n) => n !== 'id')];
}

/** CSV text: "#" provenance lines, a header row, then one line per row. */
export function toCsv(field: Field, table: string, rows: Row[], about: { label: string; filter?: unknown; now?: Date }): string {
  const t = field.tables[table];
  const columns = columnsOf(rows);
  const units = columns.map((c) => t?.units?.[c] ?? '');
  const meta = [
    `# SCOOP export: ${about.label}`,
    `# field: ${field.key} (${field.target.name})`,
    `# data source: ${t?.label ?? table} — ${t?.provenance ?? 'unknown'}`,
    ...(about.filter !== undefined ? [`# filter: ${JSON.stringify(about.filter)}`] : []),
    `# exported: ${(about.now ?? new Date()).toISOString()} · ${rows.length} rows`,
    ...(units.some(Boolean) ? [`# units: ${columns.map((c, i) => (units[i] ? `${c} [${units[i]}]` : null)).filter(Boolean).join(', ')}`] : [])
  ];
  const lines = rows.map((row) => columns.map((c) => cell(row[c])).join(','));
  return [...meta, columns.map(cell).join(','), ...lines].join('\n') + '\n';
}

export function layerCsv(field: Field, layers: Layer[], now?: Date): { name: string; text: string }[] {
  return layers.map((layer) => ({
    name: `${field.key}_${layer.id.replace(/[^a-z0-9]+/gi, '_')}.csv`,
    text: toCsv(field, layer.table, layerRows(field, layer), { label: layer.label, filter: layer.filter, now })
  }));
}

/** Save text as a file in the browser. */
export function download(name: string, text: string): void {
  const url = URL.createObjectURL(new Blob([text], { type: 'text/csv;charset=utf-8' }));
  const link = Object.assign(document.createElement('a'), { href: url, download: name });
  document.body.append(link);
  link.click();
  link.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}
