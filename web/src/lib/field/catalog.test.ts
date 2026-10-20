import { describe, expect, it } from 'vitest';
import { catalog, groupLayers } from './catalog';
import { applyAction } from './actions';
import { miniField } from './fixtures';
import type { MapState } from './types';

describe('Add data', () => {
  it('offers the layer groups not on the map, matching every query word', () => {
    const field = miniField();
    const groups = (q: string) => catalog(field, field.layers, q).map((g) => g.group);
    expect(groups('')).toEqual(['Gaia DR3', 'Spectra', 'SIMBAD galaxies', 'Gaia QSO candidates (impure)']);
    expect(groups('spec')).toEqual(['Spectra']);
    expect(groups('simbad tap')).toEqual(['SIMBAD galaxies']); // provenance matches too
    expect(groups('jwst')).toEqual([]); // already on the map
    const gaia = catalog(field, field.layers, '').find((g) => g.group === 'Gaia DR3')!;
    expect(gaia.layers.map((l) => l.id)).toEqual(['gaia:stars', 'gaia:galaxies']);
    expect(gaia.count).toBe(3);
  });

  it('offers a single layer when the search names it', () => {
    const field = miniField();
    const [group] = catalog(field, field.layers, 'rr lyrae');
    expect(group.group).toBe('Gaia QSO candidates (impure)');
    expect(group.matching.map((l) => l.id)).toEqual(['gaia:rrlyrae']);
  });

  it('reaches a data source with no layers through an "All rows" entry', () => {
    const field = miniField();
    const layers = field.layers.filter((l) => l.table !== 'milliquas');
    expect(catalog(field, layers, 'milliquas')).toEqual([expect.objectContaining({ group: 'MILLIQUAS QSOs', count: 1, table: 'milliquas' })]);
    expect(catalog(field, layers, 'target')).toEqual([]);
  });

  it('offers archives the field has not queried, marked for querying', () => {
    const field = miniField();
    const queryable = [{ name: 'exoplanet_hosts', label: 'Confirmed planet hosts', provenance: 'NASA Exoplanet Archive' },
      { name: 'gaia', label: 'Gaia DR3 sources', provenance: 'Gaia' }];
    const entries = catalog(field, field.layers, 'planet', queryable);
    expect(entries).toEqual([expect.objectContaining({ group: 'Confirmed planet hosts', query: 'exoplanet_hosts' })]);
    // A data source already in the field is not offered for querying again.
    expect(catalog(field, field.layers, '', queryable).filter((g) => g.query).map((g) => g.query)).toEqual(['exoplanet_hosts']);
  });

  it('adds by showing, and taking off the map puts it back in the list', () => {
    const field = miniField();
    const state: MapState = { layers: field.layers, highlight: null, annotations: [], selection: null };
    const shown = applyAction({ type: 'layer.update', layerId: 'gaia:stars', visible: true }, field, state, 'user', () => 1).state;
    expect(shown.layers.find((l) => l.id === 'gaia:stars')).toMatchObject({ added: true, visible: true });
    expect(catalog(field, shown.layers).find((g) => g.group === 'Gaia DR3')!.layers.map((l) => l.id)).toEqual(['gaia:galaxies']);
    const off = applyAction({ type: 'layer.update', layerId: 'gaia:stars', added: false }, field, shown, 'user', () => 1).state;
    expect(off.layers.find((l) => l.id === 'gaia:stars')).toMatchObject({ added: false, visible: false });
    expect(groupLayers(off.layers.filter((l) => l.added)).map(([g]) => g)).toEqual(['HST/JWST footprints', 'Trusted QSOs', 'Mini']);
  });
});
