import { captureSnapshot, demoReply, isViewCommand } from './contracts';
import type { ChatMessage, CircleAnnotation, MapContext, MapLayer, Publication, SkyViewport, ViewCommand, ViewRequest, WorkspaceSelection } from './contracts';
import { initialViewport, publications, sources } from './fixtures';
import type { ADSPayload, Backend, ChatEvent, ViewAcknowledgment } from '../api/client';

export function adsPublications(payload: ADSPayload): Publication[] {
  return payload.papers.map((paper) => ({
    id: paper.ref.native_id,
    title: paper.title ?? paper.ref.native_id,
    authors: paper.authors.length > 3 ? `${paper.authors.slice(0, 3).join('; ')} et al.` : paper.authors.join('; '),
    year: [paper.year, paper.venue].filter(Boolean).join(' · '),
    summary: paper.abstract ?? 'Abstract unavailable.',
    origin: 'ads',
    url: paper.provenance.access_url
  }));
}

// Call once per page/component tree. No module-level mutable state (SSR safe).
export function createWorkspace(backend?: Backend) {
  let selection = $state<WorkspaceSelection>({ sourceId: null, publicationId: null });
  let viewport = $state<SkyViewport>({ ...initialViewport });
  let messages = $state<ChatMessage[]>([]);
  let draft = $state('');
  let viewRequests = $state<ViewRequest[]>([]);
  let highlightedSourceIds = $state<string[]>([]);
  let annotations = $state<CircleAnnotation[]>([]);
  let sourceVisible = $state(true);
  let annotationVisible = $state(true);
  let sequence = 0;
  let viewerPhase = $state<'loading' | 'ready' | 'error'>('loading');
  let services = $state({ chat: false, literature: false });
  let busy = $state(false);
  let papers = $state<Publication[]>([...publications]);
  let literatureNote = $state('Invented reading set · not ADS results or source evidence');
  // Agent view requests the renderer has resolved but the agent has not yet heard about.
  let unreported: ViewAcknowledgment[] = [];
  const threadId = globalThis.crypto.randomUUID();

  function mapContext(): MapContext { return {
    layers: [
      { id: 'fixture-sources', label: 'Source markers', origin: 'Illustrative fixtures', visible: sourceVisible, count: sources.length },
      { id: 'annotations', label: 'Angular annotations', origin: 'Local view actions', visible: annotationVisible, count: annotations.length }
    ], highlightedSourceIds, annotations
  }; }

  function push(message: Omit<ChatMessage, 'id'>) {
    messages.push({ id: ++sequence, ...message });
    return messages[messages.length - 1];
  }

  function requestView(command: unknown, commandId?: string) {
    if (!isViewCommand(command)) return false;
    if (command.type === 'sources.highlight' && command.sourceIds.some((id) => !sources.some((source) => source.id === id))) return false;
    const copied: ViewCommand = command.type === 'sources.highlight'
      ? { ...command, sourceIds: [...command.sourceIds] } : { ...command };
    viewRequests.push({ sequence: ++sequence, command: copied, status: 'pending', ...(commandId ? { commandId } : {}) });
    return true;
  }

  function showPapers(payload: ADSPayload) {
    if (payload.error) {
      literatureNote = `ADS ${payload.status}: ${payload.error.message}`;
      return;
    }
    papers = adsPublications(payload);
    literatureNote = `ADS · ${payload.query} · ${payload.papers.length} of ${payload.total_count ?? '?'} matches`;
  }

  function context() {
    const snapshot = captureSnapshot(selection, viewport, mapContext());
    const paper = papers.find((item) => item.id === selection.publicationId);
    return {
      ...snapshot,
      sourceIds: sources.map((source) => source.id),
      sources: sources.map(({ id, label, ra, dec, kind }) => ({ id, label, ra, dec, kind, fixture: true })),
      ...(paper ? { selectedPublication: { id: paper.id, title: paper.title, origin: paper.origin ?? 'fixture' } } : {}),
      viewAcknowledgments: unreported.splice(0)
    };
  }

  function handle(event: ChatEvent, tools: Map<string, ChatMessage>) {
    switch (event.type) {
      case 'assistant': push({ role: 'assistant', text: event.text }); break;
      case 'tool_call': tools.set(event.id, push({ role: 'tool', text: `${event.name} ${JSON.stringify(event.args)}`, status: 'running' })); break;
      case 'tool_result': {
        const message = tools.get(event.id);
        if (message) { message.status = event.status; message.text += `\n${event.summary}`; }
        break;
      }
      case 'artifact':
        if (event.payload.kind === 'view_request') {
          if (!requestView(event.payload.command, event.id))
            unreported.push({ command_id: event.id, status: 'failed', error: 'Rejected by the browser workspace.' });
        } else if (event.payload.service === 'ADS') showPapers(event.payload as unknown as ADSPayload);
        break;
      case 'error': push({ role: 'error', text: event.message }); break;
    }
  }

  async function sendLive(text: string) {
    const tools = new Map<string, ChatMessage>();
    busy = true;
    try {
      for await (const event of backend!.chat({ thread_id: threadId, message: text, context: context() })) handle(event, tools);
    } catch (error) {
      push({ role: 'error', text: `Connection lost: ${error instanceof Error ? error.message : error}` });
    } finally {
      for (const message of tools.values()) if (message.status === 'running') message.status = 'error';
      busy = false;
    }
  }

  return {
    get viewerPhase() { return viewerPhase; },
    set viewerPhase(value: 'loading' | 'ready' | 'error') { viewerPhase = value; },
    get selection() { return selection; },
    get viewport() { return viewport; },
    get messages() { return messages; },
    get draft() { return draft; },
    set draft(value: string) { draft = value; },
    get pendingView() { return viewRequests.find((request) => request.status === 'pending') ?? null; },
    get viewRequests() { return viewRequests; },
    get highlightedSourceIds() { return highlightedSourceIds; },
    get annotations() { return annotations; },
    get layers(): MapLayer[] { return mapContext().layers; },
    get services() { return services; },
    get busy() { return busy; },
    get papers() { return papers; },
    get literatureNote() { return literatureNote; },
    async connect() {
      if (backend) services = await backend.health();
    },
    acknowledgeView(id: number, error?: string) {
      const request = viewRequests.find((item) => item.sequence === id && item.status === 'pending');
      if (!request) return false;
      request.status = error ? 'failed' : 'applied';
      request.error = error;
      if (request.commandId) unreported.push({ command_id: request.commandId, status: request.status, ...(error ? { error } : {}) });
      if (!error) {
        const command = request.command;
        if (command.type === 'layer.visibility') {
          if (command.layerId === 'fixture-sources') sourceVisible = command.visible;
          else annotationVisible = command.visible;
        } else if (command.type === 'sources.highlight') highlightedSourceIds = [...command.sourceIds];
        else if (command.type === 'annotation.circle') annotations.push({ id, ra: command.ra, dec: command.dec, label: command.label, radius: command.radius });
        else if (command.type === 'annotations.clear') annotations = [];
      }
      return true;
    },
    selectSource(id: string | null) {
      if (id === null || sources.some((source) => source.id === id)) selection.sourceId = id;
    },
    selectPublication(id: string | null) {
      if (id === null || papers.some((paper) => paper.id === id)) selection.publicationId = id;
    },
    updateViewport(value: SkyViewport) { viewport = { ...value }; },
    requestView,
    askAboutSelection() { draft = 'What context is attached to my current selection?'; },
    async searchLiterature(query: string) {
      if (!backend || !services.literature || !query.trim()) return;
      literatureNote = `Searching ADS for ${query.trim()}…`;
      try { showPapers(await backend.searchLiterature(query.trim())); }
      catch (error) { literatureNote = `ADS search failed: ${error instanceof Error ? error.message : error}`; }
    },
    async send() {
      const text = draft.trim();
      if (!text || busy) return;
      const snapshot = captureSnapshot(selection, viewport, mapContext());
      push({ role: 'user', text, snapshot });
      draft = '';
      if (backend && services.chat) return sendLive(text);
      push({ role: 'demo', text: demoReply(snapshot), snapshot: captureSnapshot(selection, viewport, mapContext()) });
    }
  };
}

export type Workspace = ReturnType<typeof createWorkspace>;
