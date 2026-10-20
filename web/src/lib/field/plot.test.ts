import { describe, expect, it } from 'vitest';
import { axis, boxFilter, equalAxes, histogram, niceTicks, presets, scatter, zoomView } from './plot';
import { applyAction, checkAction, layerRows } from './actions';
import { miniFieldAllOn as miniField } from './fixtures';
import type { MapState } from './types';

const state = (): MapState => ({ layers: miniField().layers, highlight: null, annotations: [], selection: null, plot: null });

describe('plot data', () => {
  it('picks readable ticks and robust ranges', () => {
    expect(niceTicks(0, 1)).toEqual([0, 0.2, 0.4, 0.6, 0.8, 1]);
    expect(niceTicks(-2.3, 7.9)).toEqual([0, 2.5, 5, 7.5]);
    expect(niceTicks(3, 3)).toEqual([3]);
    // One wild outlier among many does not set the range.
    const values = [...Array.from({ length: 99 }, (_, i) => i / 10), 1000];
    expect(axis(values).max).toBeLessThan(50);
    expect(axis([]).ticks).toEqual([0, 1]);
  });

  it('draws visible layers in their colours over faint context rows', () => {
    const field = miniField();
    const map = state();
    map.layers = map.layers.map((l) => (l.id === 'gaia:rrlyrae' ? { ...l, visible: false } : l));
    const plot = scatter(field, map, { table: 'gaia', x: 'bp_rp', y: 'G' });
    expect(plot.total).toBe(5);
    expect(plot.points.filter((p) => p.color === '#3a4a5c')).toHaveLength(5);
    const stars = map.layers.find((l) => l.id === 'gaia:stars')!;
    expect(plot.points.filter((p) => p.color === stars.style.color)).toHaveLength(2);
    // Hidden layers (RR Lyrae candidates) are not drawn in colour.
    const rrl = map.layers.find((l) => l.id === 'gaia:rrlyrae')!;
    expect(plot.points.some((p) => p.id === '1005' && p.color === rrl.style.color)).toBe(false);
  });

  it('histograms the table and each visible layer on the same bins', () => {
    const field = miniField();
    const result = histogram(field, state(), { table: 'gaia', x: 'G' }, 5);
    expect(result.n).toBe(5);
    expect(result.total.reduce((a, b) => a + b, 0)).toBe(5);
    const stars = result.layers.find((l) => l.id === 'gaia:stars')!;
    expect(stars.counts.reduce((a, b) => a + b, 0)).toBe(2);
  });

  it('turns a dragged box into a layer filter that selects the boxed rows', () => {
    const field = miniField();
    const filter = boxFilter({ table: 'gaia', x: 'G', y: 'bp_rp' }, 19.6, 18.5, 0.5, 1.5);
    expect(filter).toEqual([
      { column: 'G', op: '>=', value: 18.5 }, { column: 'G', op: '<=', value: 19.6 },
      { column: 'bp_rp', op: '>=', value: 0.5 }, { column: 'bp_rp', op: '<=', value: 1.5 }]);
    expect(layerRows(field, { table: 'gaia', filter }).map((r) => r.id).sort()).toEqual(['1004', '1005']);
    expect(boxFilter({ table: 'gaia', x: 'G' }, 1, 2)).toHaveLength(2);
  });

  it('offers the presets the data sources declare, when their columns exist', () => {
    const field = miniField();
    const offered = presets(field);
    expect(offered.map((p) => `${p.table}:${p.label}`)).toEqual([
      'gaia:CMD', 'gaia:Proper motion', 'gaia:Parallax / G', 'gaia:RV / G', 'gaia:Sky (ξ, η)',
      'spectra:RV histogram', 'spectra:RV vs [Fe/H]']);
    field.tables.gaia.columns = field.tables.gaia.columns.filter((c) => c !== 'pmdec');
    expect(presets(field).some((p) => p.label === 'Proper motion')).toBe(false);
    expect(presets(null)).toEqual([]);
  });

  it('zooms: a view replaces the automatic range, and zoomView scales about a point', () => {
    const spec = { table: 'gaia', x: 'G', y: 'bp_rp' };
    const all = scatter(miniField(), state(), spec);
    const zoomed = scatter(miniField(), state(), spec, { view: { x: [all.x.min, (all.x.min + all.x.max) / 2], y: [all.y.min, all.y.max] } });
    expect(zoomed.zoomed).toBe(true);
    expect(zoomed.x.max).toBeCloseTo((all.x.min + all.x.max) / 2);
    expect(new Set(zoomed.points.map((p) => p.id)).size + zoomed.outside).toBe(all.total);
    expect(zoomView({ x: [0, 10], y: [0, 4] }, 0.5, 2, 1)).toEqual({ x: [1, 6], y: [0.5, 2.5] });
    expect(zoomView({ x: [0, 10] }, 2, 5)).toEqual({ x: [-5, 15] });
  });

  it('equal axes give one data unit the same pixels on both axes', () => {
    const [x, y] = equalAxes({ min: 0, max: 10, ticks: [] }, { min: 0, max: 1, ticks: [] }, 500, 250);
    expect((x.max - x.min) / 500).toBeCloseTo((y.max - y.min) / 250);
    expect([x.min, x.max]).toEqual([0, 10]);
    expect((y.min + y.max) / 2).toBeCloseTo(0.5);
  });
});

describe('plot actions', () => {
  it('shows and hides a plot as part of map state, rejecting non-numeric columns', () => {
    const field = miniField();
    const s = state();
    expect(checkAction({ type: 'plot.show', table: 'gaia', x: 'clean' }, field, s)).toMatch(/not a numeric/);
    expect(checkAction({ type: 'plot.show', table: 'stars', x: 'G' }, field, s)).toMatch(/unknown table/);
    const shown = applyAction({ type: 'plot.show', table: 'gaia', x: 'bp_rp', y: 'G', invert_y: true }, field, s, 'agent', () => 1).state;
    expect(shown.plot).toEqual({ table: 'gaia', x: 'bp_rp', y: 'G', invert_y: true });
    expect(applyAction({ type: 'plot.hide' }, field, shown, 'user', () => 1).state.plot).toBeNull();
  });
});
