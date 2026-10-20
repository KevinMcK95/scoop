import { describe, expect, it } from 'vitest';
import { httpBackend, parseEvents } from './client';

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

  it('sends settings as JSON (the server refuses anything else from a browser)', async () => {
    let seen: RequestInit | undefined;
    const backend = httpBackend('/api', async (_url, init) => { seen = init; return new Response('{"model":"m"}'); });
    await backend.saveSettings({ api_key: 'k' });
    expect(seen?.method).toBe('POST');
    expect((seen?.headers as Record<string, string>)['Content-Type']).toBe('application/json');
    expect(seen?.body).toBe('{"api_key":"k"}');
  });
});
