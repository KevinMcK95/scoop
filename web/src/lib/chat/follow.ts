// Keep a scroll container at its bottom while its content grows (streaming text,
// tool steps, images loading), unless the reader has scrolled up to read. Whether to
// follow is decided by the reader's own scrolling, not measured after content was
// added (a long new message would otherwise look like "scrolled up").
// Passing a new `key` (e.g. the id of the message you just sent) follows again.

const SLACK = 40; // px from the bottom that still counts as "at the bottom"

export function follow(node: HTMLElement, key?: unknown) {
  let pinned = true;
  let last = key;
  const toBottom = () => { node.scrollTop = node.scrollHeight; };
  const onScroll = () => { pinned = node.scrollHeight - node.scrollTop - node.clientHeight <= SLACK; };
  const observer = typeof ResizeObserver === 'undefined' ? null : new ResizeObserver(() => { if (pinned) toBottom(); });
  for (const child of Array.from(node.children)) observer?.observe(child);
  node.addEventListener('scroll', onScroll, { passive: true });
  toBottom();
  return {
    update(next?: unknown) {
      if (next !== last) { last = next; pinned = true; }
      if (pinned) toBottom();
    },
    destroy() {
      observer?.disconnect();
      node.removeEventListener('scroll', onScroll);
    }
  };
}
