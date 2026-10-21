import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { createWorkspace } from './state.svelte';
import { miniFieldAllOn as miniField } from '../field/fixtures';
import type { Backend, ChatEvent, ChatRequest, JobView } from '../api/client';

const jobView = (status: JobView['status'], key = 'leo_1'): JobView =>
  ({ id: `job-${key}`, key, status, elapsed_s: 3, last: null, decisions: ['Took the center from the LVDB.'],
     error: status === 'failed' ? "ValueError: LVDB key 'nope_1' not found" : null });

function backend(...turns: ChatEvent[][]) {
  const requests: ChatRequest[] = [];
  const fields = ['mini_1'];
  // Each job reports running once, then finishes (leo_1 succeeds; anything else fails).
  const polls = new Map<string, number>();
  const fake: Backend = {
    health: async () => ({ chat: true, literature: true }),
    listFields: async () => fields.map((key) => ({ key })),
    getField: async (key) => { if (!fields.includes(key)) throw new Error('No such field.'); return { ...miniField(), key }; },
    startJob: async (key) => jobView('running', key),
    listSources: async () => [{ name: 'exoplanet_hosts', label: 'Confirmed planet hosts', provenance: 'NASA Exoplanet Archive', queryable: true }],
    addSource: async () => ({ rows: 1 }),
    getSettings: async () => ({ model: 'm', base_url: null, api_key_set: false, api_key_hint: null, ads_token_set: false, ads_token_hint: null }),
    saveSettings: async () => ({ model: 'm', base_url: null, api_key_set: true, api_key_hint: '…abcd', ads_token_set: false, ads_token_hint: null }),
    getJob: async (id) => {
      const key = id.replace('job-', '');
      const n = (polls.get(id) ?? 0) + 1;
      polls.set(id, n);
      if (n < 2) return jobView('running', key);
      if (key === 'leo_1') { fields.push('leo_1'); return jobView('done', key); }
      return jobView('failed', key);
    },
    async *chat(request) { requests.push(structuredClone(request)); yield* turns.shift() ?? [{ type: 'done' }]; },
    searchLiterature: async (query) => ({ service: 'ADS', query, status: 'success', total_count: 1, error: null,
      papers: [{ ref: { archive: 'ADS', native_id: '2020ApJ...1A' }, title: 'A paper', authors: ['Doe, J.'],
        abstract: 'Abstract.', year: 2020, venue: 'ApJ', citation_count: 3,
        provenance: { access_url: 'https://ui.adsabs.harvard.edu/abs/2020ApJ...1A' } }] })
  };
  return { fake, requests };
}

async function ready(...turns: ChatEvent[][]) {
  const { fake, requests } = backend(...turns);
  const workspace = createWorkspace(fake, { pollMs: 1 });
  await workspace.connect();
  return { workspace, requests };
}

const resolveAll = (workspace: ReturnType<typeof createWorkspace>) =>
  workspace.pending.forEach((request) => workspace.resolve(request.sequence));

const agentActions = (id: string, actions: unknown[]): ChatEvent =>
  ({ type: 'artifact', id, payload: { kind: 'map_actions', status: 'requested', actions } as Record<string, unknown> });

describe('field workspace', () => {
  it('loads the first available field with its pipeline layers and frames it', async () => {
    const { workspace } = await ready();
    expect(workspace.field?.key).toBe('mini_1');
    expect(workspace.layers.find((l) => l.id === 'gaia:stars')?.count).toBe(2);
    expect(workspace.camera).toMatchObject({ ra: 260.05, dec: 57.92, fov: 0.8 });
  });

  it('a refreshed field drops the layers of tables it no longer has', async () => {
    const { fake } = backend([{ type: 'artifact', id: 'call-r', payload: { kind: 'field_refresh', key: 'mini_1', removed: ['milliquas'] } },
                              { type: 'done' }]);
    const workspace = createWorkspace(fake, { pollMs: 1 });
    await workspace.connect();
    await workspace.loadField('mini_1');
    expect(workspace.layers.some((l) => l.table === 'milliquas')).toBe(true);
    // The data agent removed an added table: the field comes back without it.
    fake.getField = async (key) => {
      const f = { ...miniField(), key };
      const { milliquas: _gone, ...tables } = f.tables;
      return { ...f, tables, layers: f.layers.filter((l) => l.table !== 'milliquas') };
    };
    workspace.draft = 'clear the extra catalogs';
    await workspace.send();
    await vi.waitFor(() => expect(workspace.layers.some((l) => l.table === 'milliquas')).toBe(false));
    expect(workspace.layers.some((l) => l.table === 'gaia')).toBe(true);  // the rest stays
  });

  it('a new search clears the field, layers and chat and starts a new conversation', async () => {
    const { workspace } = await ready();
    const before = workspace.conversationId;
    expect(workspace.layers.length).toBeGreaterThan(0);
    workspace.newSearch();
    expect(workspace.field).toBeNull();
    expect(workspace.layers).toEqual([]);
    expect(workspace.messages).toEqual([]);
    expect(workspace.conversationId).not.toBe(before);
    expect(workspace.camera).toMatchObject({ fov: 180 });  // the whole sky, not the old field
  });

  it('changes the map only when the renderer resolves a request, and undo restores it', async () => {
    const { workspace } = await ready();
    expect(workspace.request([{ type: 'layer.update', layerId: 'gaia:stars', visible: false }], 'user')).toBeNull();
    expect(workspace.layers.find((l) => l.id === 'gaia:stars')!.visible).toBe(true);
    resolveAll(workspace);
    expect(workspace.layers.find((l) => l.id === 'gaia:stars')!.visible).toBe(false);
    workspace.undo();
    expect(workspace.layers.find((l) => l.id === 'gaia:stars')!.visible).toBe(true);
    expect(workspace.canUndo).toBe(false);
  });

  it('applies a batch all-or-nothing and fails it when the map is unavailable', async () => {
    const { workspace } = await ready();
    expect(workspace.request([{ type: 'annotations.clear' }, { type: 'select', table: 'gaia', id: 'invented' }], 'user')).toMatch(/unknown gaia ids/);
    expect(workspace.pending).toHaveLength(0);
    workspace.request([{ type: 'highlight', table: 'gaia', ids: ['1004'], reason: 'QSO' }], 'user');
    workspace.resolve(workspace.pending[0].sequence, 'Sky viewer unavailable');
    expect(workspace.map.highlight).toBeNull();
    expect(workspace.requests.at(-1)).toMatchObject({ status: 'failed', error: 'Sky viewer unavailable' });
  });

  it('runs an agent turn: actions apply after rendering, the answer keeps its scene, the agent hears back', async () => {
    const { workspace, requests } = await ready(
      [{ type: 'tool_call', id: 'call-1', name: 'update_map', args: {} },
       { type: 'tool_result', id: 'call-1', name: 'update_map', status: 'success', summary: 'Requested.' },
       agentActions('call-1', [
         { type: 'layer.update', layerId: 'gaia:stars', visible: false },
         { type: 'highlight', table: 'gaia', ids: ['1004'], reason: 'Gaia-CRF3 QSO' },
         { type: 'annotate', shape: 'label', ra: 260.04, dec: 57.9, text: 'QSO', table: 'gaia', rowId: '1004' }]),
       { type: 'assistant', id: 'm1', text: 'One trusted QSO here.' }, { type: 'done' }],
      [{ type: 'done' }]
    );
    workspace.selectRow({ table: 'gaia', id: '1001' });
    workspace.draft = 'Any QSOs?';
    await workspace.send();
    const sent = requests[0].context as Record<string, any>;
    expect(sent.field).toBe('mini_1');
    expect(sent.selection).toMatchObject({ table: 'gaia', id: '1001', G: 18 });
    expect(sent.layers.find((l: { id: string }) => l.id === 'target:region')).toBeTruthy();
    // The agent learns how each layer looks, to match "the blue outlines" to a layer.
    expect(sent.layers.find((l: { id: string }) => l.id === 'fp:HST:ACS/WFC').style).toBe('blue #3987e5 outline');
    expect(sent.layers.find((l: { id: string }) => l.id === 'qso:crf').style).toBe('green #2fd39b rhomb');
    expect(JSON.stringify(sent)).not.toContain('polygon');

    // The chat keeps which tool ran and how it went, never its output.
    const step = workspace.messages.find((m) => m.role === 'tool')!;
    expect([step.text, step.status]).toEqual(['update_map', 'success']);
    expect(JSON.stringify(workspace.messages)).not.toContain('Requested.');
    const answer = workspace.messages.find((m) => m.role === 'assistant')!;
    expect(answer.scene).toBeUndefined();
    resolveAll(workspace);
    expect(workspace.map.highlight).toEqual({ table: 'gaia', ids: ['1004'], reason: 'Gaia-CRF3 QSO', origin: 'agent' });
    expect(answer.scene?.map.highlight?.ids).toEqual(['1004']);

    workspace.request([{ type: 'highlight', table: 'gaia', ids: [] }, { type: 'annotations.clear' }], 'user');
    resolveAll(workspace);
    expect(workspace.map.highlight).toBeNull();
    workspace.showScene(answer);
    expect(workspace.map.highlight?.ids).toEqual(['1004']);
    expect(workspace.map.annotations).toHaveLength(1);

    workspace.draft = 'Thanks';
    await workspace.send();
    expect((requests[1].context as Record<string, unknown>).mapAcknowledgments).toEqual([{ command_id: 'call-1', status: 'applied' }]);
  });

  it('reports agent actions the browser rejects, and loads fields the agent asks for', async () => {
    const { workspace, requests } = await ready(
      [agentActions('call-2', [{ type: 'highlight', table: 'gaia', ids: ['made-up'] }]),
       { type: 'artifact', id: 'call-3', payload: { kind: 'field_load', key: 'nope_1' } }, { type: 'done' }],
      [{ type: 'done' }]
    );
    workspace.draft = 'Highlight it';
    await workspace.send();
    await Promise.resolve();
    expect(workspace.pending).toHaveLength(0);
    workspace.draft = 'Again';
    await workspace.send();
    const acks = (requests[1].context as Record<string, any>).mapAcknowledgments;
    expect(acks[0]).toMatchObject({ command_id: 'call-2', status: 'failed' });
    expect(acks[1]).toMatchObject({ command_id: 'call-3', status: 'failed' });
    expect(workspace.field?.key).toBe('mini_1');
  });

  it('shows ADS papers and keeps workspaces isolated', async () => {
    const { workspace } = await ready();
    const other = createWorkspace();
    await workspace.searchLiterature('abs:Draco');
    expect(workspace.papers[0]).toMatchObject({ id: '2020ApJ...1A', year: '2020 · ApJ' });
    workspace.selectPublication('2020ApJ...1A');
    expect(workspace.publicationId).toBe('2020ApJ...1A');
    expect(other.papers).toEqual([]);
    expect(other.field).toBeNull();
    workspace.selectRow({ table: 'gaia', id: 'invented' });
    expect(workspace.map.selection).toBeNull();
  });

  it('sends circles the user drew to the agent as regions with a ready filter', async () => {
    const { workspace, requests } = await ready([{ type: 'done' }]);
    expect(workspace.drawCircle(260.02, 57.9, 0)).toMatch(/radius/);
    expect(workspace.drawCircle(260.02, 57.9, 0.36)).toBeNull();
    resolveAll(workspace);
    const note = workspace.map.annotations[0];
    expect(note).toMatchObject({ shape: 'circle', origin: 'user', radius: 0.006 });
    expect(note.text).toMatch(/^0\.36′ · \d+ rows/);
    workspace.draft = "What's in here?";
    await workspace.send();
    const regions = (requests[0].context as Record<string, any>).userRegions;
    expect(regions).toEqual([{ shape: 'circle', ra: 260.02, dec: 57.9, radius_arcmin: 0.36, label: note.text,
      filter: [{ column: 'position', op: 'within', value: { ra: 260.02, dec: 57.9, radius: 0.006 } }] }]);
  });

  it('grows a streamed answer in place and settles it on the complete text', async () => {
    const { workspace } = await ready(
      [{ type: 'assistant_delta', id: 'm1', text: 'Draco ' }, { type: 'assistant_delta', id: 'm1', text: 'is a' },
       { type: 'assistant', id: 'm1', text: 'Draco is a dwarf.' },
       { type: 'assistant_delta', id: 'm2', text: 'Cut ' }, { type: 'done' }]
    );
    workspace.draft = 'Tell me';
    await workspace.send();
    const answers = workspace.messages.filter((m) => m.role === 'assistant');
    expect(answers.map((m) => m.text)).toEqual(['Draco is a dwarf.', 'Cut ']);
    expect(answers[0].status).toBeUndefined();
    expect(answers[1].status).toBe('error');
  });

  it('follows a pipeline run the agent started and loads the field when it is done', async () => {
    const { workspace, requests } = await ready(
      [{ type: 'artifact', id: 'call-7', payload: { kind: 'field_job', job: 'job-leo_1', key: 'leo_1' } }, { type: 'done' }],
      [{ type: 'artifact', id: 'call-8', payload: { kind: 'field_job', job: 'job-nope_1', key: 'nope_1' } }, { type: 'done' }],
      [{ type: 'done' }]
    );
    workspace.draft = 'Do Leo I';
    await workspace.send();
    await vi.waitFor(() => expect(workspace.field?.key).toBe('leo_1'));
    expect(workspace.fieldKeys).toContain('leo_1');
    expect(workspace.jobs.find((j) => j.key === 'leo_1')?.status).toBe('done');
    workspace.draft = 'Now nope';
    await workspace.send();
    await vi.waitFor(() => expect(workspace.jobs.find((j) => j.key === 'nope_1')?.status).toBe('failed'));
    workspace.draft = 'Well?';
    await workspace.send();
    // Each outcome reaches the agent with the next message after it happened.
    const acks = (i: number) => (requests[i].context as Record<string, any>).mapAcknowledgments;
    expect(acks(1)).toEqual([{ command_id: 'call-7', status: 'applied' }]);
    expect(acks(2)).toEqual([
      { command_id: 'call-8', status: 'failed', error: "Pipeline failed for nope_1: ValueError: LVDB key 'nope_1' not found" }]);
    expect(workspace.field?.key).toBe('leo_1');
  });

  it('runs a field from the interface without the agent', async () => {
    const { workspace } = await ready();
    expect(await workspace.runField('leo_1')).toBeNull();
    await vi.waitFor(() => expect(workspace.field?.key).toBe('leo_1'));
    expect(await createWorkspace().runField('leo_1')).toBe('No server connected.');
  });

  it('sends boxes the user drew as regions with an inside filter', async () => {
    const { workspace, requests } = await ready([{ type: 'done' }]);
    const box: [number, number][] = [[260.005, 57.895], [260.035, 57.895], [260.035, 57.905], [260.005, 57.905]];
    expect(workspace.drawBox(box, 260.02, 57.9, 0.95, 0.6)).toBeNull();
    resolveAll(workspace);
    const note = workspace.map.annotations[0];
    expect(note).toMatchObject({ shape: 'polygon', origin: 'user', points: box });
    // Inside: Gaia 1001 and 1002 (stars), 1003 (galaxy candidate), and 1001's DESI spectrum.
    expect(note.text).toBe('0.95′ × 0.6′ · 4 rows in 3 layers');
    workspace.draft = 'What is in this box?';
    await workspace.send();
    const [region] = (requests[0].context as Record<string, any>).userRegions;
    expect(region).toMatchObject({ shape: 'box', points: box,
      filter: [{ column: 'position', op: 'inside', value: { polygon: box } }] });
  });

  it('stops a running turn: the request is aborted and the partial answer kept', async () => {
    const { fake } = backend();
    let aborted = false;
    fake.chat = async function* (_request, signal) {
      yield { type: 'tool_call', id: 'c1', name: 'describe_field', args: {} } as ChatEvent;
      yield { type: 'assistant_delta', id: 'm1', text: 'Draco has' } as ChatEvent;
      await new Promise<void>((resolve) => signal?.addEventListener('abort', () => { aborted = true; resolve(); }));
      yield { type: 'assistant', id: 'm1', text: 'never delivered' } as ChatEvent;
    };
    const workspace = createWorkspace(fake, { pollMs: 1 });
    await workspace.connect();
    workspace.draft = 'Tell me about Draco';
    const sending = workspace.send();
    await vi.waitFor(() => expect(workspace.messages.some((m) => m.text === 'Draco has')).toBe(true));
    expect(workspace.busy).toBe(true);
    workspace.stop();
    await sending;
    expect(aborted).toBe(true);
    expect(workspace.busy).toBe(false);
    const roles = workspace.messages.map((m) => [m.role, m.status ?? '']);
    expect(roles).toEqual([['user', ''], ['tool', 'error'], ['assistant', 'error'], ['note', '']]);
    expect(workspace.messages.at(-1)!.text).toBe('⏹ Stopped.');
  });

  it('offers a view to restore only for answers that changed the map', async () => {
    const { workspace } = await ready([{ type: 'assistant', id: 'm1', text: 'Hi! What would you like to explore?' }, { type: 'done' }]);
    workspace.draft = 'hi';
    await workspace.send();
    resolveAll(workspace);
    expect(workspace.messages.find((m) => m.role === 'assistant')!.scene).toBeUndefined();
  });

  describe('conversations', () => {
    beforeEach(() => {
      const data = new Map<string, string>();
      vi.stubGlobal('localStorage', { getItem: (k: string) => data.get(k) ?? null, setItem: (k: string, v: string) => void data.set(k, v) });
    });
    afterEach(() => vi.unstubAllGlobals());

    it('saves each finished turn, and new/open switch the server thread', async () => {
      const { workspace, requests } = await ready(
        [{ type: 'assistant', id: 'm1', text: 'One.' }, { type: 'done' }],
        [{ type: 'assistant', id: 'm2', text: 'Two.' }, { type: 'done' }],
        [{ type: 'assistant', id: 'm3', text: 'Three.' }, { type: 'done' }]
      );
      workspace.draft = 'First question';
      await workspace.send();
      const first = workspace.conversationId;
      expect(workspace.conversations.map((c) => c.title)).toEqual(['First question']);
      expect(workspace.messages[0].contextNote).toMatch(/^with mini_1/);

      workspace.newConversation();
      expect(workspace.messages).toEqual([]);
      workspace.draft = 'Second question';
      await workspace.send();
      expect(requests[1].thread_id).not.toBe(requests[0].thread_id);
      expect(workspace.conversations.map((c) => c.title)).toEqual(['Second question', 'First question']);

      workspace.openConversation(first);
      expect(workspace.messages.map((m) => m.text)).toEqual(['First question', 'One.']);
      workspace.draft = 'Follow-up';
      await workspace.send();
      expect(requests[2].thread_id).toBe(requests[0].thread_id);
      expect(createWorkspace().conversations).toEqual([]); // loaded on connect, not before
    });

    it('restores a view saved on another field by loading that field first', async () => {
      const { workspace } = await ready();
      const view = { role: 'assistant' as const, id: 9, text: 'x', scene: { field: 'leo_1', survey: 'CDS/P/DSS2/color',
        viewport: { ra: 1, dec: 2, fov: 0.5 }, map: { layers: [], highlight: null, annotations: [], selection: null } } };
      await workspace.showScene(view); // leo_1 isn't on this server: nothing changes
      expect(workspace.field?.key).toBe('mini_1');
      await workspace.showScene({ ...view, scene: { ...view.scene, field: 'mini_1' } });
      expect(workspace.camera).toMatchObject({ ra: 1, dec: 2, fov: 0.5 });
    });
  });

  it('sends a drawn polygon as a polygon region', async () => {
    const { workspace, requests } = await ready([{ type: 'done' }]);
    const tri: [number, number][] = [[260.0, 57.89], [260.06, 57.89], [260.03, 57.92]];
    expect(workspace.drawPolygon(tri, 260.03, 57.9)).toBeNull();
    resolveAll(workspace);
    expect(workspace.map.annotations[0].text).toMatch(/^3-sided · \d+ rows? in \d+ layers?$/);
    workspace.draft = 'What is in here?';
    await workspace.send();
    expect((requests[0].context as Record<string, any>).userRegions[0]).toMatchObject({ shape: 'polygon', points: tri });
  });

  it('adds a queried data source without resetting the map, showing progress meanwhile', async () => {
    const { fake } = backend();
    let release!: () => void;
    const withHosts = { ...miniField(), key: 'mini_1' };
    withHosts.tables = { ...withHosts.tables, exoplanet_hosts: { label: 'Confirmed planet hosts', provenance: 'NASA', kind: 'catalog',
      columns: ['ra', 'dec'], rows: [{ id: 'H1', ra: 260.03, dec: 57.9 }] } };
    withHosts.layers = [...withHosts.layers, { id: 'exoplanets:hosts', table: 'exoplanet_hosts', filter: [], group: 'Exoplanet hosts',
      label: 'Confirmed planet hosts', style: { color: '#f0d264', shape: 'rhomb', size: 14, dash: false, hollow: true },
      visible: true, added: true, origin: 'pipeline', note: null, count: 1 }];
    let calls = 0;
    fake.getField = async () => (++calls === 1 ? miniField() : withHosts);
    fake.addSource = () => new Promise((resolve) => { release = () => resolve({ rows: 1 }); });
    const workspace = createWorkspace(fake, { pollMs: 1 });
    await workspace.connect();
    workspace.request([{ type: 'highlight', table: 'gaia', ids: ['1004'] }], 'user');
    resolveAll(workspace);
    const adding = workspace.addSource('exoplanet_hosts');
    await vi.waitFor(() => expect(workspace.progress).toEqual(['Querying Confirmed planet hosts…']));
    release();
    expect(await adding).toBeNull();
    expect(workspace.progress).toEqual([]);
    expect(workspace.field?.tables.exoplanet_hosts.rows).toHaveLength(1);
    expect(workspace.layers.at(-1)?.id).toBe('exoplanets:hosts');
    expect(workspace.map.highlight?.ids).toEqual(['1004']); // the view was kept
  });

  it('reports cost and what is left once an optional budget is set', async () => {
    const cost = (phase: 'estimate' | 'actual', note: string, remaining: number): ChatEvent =>
      ({ type: 'cost', phase, note, request_id: 'r1', model: 'claude-opus-5-5',
         budget: { thread_id: 't', budget_usd: 5, spent_usd: 5 - remaining, remaining_usd: remaining, turns: 1 } });
    const { fake, requests } = backend([
      cost('estimate', 'Estimated cost $0.0400 of $5.0000 left in this conversation', 5),
      { type: 'assistant', id: 'a1', text: 'Draco is a dwarf spheroidal.' },
      cost('actual', 'This command cost $0.0500 · $4.9500 left of this conversation\'s $5.00 budget', 4.95),
      { type: 'done' }
    ]);
    const budgets: number[] = [];
    fake.getBudget = async () => ({ budget: null, default_budget_usd: 1, model: 'claude-opus-5-5', priced: true });
    fake.setBudget = async (_thread, usd) => {
      budgets.push(usd);
      return { thread_id: 't', budget_usd: usd, spent_usd: 0, remaining_usd: usd, turns: 0 };
    };
    const workspace = createWorkspace(fake, { pollMs: 1 });
    await workspace.connect();

    // A budget is optional: with none, the question goes straight to the agent.
    expect(workspace.budget).toBeNull();

    expect(await workspace.setBudget(5)).toBeNull();
    expect(budgets).toEqual([5]);
    workspace.draft = 'What is Draco?';
    await workspace.send();
    expect(requests).toHaveLength(1);

    // One note per turn: the estimate, replaced by what the command actually cost.
    const notes = workspace.messages.filter((m) => m.role === 'note');
    expect(notes).toHaveLength(1);
    expect(notes[0].text).toMatch(/cost \$0.0500 · \$4.9500 left/);
    expect(workspace.budget?.remaining_usd).toBe(4.95);
  });

  it('says once per conversation that an unpriced model cannot be costed', async () => {
    const note = 'No price is known for some/model, so this command cannot be costed.';
    const turn = (id: string): ChatEvent[] => [
      { type: 'cost', phase: 'estimate', note, request_id: id, model: 'some/model', priced: false } as ChatEvent,
      { type: 'assistant', id, text: 'ok' }, { type: 'done' }];
    const { fake } = backend(turn('a1'), turn('a2'));
    fake.getBudget = async () => ({ budget: null, default_budget_usd: 1, model: 'some/model', priced: false });
    fake.setBudget = async (_thread, usd) => ({ thread_id: 't', budget_usd: usd, spent_usd: 0, remaining_usd: usd, turns: 0 });
    const workspace = createWorkspace(fake, { pollMs: 1 });
    await workspace.connect();
    await workspace.setBudget(1);
    for (const q of ['first?', 'second?']) {
      workspace.draft = q;
      await workspace.send();
    }
    expect(workspace.messages.filter((m) => m.role === 'note' && m.text === note)).toHaveLength(1);
    expect(workspace.messages.filter((m) => m.role === 'assistant')).toHaveLength(2);
  });

  it('passes a data source\'s options (e.g. a magnitude limit) to its query', async () => {
    const { fake } = backend();
    const sent: unknown[] = [];
    fake.addSource = async (_field, source, options) => { sent.push([source, options]); return { rows: 3 }; };
    const workspace = createWorkspace(fake, { pollMs: 1 });
    await workspace.connect();
    resolveAll(workspace);
    expect(await workspace.addSource('tic', { tmag_max: 13 })).toBeNull();
    expect(await workspace.addSource('exoplanet_hosts')).toBeNull();
    expect(sent).toEqual([['tic', { tmag_max: 13 }], ['exoplanet_hosts', undefined]]);
  });

  it('says so instead of pretending when no agent is connected', async () => {
    const workspace = createWorkspace();
    workspace.draft = 'Hello';
    await workspace.send();
    expect(workspace.messages.map((m) => m.role)).toEqual(['user', 'note']);
  });
});
