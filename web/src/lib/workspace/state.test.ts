import { describe, expect, it } from 'vitest';
import { createWorkspace } from './state.svelte';

describe('component-owned workspace', () => {
  it('shares renderer readiness within a workspace without leaking across pages', () => {
    const first = createWorkspace();
    const second = createWorkspace();
    expect(first.viewerPhase).toBe('loading');
    first.viewerPhase = 'ready';
    expect(first.viewerPhase).toBe('ready');
    expect(second.viewerPhase).toBe('loading');
    first.viewerPhase = 'error';
    expect(first.viewerPhase).toBe('error');
    expect(first.selection).toEqual({ sourceId: null, publicationId: null });
  });
  it('changes layer state only after acknowledgment and preserves records and selections', () => {
    const workspace = createWorkspace();
    workspace.selectSource('fx-src-a7');
    workspace.requestView({ type: 'layer.visibility', layerId: 'fixture-sources', visible: false });
    expect(workspace.layers[0].visible).toBe(true);
    workspace.acknowledgeView(workspace.pendingView!.sequence);
    expect(workspace.layers[0].visible).toBe(false);
    expect(workspace.layers[0].count).toBeGreaterThan(0);
    expect(workspace.selection.sourceId).toBe('fx-src-a7');
    expect(workspace.requestView({ type: 'layer.visibility', layerId: 'unknown', visible: true })).toBe(false);
  });

  it('validates known highlight IDs and records angular annotations after rendering', () => {
    const workspace = createWorkspace();
    expect(workspace.requestView({ type: 'sources.highlight', sourceIds: ['unknown'] })).toBe(false);
    const ids = ['fx-src-a7'];
    workspace.requestView({ type: 'sources.highlight', sourceIds: ids });
    ids.push('unknown');
    workspace.acknowledgeView(workspace.pendingView!.sequence);
    expect(workspace.highlightedSourceIds).toEqual(['fx-src-a7']);
    workspace.requestView({ type: 'annotation.circle', ra: 10, dec: 41, radius: 2 / 60, label: '2 arcmin' });
    workspace.acknowledgeView(workspace.pendingView!.sequence);
    expect(workspace.annotations).toHaveLength(1);
    workspace.requestView({ type: 'annotations.clear' });
    workspace.acknowledgeView(workspace.pendingView!.sequence, 'Failed');
    expect(workspace.annotations).toHaveLength(1);
    workspace.requestView({ type: 'annotations.clear' });
    workspace.acknowledgeView(workspace.pendingView!.sequence);
    expect(workspace.annotations).toHaveLength(0);
  });
  it('attaches the applied map state without rewriting old turns', () => {
    const workspace = createWorkspace();
    workspace.requestView({ type: 'annotation.circle', ra: 10, dec: 41, radius: 0.1, label: 'Demo circle' });
    workspace.acknowledgeView(workspace.pendingView!.sequence);
    workspace.draft = 'What is this circle?';
    workspace.send();
    workspace.requestView({ type: 'annotations.clear' });
    workspace.acknowledgeView(workspace.pendingView!.sequence);
    expect(workspace.messages[0].snapshot!.map?.annotations[0].ra).toBe(10);
    expect(workspace.messages[0].snapshot!.map?.annotations).toHaveLength(1);
    expect(workspace.annotations).toHaveLength(0);
  });

  it('keeps workspace instances isolated', () => {
    const first = createWorkspace();
    const second = createWorkspace();
    first.selectSource('fx-src-a7');
    first.selectPublication('fx-pub-r4');
    expect(second.selection).toEqual({ sourceId: null, publicationId: null });
    expect(second.messages).toHaveLength(0);
  });

  it('keeps publication selection when a source changes and rejects unknown IDs', () => {
    const workspace = createWorkspace();
    workspace.selectPublication('fx-pub-r4');
    workspace.selectSource('fx-src-k2');
    workspace.selectSource('not-a-fixture');
    workspace.selectPublication('not-a-fixture');
    expect(workspace.selection).toEqual({ sourceId: 'fx-src-k2', publicationId: 'fx-pub-r4' });
    workspace.selectSource(null);
    expect(workspace.selection.publicationId).toBe('fx-pub-r4');
  });

  it('sends trimmed text with a frozen-in-time snapshot and a labeled local reply', () => {
    const workspace = createWorkspace();
    workspace.selectSource('fx-src-a7');
    workspace.selectPublication('fx-pub-t8');
    workspace.draft = '  My question  ';
    workspace.send();
    workspace.selectSource('fx-src-p9');
    workspace.updateViewport({ ...workspace.viewport, ra: 20 });
    expect(workspace.messages).toHaveLength(2);
    expect(workspace.messages[0].text).toBe('My question');
    expect(workspace.messages[0].snapshot!.selection.sourceId).toBe('fx-src-a7');
    expect(workspace.messages[0].snapshot!.viewport.ra).not.toBe(20);
    expect(workspace.messages[1].role).toBe('demo');
    expect(workspace.draft).toBe('');
  });

  it('ignores empty sends and does not interpret chat text as a command', () => {
    const workspace = createWorkspace();
    workspace.draft = '  ';
    workspace.send();
    expect(workspace.messages).toHaveLength(0);
    workspace.draft = 'show M31';
    workspace.send();
    expect(workspace.pendingView).toBeNull();
    workspace.askAboutSelection();
    expect(workspace.draft).toContain('selection');
  });

  it('queues copied and validated view commands, including repeat centers', () => {
    const workspace = createWorkspace();
    const command = { type: 'center' as const, ra: 10, dec: 41, fov: 1 };
    workspace.requestView(command);
    const firstSequence = workspace.pendingView!.sequence;
    command.ra = 22;
    expect(workspace.pendingView!.command).toEqual({ type: 'center', ra: 10, dec: 41, fov: 1 });
    workspace.requestView(command);
    expect(workspace.viewRequests).toHaveLength(2);
    expect(workspace.viewRequests[1].sequence).toBeGreaterThan(firstSequence);
    expect(workspace.requestView({ ...command, fov: -1 })).toBe(false);
    workspace.acknowledgeView(firstSequence);
    expect(workspace.pendingView!.sequence).toBe(workspace.viewRequests[1].sequence);
    workspace.acknowledgeView(workspace.pendingView!.sequence, 'Renderer unavailable');
    expect(workspace.pendingView).toBeNull();
    expect(workspace.viewRequests[1].status).toBe('failed');
  });
});
