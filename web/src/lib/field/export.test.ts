import { describe, expect, it } from 'vitest';
import { columnsOf, layerCsv, toCsv } from './export';
import { miniField } from './fixtures';

describe('CSV export', () => {
  it('writes a provenance header, units and only real columns', () => {
    const field = miniField();
    const rows = field.tables.gaia.rows.filter((r) => r.id === '1001');
    const text = toCsv(field, 'gaia', rows, { label: 'Gaia sources', filter: [{ column: 'star', op: '==', value: true }],
      now: new Date('2026-10-20T00:00:00Z') });
    const lines = text.trimEnd().split('\n');
    expect(lines[0]).toBe('# SCOOP export: Gaia sources');
    expect(lines[1]).toBe('# field: mini_1 (Mini)');
    expect(lines[2]).toBe('# data source: Gaia DR3 sources — Gaia DR3 (ARI Heidelberg TAP)');
    expect(lines[3]).toBe('# filter: [{"column":"star","op":"==","value":true}]');
    expect(lines[4]).toBe('# exported: 2026-10-20T00:00:00.000Z · 1 rows');
    expect(lines[5]).toContain('pmra [mas/yr]');
    expect(lines[6].split(',')[0]).toBe('id');
    expect(lines[6]).not.toMatch(/_details|_links|polygon/);
    expect(lines[7].startsWith('1001,260.01,57.9,')).toBe(true);
  });

  it('quotes awkward values and keeps ids as text', () => {
    const field = miniField();
    const text = toCsv(field, 'observations', [{ id: 'a"b', ra: 1, dec: 2, pi: 'Doe, J.', s_region: 'POLYGON 1 2\n3 4' }], { label: 'x' });
    expect(text).toContain('"a""b",1,2,"Doe, J.","POLYGON 1 2\n3 4"');
    expect(columnsOf([{ id: '1', ra: 1, dec: 2, _links: [], polygon: [] }])).toEqual(['id', 'ra', 'dec']);
  });

  it('exports each layer of a group to its own file', () => {
    const field = miniField();
    const files = layerCsv(field, field.layers.filter((l) => l.group === 'Trusted QSOs'));
    expect(files.map((f) => f.name)).toEqual(['mini_1_qso_crf.csv', 'mini_1_qso_quaia.csv', 'mini_1_qso_milliquas.csv', 'mini_1_qso_milliquas_candidates.csv']);
    expect(files[0].text).toContain('· 1 rows');
  });
});
