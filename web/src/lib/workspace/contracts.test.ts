import { describe, expect, it } from 'vitest';
import { captureSnapshot, demoReply, isViewCommand } from './contracts';
import { initialViewport } from './fixtures';
import { fixtureIdFromClick } from '../sky/aladin';

describe('workspace transport contracts', () => {
  it('captures independent source and paper selection without retaining mutable references', () => {
    const selection = { sourceId: 'fx-src-a7', publicationId: 'fx-pub-t8' };
    const viewport = { ...initialViewport };
    const snapshot = captureSnapshot(selection, viewport);
    selection.sourceId = 'fx-src-k2';
    selection.publicationId = 'fx-pub-v3';
    viewport.ra = 42;
    expect(snapshot.selection).toEqual({ sourceId: 'fx-src-a7', publicationId: 'fx-pub-t8' });
    expect(snapshot.viewport.ra).toBe(initialViewport.ra);
  });

  it('supports either selection alone or neither', () => {
    for (const selection of [
      { sourceId: null, publicationId: null },
      { sourceId: 'fx-src-a7', publicationId: null },
      { sourceId: null, publicationId: 'fx-pub-t8' }
    ]) expect(captureSnapshot(selection, initialViewport).selection).toEqual(selection);
  });

  it('only accepts finite, bounded structured center commands', () => {
    expect(isViewCommand({ type: 'center', ra: 0, dec: -90, fov: 180 })).toBe(true);
    for (const value of [null, 'javascript:alert(1)', {},
      { type: 'eval', ra: 10, dec: 10, fov: 1 },
      { type: 'center', ra: NaN, dec: 10, fov: 1 },
      { type: 'center', ra: 360, dec: 10, fov: 1 },
      { type: 'center', ra: 10, dec: 91, fov: 1 },
      { type: 'center', ra: 10, dec: 10, fov: 0 },
      { type: 'center', ra: 10, dec: 10, fov: Infinity }
    ]) expect(isViewCommand(value)).toBe(false);
  });

  it('uses opaque fixture IDs from marker payloads, not labels or positions', () => {
    expect(fixtureIdFromClick({ data: { fixtureId: 'fx-src-k2', name: 'anything' } })).toBe('fx-src-k2');
    for (const value of [null, {}, { data: null }, { data: { name: 'Demo source A' } }, { data: { fixtureId: 7 } }]) {
      expect(fixtureIdFromClick(value)).toBeNull();
    }
  });

  it('labels replies as local demo context, not evidence', () => {
    const reply = demoReply(captureSnapshot({ sourceId: null, publicationId: 'fx-pub-t8' }, initialViewport));
    expect(reply).toContain('Local demo only');
    expect(reply).toContain('source: none');
    expect(reply).toContain('fx-pub-t8');
    expect(reply).toContain('No model, archive search, or scientific interpretation was run.');
  });
});
