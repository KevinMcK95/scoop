// Browser side of scoop/server/app.py. Credentials stay on the server.

export interface ViewAcknowledgment {
  command_id: string;
  status: 'applied' | 'failed';
  error?: string;
}

export type ChatEvent =
  | { type: 'assistant'; id: string | null; text: string }
  | { type: 'tool_call'; id: string; name: string; args: unknown }
  | { type: 'tool_result'; id: string; name: string | null; status: 'success' | 'error'; summary: string }
  | { type: 'artifact'; id: string; payload: Record<string, unknown> }
  | { type: 'error'; message: string }
  | { type: 'done'; request_id?: string };

export interface ChatRequest {
  thread_id: string;
  message: string;
  context: Record<string, unknown>;
}

// Subset of scoop/literature/serialization.py:result_payload used by the panel.
export interface ADSPaper {
  ref: { archive: string; native_id: string };
  title: string | null;
  authors: string[];
  abstract: string | null;
  year: number | null;
  venue: string | null;
  citation_count: number | null;
  provenance: { access_url: string | null };
}

export interface ADSPayload {
  service: 'ADS';
  query: string;
  status: string;
  papers: ADSPaper[];
  total_count: number | null;
  error: { kind: string; message: string } | null;
}

export interface Backend {
  health(): Promise<{ chat: boolean; literature: boolean }>;
  chat(request: ChatRequest, signal?: AbortSignal): AsyncIterable<ChatEvent>;
  searchLiterature(query: string): Promise<ADSPayload>;
}

export async function* parseEvents(body: ReadableStream<Uint8Array>): AsyncGenerator<ChatEvent> {
  const reader = body.getReader();
  const decoder = new TextDecoder();
  let buffer = '';
  for (;;) {
    const { value, done } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    let end;
    while ((end = buffer.indexOf('\n\n')) >= 0) {
      const frame = buffer.slice(0, end);
      buffer = buffer.slice(end + 2);
      const data = frame.split('\n').filter((line) => line.startsWith('data: ')).map((line) => line.slice(6)).join('\n');
      if (data) yield JSON.parse(data) as ChatEvent;
    }
  }
}

async function errorText(response: Response): Promise<string> {
  try { return (await response.json()).error ?? response.statusText; } catch { return response.statusText; }
}

export function httpBackend(base = '/api', fetcher: typeof fetch = fetch): Backend {
  return {
    async health() {
      try {
        const response = await fetcher(`${base}/health`);
        return response.ok ? await response.json() : { chat: false, literature: false };
      } catch {
        return { chat: false, literature: false };
      }
    },
    async *chat(request, signal) {
      const response = await fetcher(`${base}/chat`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(request), signal
      });
      if (!response.ok || !response.body) {
        yield { type: 'error', message: await errorText(response) };
        yield { type: 'done' };
        return;
      }
      yield* parseEvents(response.body);
    },
    async searchLiterature(query) {
      const response = await fetcher(`${base}/literature/search`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ query, page_size: 20 })
      });
      if (!response.ok) throw new Error(await errorText(response));
      return response.json();
    }
  };
}
