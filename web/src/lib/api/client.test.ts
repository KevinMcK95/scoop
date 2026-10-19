import { describe, expect, it } from 'vitest';
import { httpBackend, parseEvents, type ChatEvent, type ChatRequest } from './client';
import { createWorkspace } from '../workspace/state.svelte';

function stream(...chunks: string[]) {
  return new ReadableStream<Uint8Array>({
    start(controller) {
      for (const chunk of chunks) controller.enqueue(new TextEncoder().encode(chunk));
      controller.close();
    }
  });
}

async function collect<T>(items: AsyncIterable<T>) {
  const out: T[] = [];
  for await (const item of items) out.push(item);
  return out;
}

const circle = { type: 'annotation.circle', ra: 10.68, dec: 41.27, radius: 1, label: '1 deg' };

function scriptedBackend(...turns: ChatEvent[][]) {
  const requests: ChatRequest[] = [];
  return {
    requests,
    backend: {
      health: async () => ({ chat: true, literature: true }),
      async *chat(request: ChatRequest) {
        requests.push(structuredClone(request));
        yield* turns.shift() ?? [];
      },
      searchLiterature: async (query: string) => ({
        service: 'ADS' as const, query, status: 'success', total_count: 1, error: null,
        papers: [{ ref: { archive: 'ADS', native_id: '2020ApJ...1A' }, title: 'A paper', authors: ['Doe, J.'],
          abstract: 'Abstract.', year: 2020, venue: 'ApJ', citation_count: 3, provenance: { access_url: 'https://ui.adsabs.harvard.edu/abs/2020ApJ...1A' } }]
      })
    }
  };
}

describe('server event transport', () => {
  it('parses events split across network chunks', async () => {
    const events = await collect(parseEvents(stream('data: {"type":"assi', 'stant","id":"a","text":"hi"}\n\ndata: {"type":"done"}\n\n')));
    expect(events).toEqual([{ type: 'assistant', id: 'a', text: 'hi' }, { type: 'done' }]);
  });

  it('turns HTTP errors into error events and reports an unreachable server as offline', async () => {
    const failing = httpBackend('/api', async () => new Response(JSON.stringify({ error: 'No model' }), { status: 503 }));
    expect(await collect(failing.chat({ thread_id: 'thread-01', message: 'hi', context: {} })))
      .toEqual([{ type: 'error', message: 'No model' }, { type: 'done' }]);
    const offline = httpBackend('/api', async () => { throw new TypeError('fetch failed'); });
    expect(await offline.health()).toEqual({ chat: false, literature: false });
  });
});

describe('live workspace', () => {
  it('queues agent view requests and reports the renderer outcome on the next turn', async () => {
    const { backend, requests } = scriptedBackend(
      [{ type: 'tool_call', id: 'call-1', name: 'update_sky_view', args: { command: circle } },
       { type: 'tool_result', id: 'call-1', name: 'update_sky_view', status: 'success', summary: 'Requested.' },
       { type: 'artifact', id: 'call-1', payload: { kind: 'view_request', status: 'requested', command: circle } },
       { type: 'assistant', id: 'm1', text: 'Drawing a circle.' }, { type: 'done' }],
      [{ type: 'done' }]
    );
    const workspace = createWorkspace(backend);
    await workspace.connect();
    workspace.draft = 'Circle M31';
    await workspace.send();
    expect(workspace.messages.map((message) => message.role)).toEqual(['user', 'tool', 'assistant']);
    expect(workspace.messages[1].status).toBe('success');
    expect(workspace.pendingView?.commandId).toBe('call-1');
    expect(requests[0].context.viewAcknowledgments).toEqual([]);
    expect(requests[0].context.sourceIds).toContain('fx-src-a7');

    workspace.acknowledgeView(workspace.pendingView!.sequence);
    workspace.draft = 'Did it work?';
    await workspace.send();
    expect(requests[1].context.viewAcknowledgments).toEqual([{ command_id: 'call-1', status: 'applied' }]);
    expect(requests[1].thread_id).toBe(requests[0].thread_id);
  });

  it('fails invalid agent commands immediately and shows ADS results from tools', async () => {
    const { backend, requests } = scriptedBackend(
      [{ type: 'artifact', id: 'call-2', payload: { kind: 'view_request', command: { type: 'sources.highlight', sourceIds: ['made-up'] } } },
       { type: 'done' }],
      [{ type: 'done' }]
    );
    const workspace = createWorkspace(backend);
    await workspace.connect();
    workspace.draft = 'Highlight it';
    await workspace.send();
    expect(workspace.pendingView).toBeNull();
    workspace.draft = 'Again';
    await workspace.send();
    expect(requests[1].context.viewAcknowledgments).toEqual([{ command_id: 'call-2', status: 'failed', error: 'Rejected by the browser workspace.' }]);

    await workspace.searchLiterature('abs:M31');
    expect(workspace.papers[0]).toMatchObject({ id: '2020ApJ...1A', origin: 'ads', year: '2020 · ApJ' });
    workspace.selectPublication('2020ApJ...1A');
    expect(workspace.selection.publicationId).toBe('2020ApJ...1A');
  });
});
