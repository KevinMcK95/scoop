import type { ModelInfo } from '../api/client';

/** Models matching every word of the query (in the id or name), best first: an id that
 *  starts with the query, then a word of the id or name starting with it, then the rest. */
export function searchModels(models: ModelInfo[], query: string, limit = 50): { shown: ModelInfo[]; total: number } {
  const words = query.toLowerCase().split(/\s+/).filter(Boolean);
  const text = (m: ModelInfo) => `${m.id} ${m.name ?? ''}`.toLowerCase();
  const found = words.length ? models.filter((m) => words.every((w) => text(m).includes(w))) : models;
  const first = words[0] ?? '';
  const rank = (m: ModelInfo) => {
    const id = m.id.toLowerCase();
    if (!first || id.startsWith(first)) return 0;
    return text(m).split(/[\s/:._-]+/).some((part) => part.startsWith(first)) ? 1 : 2;
  };
  const sorted = found.map((m, i) => ({ m, i, r: rank(m) })).sort((a, b) => a.r - b.r || a.i - b.i).map((x) => x.m);
  return { shown: sorted.slice(0, limit), total: found.length };
}

/** "128k context" from a context length, or ''. */
export function contextLabel(length: number | null): string {
  if (!length) return '';
  return length >= 1_000_000 ? `${+(length / 1_000_000).toFixed(1)}M context` : `${Math.round(length / 1000)}k context`;
}
