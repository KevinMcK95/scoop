import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { deleteConversation, loadConversations, MAX_CONVERSATIONS, saveConversation, titleOf } from './history';
import type { ChatMessage } from '../workspace/contracts';

function memoryStorage() {
  const data = new Map<string, string>();
  return { getItem: (k: string) => data.get(k) ?? null, setItem: (k: string, v: string) => void data.set(k, v),
    removeItem: (k: string) => void data.delete(k), data };
}
const ask = (text: string): ChatMessage[] => [{ id: 1, role: 'user', text }, { id: 2, role: 'assistant', text: 'ok' }];

describe('conversation history', () => {
  beforeEach(() => vi.stubGlobal('localStorage', memoryStorage()));
  afterEach(() => vi.unstubAllGlobals());

  it('saves newest first, replaces by id, and skips empty conversations', () => {
    saveConversation({ id: 'a', title: 'A', field: 'draco_1', updatedAt: 1, messages: ask('first') });
    saveConversation({ id: 'b', title: 'B', field: null, updatedAt: 2, messages: ask('second') });
    saveConversation({ id: 'a', title: 'A2', field: 'draco_1', updatedAt: 3, messages: ask('first again') });
    saveConversation({ id: 'c', title: 'C', field: null, updatedAt: 4, messages: [] });
    expect(loadConversations().map((c) => [c.id, c.title])).toEqual([['a', 'A2'], ['b', 'B']]);
    expect(deleteConversation('a').map((c) => c.id)).toEqual(['b']);
  });

  it('keeps at most the newest conversations', () => {
    for (let i = 0; i < MAX_CONVERSATIONS + 5; i++) saveConversation({ id: `c${i}`, title: '', field: null, updatedAt: i, messages: ask('q') });
    const kept = loadConversations();
    expect(kept).toHaveLength(MAX_CONVERSATIONS);
    expect(kept[0].id).toBe(`c${MAX_CONVERSATIONS + 4}`);
  });

  it('keeps run_python code and output but not images', () => {
    const messages: ChatMessage[] = [...ask('q'), { id: 3, role: 'tool', text: 'run_python', status: 'success',
      detail: { code: 'print(1)', output: '1', image: 'data:image/png;base64,AAAA' } }];
    saveConversation({ id: 'a', title: '', field: null, updatedAt: 1, messages });
    expect(loadConversations()[0].messages[2].detail).toEqual({ code: 'print(1)', output: '1' });
    expect(messages[2].detail?.image).toBeDefined();
  });

  it('works without storage and survives garbage', () => {
    vi.stubGlobal('localStorage', { getItem: () => { throw new Error('blocked'); }, setItem: () => { throw new Error('blocked'); } });
    expect(loadConversations()).toEqual([]);
    expect(() => saveConversation({ id: 'a', title: '', field: null, updatedAt: 1, messages: ask('q') })).not.toThrow();
    vi.stubGlobal('localStorage', { getItem: () => '{not json', setItem: () => {} });
    expect(loadConversations()).toEqual([]);
  });

  it('titles a conversation by its first question', () => {
    expect(titleOf(ask('Which  trusted QSOs\nare covered?'))).toBe('Which trusted QSOs are covered?');
    expect(titleOf(ask('x'.repeat(80)))).toHaveLength(58);
    expect(titleOf([])).toBe('New conversation');
  });
});
