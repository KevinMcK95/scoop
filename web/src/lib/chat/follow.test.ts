import { afterEach, describe, expect, it, vi } from 'vitest';
import { follow } from './follow';

// A scroll box whose content height we set by hand; ResizeObserver fires on demand.
function setup() {
  let grow = () => {};
  vi.stubGlobal('ResizeObserver', class {
    constructor(callback: () => void) { grow = callback; }
    observe() {}
    disconnect() {}
  });
  const box = { height: 1000, client: 300 };
  let onScroll = () => {};
  const node = {
    children: [{}], scrollTop: 0,
    get scrollHeight() { return box.height; },
    get clientHeight() { return box.client; },
    addEventListener: (_: string, listener: () => void) => { onScroll = listener; },
    removeEventListener: () => {}
  };
  const action = follow(node as unknown as HTMLElement, 'turn-1');
  const contentGrows = (by: number) => { box.height += by; grow(); };
  const readerScrollsTo = (top: number) => { node.scrollTop = top; onScroll(); };
  return { node, box, action, contentGrows, readerScrollsTo };
}

describe('chat follows the conversation down', () => {
  afterEach(() => vi.unstubAllGlobals());

  it('stays at the bottom as content grows, even by a long message', () => {
    const { node, box, contentGrows } = setup();
    expect(node.scrollTop).toBe(box.height);
    contentGrows(2000); // a long answer or an image: more than a screen at once
    expect(node.scrollTop).toBe(box.height);
  });

  it('leaves the reader where they scrolled, until they send again', () => {
    const { node, box, action, contentGrows, readerScrollsTo } = setup();
    readerScrollsTo(100);
    contentGrows(500);
    expect(node.scrollTop).toBe(100);
    action.update('turn-1'); // same turn: still reading
    expect(node.scrollTop).toBe(100);
    action.update('turn-2'); // they sent a message
    expect(node.scrollTop).toBe(box.height);
    contentGrows(400);
    expect(node.scrollTop).toBe(box.height);
  });

  it('follows again once the reader scrolls back to the bottom', () => {
    const { node, box, contentGrows, readerScrollsTo } = setup();
    readerScrollsTo(100);
    readerScrollsTo(box.height - box.client - 10);
    contentGrows(300);
    expect(node.scrollTop).toBe(box.height);
  });
});
