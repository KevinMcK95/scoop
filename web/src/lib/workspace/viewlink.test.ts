import { describe, expect, it } from 'vitest';
import { decodeView, encodeView } from './viewlink';
import { createWorkspace } from './state.svelte';
import { miniFieldAllOn } from '../field/fixtures';
import type { Scene } from './contracts';

const scene = (): Scene => ({ field: 'mini_1', survey: 'CDS/P/2MASS/color', viewport: { ra: 260.04, dec: 57.9, fov: 0.2 },
  map: { layers: miniFieldAllOn().layers, highlight: { table: 'gaia', ids: ['1004'], reason: 'QSO ✓', origin: 'agent' },
    annotations: [], selection: null, plot: null } });

describe('view links', () => {
  it('round-trips a view through a URL-safe fragment, without counts', () => {
    const link = encodeView(scene());
    expect(link).toMatch(/^view=[\w-]+$/);
    const back = decodeView(`#${link}`)!;
    expect(back.viewport).toEqual({ ra: 260.04, dec: 57.9, fov: 0.2 });
    expect(back.map.highlight?.reason).toBe('QSO ✓');
    expect(back.map.layers[0]).not.toHaveProperty('count');
  });

  it('rejects anything that is not a view', () => {
    for (const hash of ['', '#other=1', '#view=!!!', `#view=${btoa('{"v":2}')}`, '#view=e30']) expect(decodeView(hash)).toBeNull();
  });

  it('opens a link in a workspace: field, view and layers restored, counts recomputed', async () => {
    const field = miniFieldAllOn();
    const workspace = createWorkspace({
      health: async () => ({ chat: false, literature: false }), listFields: async () => [{ key: 'mini_1' }],
      getField: async () => field, chat: async function* () {}, searchLiterature: async () => { throw new Error(); },
      startJob: async () => { throw new Error(); }, getJob: async () => { throw new Error(); },
      getSettings: async () => { throw new Error(); }, saveSettings: async () => { throw new Error(); },
      listSources: async () => [], addSource: async () => { throw new Error(); }
    });
    await workspace.connect();
    expect(await workspace.openViewLink(`#${encodeView(scene())}`)).toBe(true);
    expect(workspace.camera).toMatchObject({ ra: 260.04, dec: 57.9, fov: 0.2 });
    expect(workspace.survey).toBe('CDS/P/2MASS/color');
    expect(workspace.map.highlight?.ids).toEqual(['1004']);
    expect(workspace.layers.find((l) => l.id === 'gaia:stars')?.count).toBe(2);
    expect(await workspace.openViewLink('#view=bad')).toBe(false);
  });
});
