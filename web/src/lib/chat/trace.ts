// Developer traces of chat turns (see scoop/server/trace.py), for the Debug tab.

export interface TraceMessage {
  role: string;
  content: string;
  tool_calls?: { name: string; args: unknown; id?: string }[];
  tool_call_id?: string;
}

export interface TraceStep {
  kind: 'model' | 'tool';
  start_ms: number;
  duration_ms?: number;
  // model calls
  model?: string | null;
  sent_count?: number;
  sent?: TraceMessage[];
  reply?: TraceMessage;
  usage?: { input: number | null; output: number | null; cached: number | null };
  error?: string;
  // tool calls
  name?: string;
  args?: unknown;
  result?: string;
  status?: string;
  image?: boolean;
}

export interface Trace {
  request_id: string;
  thread_id: string;
  started_at: string;
  duration_ms: number;
  question: string;
  error: string | null;
  totals: { model_calls: number; input_tokens: number; output_tokens: number; cached_tokens: number; tool_calls: number; tool_failures: number };
  steps: TraceStep[];
}

export const seconds = (ms: number | undefined) => (ms === undefined ? '…' : ms < 1000 ? `${ms} ms` : `${(ms / 1000).toFixed(1)} s`);
export const tokens = (n: number | null | undefined) => (n == null ? '–' : n >= 10_000 ? `${Math.round(n / 1000)}k` : n >= 1000 ? `${(n / 1000).toFixed(1)}k` : String(n));

/** Where a step sits on its turn's timeline, as percentages for a waterfall bar. */
export function bar(step: TraceStep, total: number): { left: number; width: number } {
  const span = Math.max(total, 1);
  const left = Math.min(100, (step.start_ms / span) * 100);
  return { left, width: Math.max(0.5, Math.min(100 - left, ((step.duration_ms ?? span - step.start_ms) / span) * 100)) };
}

/** One line for a step: the model's tool calls or answer, or the tool's name. */
export function stepTitle(step: TraceStep): string {
  if (step.kind === 'tool') return step.name ?? 'tool';
  const calls = step.reply?.tool_calls?.map((c) => c.name) ?? [];
  return calls.length ? `model → ${calls.join(', ')}` : step.error ? 'model (failed)' : 'model → answer';
}

/** Share of input tokens served from the prompt cache. */
export function cacheShare(trace: Trace): number | null {
  return trace.totals.input_tokens ? trace.totals.cached_tokens / trace.totals.input_tokens : null;
}
