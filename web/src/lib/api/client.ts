// Browser side of scoop/server/app.py. Credentials stay on the server.
import type { Field } from '../field/types';

export interface ActionAcknowledgment {
  command_id: string;
  status: 'applied' | 'failed';
  error?: string;
}

export type ChatEvent =
  | { type: 'assistant'; id: string | null; text: string }
  | { type: 'assistant_delta'; id: string | null; text: string }
  | { type: 'tool_call'; id: string; name: string; args: unknown }
  | { type: 'tool_result'; id: string; name: string | null; status: 'success' | 'error'; summary: string;
      image?: string; code?: string; output?: string; error?: string }
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
  listFields(): Promise<{ key: string }[]>;
  getField(key: string): Promise<Field>;
  startJob(key: string, nRhalf?: number): Promise<JobView>;
  getSettings(): Promise<SettingsView>;
  listSources(): Promise<SourceInfo[]>;
  addSource(field: string, source: string): Promise<{ rows: number }>;
  saveSettings(changes: Partial<Record<'model' | 'base_url' | 'api_key' | 'ads_token', string>>): Promise<SettingsView>;
  getJob(id: string): Promise<JobView>;
  deleteFigures?(thread: string): Promise<void>;
}

// A data source the server knows; queryable ones can be added to a field on demand.
export interface SourceInfo { name: string; label: string; provenance: string; queryable: boolean }

// What the server tells the browser about its settings: never secret values.
export interface SettingsView {
  model: string | null;
  base_url: string | null;
  api_key_set: boolean;
  api_key_hint: string | null;
  ads_token_set: boolean;
  ads_token_hint: string | null;
  // Where each value comes from: saved in Settings, the server's environment, or unset.
  sources?: Record<'model' | 'base_url' | 'api_key' | 'ads_token', 'settings' | 'environment' | null>;
  chat?: boolean;
  literature?: boolean;
  problems?: string[];
}

// A background pipeline run (scoop/server/jobs.py).
export interface JobView {
  id: string;
  key: string;
  status: 'running' | 'done' | 'failed';
  elapsed_s: number;
  last: string | null;
  decisions: string[];
  error: string | null;
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
    },
    async listFields() {
      try {
        const response = await fetcher(`${base}/fields`);
        return response.ok ? (await response.json()).fields : [];
      } catch {
        return [];
      }
    },
    async listSources() {
      try {
        const response = await fetcher(`${base}/sources`);
        return response.ok ? (await response.json()).sources : [];
      } catch {
        return [];
      }
    },
    async addSource(field, source) {
      const response = await fetcher(`${base}/fields/${encodeURIComponent(field)}/sources/${encodeURIComponent(source)}`, { method: 'POST' });
      if (!response.ok) throw new Error(await errorText(response));
      return response.json();
    },
    async deleteFigures(thread) {
      await fetcher(`${base}/figures/${encodeURIComponent(thread)}`, { method: 'DELETE' }).catch(() => undefined);
    },
    async getSettings() {
      const response = await fetcher(`${base}/settings`);
      if (!response.ok) throw new Error(await errorText(response));
      return response.json();
    },
    async saveSettings(changes) {
      const response = await fetcher(`${base}/settings`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(changes)
      });
      if (!response.ok) throw new Error(await errorText(response));
      return response.json();
    },
    async startJob(key, nRhalf = 2) {
      const response = await fetcher(`${base}/jobs`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ key, n_rhalf: nRhalf })
      });
      if (!response.ok) throw new Error(await errorText(response));
      return response.json();
    },
    async getJob(id) {
      const response = await fetcher(`${base}/jobs/${encodeURIComponent(id)}`);
      if (!response.ok) throw new Error(await errorText(response));
      return response.json();
    },
    async getField(key) {
      const response = await fetcher(`${base}/fields/${encodeURIComponent(key)}`);
      if (!response.ok) throw new Error(await errorText(response));
      return response.json();
    }
  };
}
