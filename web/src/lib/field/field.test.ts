import { describe, expect, it } from 'vitest';
import { matches } from './filter';
import { applyAction, checkAction, circleCounts, circleLabel, colorName, describeStyle, frame, layerRows } from './actions';
import { boxFromDrag, circleFromDrag, polygonFromClicks, wheelFactor, zoomAnchor } from '../sky/aladin';
import { filterCases, miniField, miniFieldAllOn } from './fixtures';
import type { MapState } from './types';

const state = (): MapState => ({ layers: miniField().layers, highlight: null, annotations: [], selection: null });

describe('filter', () => {
  it('agrees with the Python evaluator on the shared cases', () => {
    const { rows, cases } = filterCases();
    for (const c of cases) expect(rows.filter((row) => matches(row, c.filter)).map((row) => row.id), JSON.stringify(c.filter)).toEqual(c.ids);
  });

  it('reproduces the pipeline layer counts', () => {
    const field = miniField();
    for (const layer of field.layers) expect(layerRows(field, layer).length, layer.id).toBe(layer.count);
  });
});

describe('map actions', () => {
  it('rejects what does not exist and what is not allowed', () => {
    const field = miniField();
    const s = state();
    expect(checkAction({ type: 'highlight', table: 'gaia', ids: ['9999'] }, field, s)).toMatch(/unknown gaia ids/);
    expect(checkAction({ type: 'select', table: 'stars', id: '1' }, field, s)).toMatch(/unknown table/);
    expect(checkAction({ type: 'layer.remove', layerId: 'gaia:stars' }, field, s)).toMatch(/only be hidden/);
    expect(checkAction({ type: 'layer.add', id: 'gaia:stars', table: 'gaia', label: 'x' }, field, s)).toMatch(/exists/);
    expect(checkAction({ type: 'layer.add', id: 'x', table: 'gaia', label: 'x', style: { color: 'red' } }, field, s)).toMatch(/color/);
    expect(checkAction({ type: 'focus', ra: 360, dec: 0, fov: 1 }, field, s)).toBeTruthy();
    expect(checkAction({ type: 'eval', code: 'alert(1)' } as never, field, s)).toBe('unknown action type');
    expect(checkAction({ type: 'highlight', table: 'gaia', ids: ['1004'] }, null, s)).toMatch(/unknown table/);
  });

  it('applies to a copy and reports camera moves', () => {
    const field = miniField();
    const before = state();
    let id = 0;
    const added = applyAction({ type: 'layer.add', id: 'agent:bright', table: 'gaia', label: 'G < 19.5',
      filter: [{ column: 'G', op: '<', value: 19.5 }], style: { color: '#ff0000' } }, field, before, 'agent', () => ++id);
    expect(before.layers.some((l) => l.id === 'agent:bright')).toBe(false);
    const layer = added.state.layers.at(-1)!;
    expect(layer).toMatchObject({ origin: 'agent', count: 2, visible: true, style: { color: '#ff0000', shape: 'circle' } });

    const hidden = applyAction({ type: 'layer.update', layerId: 'gaia:stars', visible: false }, field, added.state, 'user', () => ++id);
    expect(hidden.state.layers.find((l) => l.id === 'gaia:stars')!.visible).toBe(false);

    const focused = applyAction({ type: 'focus.rows', table: 'gaia', ids: ['1001', '1005'] }, field, hidden.state, 'agent', () => ++id);
    expect(focused.camera!.ra).toBeCloseTo(260.03);
    expect(focused.state).toEqual(hidden.state);

    const noted = applyAction({ type: 'annotate', shape: 'label', ra: 260.04, dec: 57.9, text: 'CRF QSO', table: 'gaia', rowId: '1004' },
      field, hidden.state, 'agent', () => ++id);
    expect(noted.state.annotations).toEqual([{ id: 1, shape: 'label', ra: 260.04, dec: 57.9, text: 'CRF QSO', table: 'gaia', rowId: '1004', origin: 'agent' }]);
  });

  it('counts visible layers inside a drawn circle and labels it', () => {
    const field = miniFieldAllOn();
    // 0.006 deg around Gaia 1002 (260.02, 57.9): 1001, 1002, 1003 are within reach.
    const counts = circleCounts(field, { layers: field.layers, highlight: null, annotations: [], selection: null }, 260.02, 57.9, 0.006);
    expect(counts.find((c) => c.id === 'gaia:stars')?.count).toBe(2);
    expect(counts.find((c) => c.id === 'gaia:galaxies')?.count).toBe(1);
    expect(counts.some((c) => c.id.startsWith('target:'))).toBe(false);
    expect(circleLabel(2, counts)).toMatch(/^2′ · \d+ rows in \d+ layers$/);
    expect(circleLabel(0.5, [{ count: 1 }])).toBe('0.5′ · 1 row in 1 layer');
  });

  it('turns a drag into a circle: center at the press, radius to the release', () => {
    // 1 pixel = 0.01 deg on a small patch at the equator.
    const pix = (x: number, y: number): [number, number] => [10 + x * 0.01, y * 0.01];
    const circle = circleFromDrag(pix, 0, 0, 30, 40)!;
    expect(circle.ra).toBe(10);
    expect(circle.dec).toBe(0);
    expect(circle.radiusArcmin).toBeCloseTo(30, 3);
    expect(circleFromDrag(pix, 0, 0, 2, 2)).toBeNull();
    expect(circleFromDrag(() => undefined, 0, 0, 30, 40)).toBeNull();
    expect(circleFromDrag((x) => [x < 1 ? -0.5 : 0.5, 0], 0, 0, 30, 0)!.ra).toBe(359.5);
  });

  it('turns a corner-to-corner drag into a sky box, in either direction', () => {
    const pix = (x: number, y: number): [number, number] => [10 + x * 0.01, y * 0.01];
    const box = boxFromDrag(pix, 30, 40, 0, 0)!;
    expect(box.points).toEqual([[10.3, 0.4], [10, 0.4], [10, 0], [10.3, 0]].map(([r, d]) => [expect.closeTo(r, 9), expect.closeTo(d, 9)]));
    expect(box.ra).toBeCloseTo(10.15);
    expect(box.widthArcmin).toBeCloseTo(18, 1);
    expect(box.heightArcmin).toBeCloseTo(24, 3);
    expect(boxFromDrag(pix, 0, 0, 30, 2)).toBeNull();
    expect(boxFromDrag(() => null, 0, 0, 30, 40)).toBeNull();
  });

  it('turns clicked corners into a sky polygon', () => {
    const pix = (x: number, y: number): [number, number] => [x * 0.01 - 0.1, y * 0.01];
    const shape = polygonFromClicks(pix, [{ x: 0, y: 0 }, { x: 20, y: 0 }, { x: 10, y: 30 }])!;
    expect(shape.points[0]).toEqual([expect.closeTo(359.9, 9), 0]); // RA wrapped into [0, 360)
    expect(shape.ra).toBeCloseTo(0, 9);
    expect(shape.dec).toBeCloseTo(0.1, 9);
    expect(polygonFromClicks(pix, [{ x: 0, y: 0 }, { x: 1, y: 1 }])).toBeNull();
    expect(polygonFromClicks(() => null, [{ x: 0, y: 0 }, { x: 1, y: 1 }, { x: 2, y: 0 }])).toBeNull();
  });

  it('keeps the sky point under the cursor fixed when zooming', () => {
    // Before zooming the cursor was over (10.2, 0.1); afterwards that pixel shows (10.1, 0.05).
    expect(zoomAnchor([10, 0], [10.2, 0.1], [10.1, 0.05])).toEqual([expect.closeTo(10.1, 9), expect.closeTo(0.05, 9)]);
    // Across RA = 0 the shift is small, not almost 360 degrees.
    expect(zoomAnchor([0.01, 0], [359.99, 0], [0.02, 0])[0]).toBeCloseTo(359.98, 9);
    expect(zoomAnchor([10, 89.99], [10, 89.9], [10, 89.8])[1]).toBe(90);
    expect(wheelFactor(100, 0)).toBeGreaterThan(1); // scroll down zooms out
    expect(wheelFactor(-100, 0)).toBeLessThan(1);
    expect(wheelFactor(3, 1)).toBeCloseTo(wheelFactor(48, 0));
    expect(wheelFactor(1e6, 0)).toBe(wheelFactor(200, 0));
  });

  it('describes a layer the way a person would name it', () => {
    expect(colorName('#d95926')).toBe('orange');
    expect(colorName('#3987e5')).toBe('blue');
    expect(colorName('#2fd39b')).toBe('green');
    expect(colorName('#ffd84d')).toBe('yellow');
    expect(colorName('nope')).toBe('nope');
    expect(describeStyle({ color: '#d55181', shape: 'triangle', size: 12, dash: false, hollow: true }, false)).toBe('pink #d55181 triangle, hollow');
    expect(describeStyle({ color: '#d95926', shape: 'circle', size: 8, dash: true, hollow: true }, true)).toBe('orange #d95926 dashed outline');
  });

  it('frames rows across RA = 0 without spinning around the sky', () => {
    const rows = [{ id: 'a', ra: 359.9, dec: 0 }, { id: 'b', ra: 0.1, dec: 0 }];
    const camera = frame(rows)!;
    expect(camera.ra < 0.01 || camera.ra > 359.99).toBe(true);
    expect(camera.fov).toBeCloseTo(0.3);
    expect(frame([{ id: 'x', ra: null, dec: null }])).toBeNull();
  });
});
