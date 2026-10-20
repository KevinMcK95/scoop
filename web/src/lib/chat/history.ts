// Past conversations, kept in this browser only (a per-viewer convenience).
// Storage can be missing, full or blocked; every access is guarded, and the
// workspace works the same without it.
import type { ChatMessage } from '../workspace/contracts';

export interface Conversation {
  id: string; // also the server's thread id
  title: string;
  field: string | null;
  updatedAt: number;
  messages: ChatMessage[];
}

const KEY = 'scoop.conversations.v1';
export const MAX_CONVERSATIONS = 30;
export const MAX_MESSAGES = 200;

function storage(): Storage | null {
  try {
    return globalThis.localStorage ?? null;
  } catch {
    return null;
  }
}

export function loadConversations(): Conversation[] {
  try {
    const parsed = JSON.parse(storage()?.getItem(KEY) ?? '[]');
    return Array.isArray(parsed) ? parsed.filter((c) => c && typeof c.id === 'string' && Array.isArray(c.messages)) : [];
  } catch {
    return [];
  }
}

function write(conversations: Conversation[]): void {
  try {
    storage()?.setItem(KEY, JSON.stringify(conversations));
  } catch {
    // Full or blocked: drop the oldest half and try once more, else give up quietly.
    try { storage()?.setItem(KEY, JSON.stringify(conversations.slice(0, Math.ceil(conversations.length / 2)))); } catch { /* not saved */ }
  }
}

/** Save (or replace) one conversation; newest first, capped. Empty ones aren't kept. */
export function saveConversation(conversation: Conversation): Conversation[] {
  const rest = loadConversations().filter((c) => c.id !== conversation.id);
  if (!conversation.messages.some((m) => m.role === 'user')) return rest;
  // Figures stored by the server are short URLs and are kept; inline images are too big for storage.
  const messages = conversation.messages.slice(-MAX_MESSAGES).map((m) => m.detail?.image?.startsWith('data:') ? { ...m, detail: { ...m.detail, image: undefined } } : m);
  const kept = { ...conversation, messages };
  const all = [kept, ...rest].sort((a, b) => b.updatedAt - a.updatedAt).slice(0, MAX_CONVERSATIONS);
  write(all);
  return all;
}

export function deleteConversation(id: string): Conversation[] {
  const rest = loadConversations().filter((c) => c.id !== id);
  write(rest);
  return rest;
}

/** A title from the first question, trimmed to a line. */
export function titleOf(messages: ChatMessage[]): string {
  const first = messages.find((m) => m.role === 'user')?.text.trim().replace(/\s+/g, ' ') ?? 'New conversation';
  return first.length > 60 ? `${first.slice(0, 57)}…` : first;
}
