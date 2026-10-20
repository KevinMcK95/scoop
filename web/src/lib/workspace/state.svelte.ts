import { applyAction, checkAction, circleCounts, circleLabel, clone, describeStyle, layerRows, regionCounts, regionLabel, rowsOf } from '../field/actions';
import type { Camera, Field, MapAction, MapState, Origin, Row, RowRef } from '../field/types';
import type { ADSPayload, ActionAcknowledgment, Backend, ChatEvent, JobView, SourceInfo } from '../api/client';
import { contextSummary } from '../chat/activity';
import { deleteConversation, loadConversations, saveConversation, titleOf, type Conversation } from '../chat/history';
import { DEFAULT_SURVEY, initialViewport } from './contracts';
import { decodeView, encodeView } from './viewlink';
import type { ActionRequest, ChatMessage, Publication, Scene, SkyViewport } from './contracts';

export function adsPublications(payload: ADSPayload): Publication[] {
  return payload.papers.map((paper) => ({
    id: paper.ref.native_id,
    title: paper.title ?? paper.ref.native_id,
    authors: paper.authors.length > 3 ? `${paper.authors.slice(0, 3).join('; ')} et al.` : paper.authors.join('; '),
    year: [paper.year, paper.venue].filter(Boolean).join(' · '),
    summary: paper.abstract ?? 'Abstract unavailable.',
    url: paper.provenance.access_url
  }));
}

const emptyMap = (): MapState => ({ layers: [], highlight: null, annotations: [], selection: null, plot: null });
const MAX_UNDO = 50;

// Call once per page/component tree. No module-level mutable state (SSR safe).
export function createWorkspace(backend?: Backend, options: { pollMs?: number } = {}) {
  const pollMs = options.pollMs ?? 2000;
  let jobs = $state<JobView[]>([]);
  let sources = $state<SourceInfo[]>([]);
  // Retrievals in flight, shown as a progress bar over the map.
  let tasks = $state<{ id: number; label: string }[]>([]);
  async function track<T>(label: string, work: () => Promise<T>): Promise<T> {
    const id = ++sequence;
    tasks = [...tasks, { id, label }];
    try { return await work(); } finally { tasks = tasks.filter((task) => task.id !== id); }
  }
  let field = $state<Field | null>(null);
  let fieldKeys = $state<string[]>([]);
  let fieldStatus = $state('No field loaded.');
  let map = $state<MapState>(emptyMap());
  let survey = $state(DEFAULT_SURVEY);
  let viewport = $state<SkyViewport>({ ...initialViewport });
  // Where the renderer should move next; the sequence makes repeats distinct.
  let camera = $state<(Camera & { sequence: number }) | null>(null);
  let requests = $state<ActionRequest[]>([]);
  let undoStack = $state<MapState[]>([]);
  let messages = $state<ChatMessage[]>([]);
  let draft = $state('');
  let viewerPhase = $state<'loading' | 'ready' | 'error'>('loading');
  let services = $state({ chat: false, literature: false });
  let busy = $state(false);
  let papers = $state<Publication[]>([]);
  let publicationId = $state<string | null>(null);
  let literatureNote = $state('Search ADS, or ask the agent to.');
  let sequence = 0;
  // Agent requests the renderer has resolved but the agent has not yet heard about.
  let unreported: ActionAcknowledgment[] = [];
  // Requests from the running turn; the answer's scene is captured once they resolve.
  // `changed`: the agent applied map actions this turn, so its answer gets a view to restore.
  let turn: { open: Set<number>; message: ChatMessage | null; done: boolean; changed: boolean } | null = null;
  // The assistant message being streamed, until its complete text arrives.
  let streaming: { id: string | null; message: ChatMessage } | null = null;
  let threadId: string = globalThis.crypto.randomUUID();
  let conversations = $state<Conversation[]>([]);

  function persist() {
    conversations = saveConversation({ id: threadId, title: titleOf(messages), field: field?.key ?? null,
      updatedAt: Date.now(), messages: clone(messages) });
  }

  function push(message: Omit<ChatMessage, 'id'>) {
    messages.push({ id: ++sequence, ...message });
    return messages[messages.length - 1];
  }

  function moveCamera(target: Camera) {
    camera = { ...target, sequence: ++sequence };
  }

  function scene(): Scene {
    return clone({ field: field?.key ?? null, viewport: { ra: viewport.ra, dec: viewport.dec, fov: viewport.fov }, survey, map });
  }

  function maybeCaptureScene() {
    if (turn?.done && turn.open.size === 0) {
      if (turn.message && turn.changed) { turn.message.scene = scene(); persist(); }
      turn = null;
    }
  }

  function report(commandId: string | undefined, status: 'applied' | 'failed', error?: string) {
    if (commandId) unreported.push({ command_id: commandId, status, ...(error ? { error } : {}) });
  }

  /** Validate a batch against the state it will apply to; queue it if every action fits. */
  function request(actions: MapAction[], origin: Origin, commandId?: string): string | null {
    let scratch = clone(map);
    for (const [i, action] of actions.entries()) {
      const problem = checkAction(action, field, scratch);
      if (problem) {
        report(commandId, 'failed', `action ${i} (${action?.type}): ${problem}`);
        return problem;
      }
      scratch = applyAction(action, field, scratch, origin, () => 0).state;
    }
    requests.push({ sequence: ++sequence, actions: clone(actions), origin, status: 'pending', ...(commandId ? { commandId } : {}) });
    if (origin === 'agent') turn?.open.add(sequence);
    return null;
  }

  /** Called by the renderer: apply a pending request, or fail it when the map can't show it. */
  function resolve(seq: number, error?: string) {
    const req = requests.find((item) => item.sequence === seq && item.status === 'pending');
    if (!req) return false;
    let next = map;
    let target: Camera | null = null;
    if (!error) {
      for (const action of req.actions) {
        const problem = checkAction(action, field, next);
        if (problem) { error = problem; break; }
        const result = applyAction(action, field, next, req.origin, () => ++sequence);
        next = result.state;
        target = result.camera ?? target;
      }
    }
    req.status = error ? 'failed' : 'applied';
    if (error) req.error = error;
    else {
      undoStack = [...undoStack.slice(-MAX_UNDO + 1), clone(map)];
      map = next;
      if (target) moveCamera(target);
    }
    report(req.commandId, req.status, error);
    if (turn?.open.has(seq) && req.status === 'applied') turn.changed = true;
    turn?.open.delete(seq);
    maybeCaptureScene();
    return true;
  }

  async function loadField(key: string, commandId?: string) {
    if (!backend) return;
    fieldStatus = `Loading ${key}…`;
    try {
      const loaded = await track(`Loading ${key}…`, () => backend.getField(key));
      field = loaded;
      map = { ...emptyMap(), layers: clone(loaded.layers) };
      undoStack = [];
      fieldStatus = `${loaded.target.name} · ${loaded.n_rhalf} r_half`;
      moveCamera({ ra: loaded.target.ra, dec: loaded.target.dec, fov: loaded.fov });
      report(commandId, 'applied');
    } catch (error) {
      fieldStatus = `Could not load ${key}: ${error instanceof Error ? error.message : error}`;
      report(commandId, 'failed', fieldStatus);
    }
  }

  /** Reload the field after it gained a table: keep the map as it is, add the new layers. */
  async function refreshField(key: string, commandId?: string) {
    if (!backend || field?.key !== key) return loadField(key, commandId);
    try {
      const loaded = await track(`Updating ${key}…`, () => backend.getField(key));
      const known = new Set(map.layers.map((layer) => layer.id));
      const counts = new Map(loaded.layers.map((layer) => [layer.id, layer.count]));
      field = loaded;
      map = { ...map, layers: [...map.layers.map((layer) => ({ ...layer, count: counts.get(layer.id) ?? layer.count })),
        ...clone(loaded.layers.filter((layer) => !known.has(layer.id)))] };
      report(commandId, 'applied');
    } catch (error) {
      report(commandId, 'failed', `Could not update ${key}: ${error instanceof Error ? error.message : error}`);
    }
  }

  /** Follow a pipeline run; load its field when done and tell the agent either way. */
  async function trackJob(id: string, key: string, commandId?: string) {
    if (!backend) return;
    for (;;) {
      let view: JobView;
      try { view = await backend.getJob(id); }
      catch (error) {
        report(commandId, 'failed', `Lost track of the run: ${error instanceof Error ? error.message : error}`);
        jobs = jobs.filter((job) => job.id !== id);
        return;
      }
      jobs = [...jobs.filter((job) => job.id !== id), view];
      if (view.status === 'done') {
        fieldKeys = (await backend.listFields()).map((item) => item.key);
        await loadField(key, commandId);
        return;
      }
      if (view.status === 'failed') {
        report(commandId, 'failed', `Pipeline failed for ${key}: ${view.error ?? 'unknown error'}`);
        return;
      }
      await new Promise((resolve) => setTimeout(resolve, pollMs));
    }
  }

  function showPapers(payload: ADSPayload) {
    if (payload.error) {
      literatureNote = `ADS ${payload.status}: ${payload.error.message}`;
      return;
    }
    papers = adsPublications(payload);
    literatureNote = `ADS · ${payload.query} · ${payload.papers.length} of ${payload.total_count ?? '?'} matches`;
  }

  function selectedRow(): (Row & { table: string }) | null {
    const ref = map.selection;
    const row = ref ? rowsOf(field, ref.table).find((item) => item.id === ref.id) : undefined;
    return row && ref ? { ...row, table: ref.table } : null;
  }

  /** What the agent sees each turn: summaries and ids, never whole tables. */
  function context() {
    const row = selectedRow();
    const paper = papers.find((item) => item.id === publicationId);
    return {
      field: field?.key ?? null,
      availableFields: fieldKeys,
      viewport: { ra: viewport.ra, dec: viewport.dec, fov: viewport.fov, status: viewport.status },
      survey,
      // Each layer's look in words, so "the orange triangles" can be matched to a layer.
      layers: map.layers.map(({ id, table, group, label, visible, added, origin, count, note, style }) =>
        ({ id, table, group, label, visible, added, origin, count,
           style: describeStyle(style, ['footprints', 'annotation'].includes(field?.tables[table]?.kind ?? '')),
           ...(note ? { note } : {}) })),
      highlight: map.highlight && { ...map.highlight, ids: map.highlight.ids.slice(0, 50) },
      annotations: map.annotations.map(({ shape, text, ra, dec, radius, rowId, origin }) => ({ shape, text, ra, dec, radius, rowId, origin })),
      // Shapes the user drew: what they mean by "here" or "this region".
      userRegions: map.annotations.filter((a) => a.origin === 'user' && (a.shape === 'circle' || a.shape === 'polygon')).map((a) =>
        a.shape === 'circle'
          ? { shape: 'circle', ra: a.ra, dec: a.dec, radius_arcmin: +((a.radius ?? 0) * 60).toPrecision(4), label: a.text,
              filter: [{ column: 'position', op: 'within', value: { ra: a.ra, dec: a.dec, radius: a.radius } }] }
          : { shape: a.points?.length === 4 ? 'box' : 'polygon', ra: a.ra, dec: a.dec, points: a.points, label: a.text,
              filter: [{ column: 'position', op: 'inside', value: { polygon: a.points } }] }),
      selection: row && Object.fromEntries(Object.entries(row).filter(([k]) => !['polygon', 's_region', '_links'].includes(k))),
      plot: map.plot ?? null,
      ...(paper ? { selectedPublication: { id: paper.id, title: paper.title } } : {}),
      mapAcknowledgments: unreported.splice(0)
    };
  }

  function handle(event: ChatEvent, tools: Map<string, ChatMessage>) {
    switch (event.type) {
      case 'assistant_delta':
        if (streaming && streaming.id === event.id) streaming.message.text += event.text;
        else {
          streaming = { id: event.id, message: push({ role: 'assistant', text: event.text, status: 'running' }) };
          if (turn) turn.message = streaming.message;
        }
        break;
      case 'assistant': {
        // The complete text replaces what streamed, so nothing is lost or doubled.
        const message = streaming && streaming.id === event.id ? streaming.message : push({ role: 'assistant', text: event.text });
        message.text = event.text;
        delete message.status;
        streaming = null;
        if (turn) turn.message = message;
        break;
      }
      // The chat records which tool ran and how it went; only run_python's code and output, and images, are shown.
      case 'tool_call': tools.set(event.id, push({ role: 'tool', text: event.name, status: 'running' })); break;
      case 'tool_result': {
        const message = tools.get(event.id);
        if (message) {
          message.status = event.status;
          const { code, output, error, image } = event;
          if (code || image) message.detail = { code, output, error, image };
        }
        break;
      }
      case 'artifact': {
        const payload = event.payload;
        if (payload.kind === 'map_actions' && Array.isArray(payload.actions)) request(payload.actions as MapAction[], 'agent', event.id);
        else if (payload.kind === 'field_load' && typeof payload.key === 'string') void loadField(payload.key, event.id);
        else if (payload.kind === 'field_refresh' && typeof payload.key === 'string') void refreshField(payload.key, event.id);
        else if (payload.kind === 'field_job' && typeof payload.job === 'string' && typeof payload.key === 'string')
          void trackJob(payload.job, payload.key, event.id);
        else if (payload.service === 'ADS') showPapers(payload as unknown as ADSPayload);
        break;
      }
      case 'error': push({ role: 'error', text: event.message }); break;
    }
  }

  let controller: AbortController | null = null;

  async function sendLive(text: string, sent: Record<string, unknown>) {
    const tools = new Map<string, ChatMessage>();
    controller = new AbortController();
    const { signal } = controller;
    busy = true;
    turn = { open: new Set(), message: null, done: false, changed: false };
    try {
      for await (const event of backend!.chat({ thread_id: threadId, message: text, context: sent }, signal)) {
        if (signal.aborted) break;
        handle(event, tools);
      }
    } catch (error) {
      if (!signal.aborted) push({ role: 'error', text: `Connection lost: ${error instanceof Error ? error.message : error}` });
    } finally {
      for (const message of tools.values()) if (message.status === 'running') message.status = 'error';
      // A stream cut off mid-answer keeps its text but is marked unfinished.
      if (streaming) { streaming.message.status = 'error'; streaming = null; }
      if (signal.aborted) push({ role: 'note', text: '⏹ Stopped.' });
      controller = null;
      busy = false;
      persist();
      if (turn) { turn.done = true; maybeCaptureScene(); }
    }
  }

  return {
    get field() { return field; },
    get fieldKeys() { return fieldKeys; },
    get fieldStatus() { return fieldStatus; },
    get map() { return map; },
    get layers() { return map.layers; },
    get survey() { return survey; },
    get camera() { return camera; },
    get viewerPhase() { return viewerPhase; },
    set viewerPhase(value: 'loading' | 'ready' | 'error') { viewerPhase = value; },
    get viewport() { return viewport; },
    get requests() { return requests; },
    get pending() { return requests.filter((item) => item.status === 'pending'); },
    get canUndo() { return undoStack.length > 0; },
    get selectedRow() { return selectedRow(); },
    get messages() { return messages; },
    get draft() { return draft; },
    set draft(value: string) { draft = value; },
    get services() { return services; },
    get busy() { return busy; },
    get papers() { return papers; },
    get publicationId() { return publicationId; },
    get literatureNote() { return literatureNote; },
    get jobs() { return jobs; },
    get sources() { return sources; },
    /** What is being retrieved right now (field loads, queries, searches, pipeline runs). */
    get progress() {
      return [...tasks.map((task) => task.label),
        ...jobs.filter((job) => job.status === 'running').map((job) => `Running ${job.key} · ${job.elapsed_s.toFixed(0)} s`)];
    },
    /** Query a data source the field lacks over its region, then add its layers. */
    async addSource(name: string) {
      if (!backend || !field) return 'No field loaded.';
      const key = field.key;
      const label = sources.find((s) => s.name === name)?.label ?? name;
      try {
        await track(`Querying ${label}…`, () => backend.addSource(key, name));
        await refreshField(key);
        return null;
      } catch (error) {
        return error instanceof Error ? error.message : String(error);
      }
    },
    /** Start a pipeline run from the interface (no agent needed). */
    async runField(key: string, nRhalf = 2) {
      if (!backend) return 'No server connected.';
      try {
        const view = await backend.startJob(key.trim(), nRhalf);
        jobs = [...jobs.filter((job) => job.id !== view.id), view];
        void trackJob(view.id, view.key);
        return null;
      } catch (error) {
        return error instanceof Error ? error.message : String(error);
      }
    },
    request,
    resolve,
    loadField,
    get backend() { return backend; },
    /** Re-read which services are on (after Settings change). */
    async refreshServices() {
      if (backend) services = await backend.health();
    },
    /** Stop the running turn: the request is aborted and the server cancels the agent run. */
    stop() {
      controller?.abort();
    },
    async connect() {
      conversations = loadConversations();
      if (!backend) return;
      services = await backend.health();
      sources = await backend.listSources();
      fieldKeys = (await backend.listFields()).map((item) => item.key);
      if (fieldKeys.length && !field) await loadField(fieldKeys[0]);
    },
    undo() {
      const previous = undoStack.at(-1);
      if (!previous) return;
      undoStack = undoStack.slice(0, -1);
      map = previous;
    },
    /** A "#view=…" fragment for the current map. */
    viewLink() {
      return encodeView(scene());
    },
    /** Restore a view from a link; false if the link is invalid or its field is unavailable. */
    async openViewLink(hash: string) {
      const saved = decodeView(hash);
      if (!saved) return false;
      await this.showScene({ id: 0, role: 'assistant', text: '', scene: saved });
      return !saved.field || field?.key === saved.field;
    },
    async showScene(message: ChatMessage) {
      const saved = message.scene;
      if (!saved) return;
      // A view from another field (an older conversation) needs that field first.
      if (saved.field && saved.field !== field?.key) {
        await loadField(saved.field);
        if (field?.key !== saved.field) return;
      }
      undoStack = [...undoStack.slice(-MAX_UNDO + 1), clone(map)];
      map = clone(saved.map);
      // Links carry no counts; recompute them for this field.
      map.layers = map.layers.map((layer) => ({ ...layer, count: layerRows(field, layer).length }));
      survey = saved.survey;
      moveCamera(saved.viewport);
    },
    get conversations() { return conversations; },
    get conversationId() { return threadId; },
    /** Start a fresh conversation (a new server thread); the current one stays in History. */
    newConversation() {
      if (busy) return;
      persist();
      threadId = globalThis.crypto.randomUUID();
      messages = [];
      unreported = [];
    },
    /** Reopen a saved conversation and continue its server thread. */
    openConversation(id: string) {
      if (busy || id === threadId) return;
      const saved = conversations.find((c) => c.id === id);
      if (!saved) return;
      persist();
      threadId = saved.id;
      messages = clone(saved.messages);
      sequence = Math.max(sequence, ...messages.map((m) => m.id));
      unreported = [];
    },
    deleteConversation(id: string) {
      conversations = deleteConversation(id);
      if (id === threadId && !busy) { threadId = globalThis.crypto.randomUUID(); messages = []; }
    },
    selectRow(ref: RowRef | null) {
      if (ref === null || rowsOf(field, ref.table).some((row) => row.id === ref.id)) map.selection = ref;
    },
    setSurvey(id: string) { survey = id; },
    /** Draw a user circle labelled with what it contains; returns why not, or null. */
    drawCircle(ra: number, dec: number, radiusArcmin: number) {
      if (!(radiusArcmin > 0 && radiusArcmin <= 600)) return 'radius must be between 0 and 600 arcmin';
      const radius = radiusArcmin / 60;
      const text = circleLabel(radiusArcmin, circleCounts(field, map, ra, dec, radius));
      return request([{ type: 'annotate', shape: 'circle', ra, dec, radius, text }], 'user');
    },
    /** Draw a user box (sky polygon) labelled with its size and what it contains. */
    drawBox(points: [number, number][], ra: number, dec: number, widthArcmin: number, heightArcmin: number) {
      const fmt = (v: number) => `${+v.toPrecision(3)}′`;
      return this.drawPolygon(points, ra, dec, `${fmt(widthArcmin)} × ${fmt(heightArcmin)}`);
    },
    /** Draw a user polygon labelled with what it contains. */
    drawPolygon(points: [number, number][], ra: number, dec: number, size = `${points.length}-sided`) {
      const counts = regionCounts(field, map, { column: 'position', op: 'inside', value: { polygon: points } });
      return request([{ type: 'annotate', shape: 'polygon', ra, dec, points, text: regionLabel(size, counts) }], 'user');
    },
    updateViewport(value: SkyViewport) { viewport = { ...value }; },
    selectPublication(id: string | null) {
      if (id === null || papers.some((paper) => paper.id === id)) publicationId = id;
    },
    askAboutSelection() { draft = 'What do we know about the selected object?'; },
    async searchLiterature(query: string) {
      if (!backend || !services.literature || !query.trim()) return;
      literatureNote = `Searching ADS for ${query.trim()}…`;
      try { showPapers(await track('Searching ADS…', () => backend.searchLiterature(query.trim()))); }
      catch (error) { literatureNote = `ADS search failed: ${error instanceof Error ? error.message : error}`; }
    },
    async send() {
      const text = draft.trim();
      if (!text || busy) return;
      draft = '';
      if (!backend || !services.chat) {
        push({ role: 'user', text });
        push({ role: 'note', text: 'No agent is connected. Start `python -m scoop.server` with SCOOP_MODEL set.' });
        return;
      }
      const sent = context();
      push({ role: 'user', text, contextNote: contextSummary(sent) });
      return sendLive(text, sent);
    }
  };
}

export type Workspace = ReturnType<typeof createWorkspace>;
