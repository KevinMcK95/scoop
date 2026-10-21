import { describe, expect, it } from 'vitest';
import { contextLabel, searchModels } from './models';

const model = (id: string, name: string | null = null) => ({ id, name, context_length: null });
const MODELS = [model('anthropic/claude-opus-5-5', 'Claude Opus 5.5'), model('openai/gpt-6-luna', 'GPT-6 Luna'),
  model('moonshotai/Kimi-K3'), model('mistralai/Mistral-Medium-3.5-128B')];

describe('searchModels', () => {
  it('matches every word, in the id or the name, ignoring case', () => {
    expect(searchModels(MODELS, 'opus').shown.map((m) => m.id)).toEqual(['anthropic/claude-opus-5-5']);
    expect(searchModels(MODELS, 'GPT luna').shown.map((m) => m.id)).toEqual(['openai/gpt-6-luna']);
    expect(searchModels(MODELS, 'gpt opus').total).toBe(0);
  });

  it('puts ids starting with the query first, then word starts', () => {
    const ids = searchModels(MODELS, 'm').shown.map((m) => m.id);
    expect(ids.slice(0, 2)).toEqual(['moonshotai/Kimi-K3', 'mistralai/Mistral-Medium-3.5-128B']);
  });

  it('shows everything for an empty query, up to the limit', () => {
    expect(searchModels(MODELS, '  ', 2)).toEqual({ shown: MODELS.slice(0, 2), total: 4 });
  });
});

describe('contextLabel', () => {
  it('rounds to k or M', () => {
    expect([contextLabel(131072), contextLabel(1_000_000), contextLabel(null)]).toEqual(['131k context', '1M context', '']);
  });
});
