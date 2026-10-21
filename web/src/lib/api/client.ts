// Browser side of scoop/server/app.py. Credentials stay on the server.
import type { Trace } from '../chat/trace';
import type { Field } from '../field/types';

export interface ActionAcknowledgment {
  command_id: string;
  status: 'applied' | 'failed';
  error?: string;
}

// What a conversation may spend, has spent, and has left (scoop/cost.py).
export interface BudgetView {
  thread_id: string;
  budget_usd: number;
  spent_usd: number;
  remaining_usd: number;
  turns: number;
}

export interface BudgetReport {
  budget: BudgetView | null;
  default_budget_usd: number;
  model: string;
  // False when the server has no price for the model: turns cannot be costed.
  priced: boolean;
}

// What a turn is expected to cost ('estimate', before it runs) and what it did
// cost ('actual', once its tokens are known). `note` is the line to show.
export interface CostEvent {
  type: 'cost';
  phase: 'estimate' | 'actual';
  request_id: string;
  model: string;
  note: string;
  budget: BudgetView | null;
  estimated_cost_usd?: number | null;
  actual_cost_usd?: number | null;
  budget_usd?: number;
  budget_scope?: 'conversation' | 'default';
  within_budget?: boolean;
  priced?: boolean;
}

export type ChatEvent =
  | { type: 'assistant'; id: string | null; text: string }
  | { type: 'assistant_delta'; id: string | null; text: string }
  | { type: 'tool_call'; id: string; name: string; args: unknown }
  | { type: 'tool_result'; id: string; name: string | null; status: 'success' | 'error'; summary: string;
      image?: string; code?: string; output?: string; error?: string }
  | { type: 'artifact'; id: string; payload: Record<string, unknown> }
  | CostEvent
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
  addSource(field: string, source: string, options?: Record<string, number>): Promise<{ rows: number }>;
  saveSettings(changes: Partial<Record<SettingName, string>>): Promise<SettingsView>;
  // The models an endpoint offers (the saved one when baseUrl is omitted); optional for test doubles.
  listModels?(baseUrl?: string): Promise<ModelInfo[]>;
  getJob(id: string): Promise<JobView>;
  // Budgets: present whenever the server supports them (a test double may leave them out).
  getBudget?(thread: string): Promise<BudgetReport>;
  setBudget?(thread: string, budgetUsd: number): Promise<BudgetView>;
  deleteFigures?(thread: string): Promise<void>;
  getTraces?(thread: string): Promise<Trace[]>;
  deleteTraces?(thread: string): Promise<void>;
}

// A data source the server knows; queryable ones can be added to a field on demand.
// A number the user can set before a data source's query (e.g. a magnitude limit).
export interface SourceOption { name: string; label: string; default: number; min: number; max: number; step: number; help: string }
export interface SourceInfo { name: string; label: string; provenance: string; queryable: boolean; options?: SourceOption[] }

// One model an OpenAI-compatible endpoint lists at GET /models.
export interface ModelInfo { id: string; name: string | null; context_length: number | null }

export type SettingName = 'model' | 'base_url' | 'api_key' | 'ads_token' | 'kernel' | 'kernel_cwd';

// What the server tells the browser about its settings: never secret values.
export interface SettingsView {
  model: string | null;
  base_url: string | null;
  api_key_set: boolean;
  api_key_hint: string | null;
  ads_token_set: boolean;
  ads_token_hint: string | null;
  // The Jupyter kernel run_kernel uses (null: off), its working directory, and the installed kernels.
  kernel?: string | null;
  kernel_cwd?: string | null;
  kernels?: { name: string; display_name: string }[];
  // Where each value comes from: saved in Settings, the server's environment, or unset.
  sources?: Partial<Record<SettingName, 'settings' | 'environment' | null>>;
  chat?: boolean;
  kernel_on?: boolean;
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
    async addSource(field, source, options) {
      const response = await fetcher(`${base}/fields/${encodeURIComponent(field)}/sources/${encodeURIComponent(source)}`,
        options ? { method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify({ options }) } : { method: 'POST' });
      if (!response.ok) throw new Error(await errorText(response));
      return response.json();
    },
    async getTraces(thread) {
      const response = await fetcher(`${base}/traces/${encodeURIComponent(thread)}`);
      if (!response.ok) throw new Error(await errorText(response));
      return (await response.json()).traces;
    },
    async deleteTraces(thread) {
      await fetcher(`${base}/traces/${encodeURIComponent(thread)}`, { method: 'DELETE' }).catch(() => undefined);
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
    async listModels(baseUrl) {
      const query = baseUrl ? `?base_url=${encodeURIComponent(baseUrl)}` : '';
      const response = await fetcher(`${base}/models${query}`);
      if (!response.ok) throw new Error(await errorText(response));
      return (await response.json()).models;
    },
    async startJob(key, nRhalf = 2) {
      const response = await fetcher(`${base}/jobs`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ key, n_rhalf: nRhalf })
      });
      if (!response.ok) throw new Error(await errorText(response));
      return response.json();
    },
    async getBudget(thread) {
      const response = await fetcher(`${base}/budget/${encodeURIComponent(thread)}`);
      if (!response.ok) throw new Error(await errorText(response));
      return response.json();
    },
    async setBudget(thread, budgetUsd) {
      const response = await fetcher(`${base}/budget/${encodeURIComponent(thread)}`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ budget_usd: budgetUsd })
      });
      if (!response.ok) throw new Error(await errorText(response));
      return (await response.json()).budget;
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
