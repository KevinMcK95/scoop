<script lang="ts">
  import type { Workspace } from '../workspace/state.svelte';
  import { sources } from '../workspace/fixtures';
  let { workspace }: { workspace: Workspace } = $props();
  const source = $derived(sources.find((s) => s.id === workspace.selection.sourceId));
  const paper = $derived(workspace.papers.find((p) => p.id === workspace.selection.publicationId));
  const live = $derived(workspace.services.chat);
  const labels = { user: 'YOU', demo: 'LOCAL DEMO · NO MODEL', assistant: 'SCOOP', tool: 'TOOL', error: 'ERROR' } as const;
</script>

<section class="panel chat-panel" aria-labelledby="chat-heading">
  <header class="panel-heading"><div><span class="eyebrow">01 / REASON</span><h2 id="chat-heading">Conversation</h2></div><span class="badge">{live ? (workspace.busy ? 'Working…' : 'Agent connected') : 'Local demo'}</span></header>
  <div class="chat-scroll">
    <div class="intro-card">
      <span class="orbit-mark" aria-hidden="true">◎</span>
      <h3>A little sky. A shared context.</h3>
      <p>Select a sky marker and a literature card, then send a question. Both selections travel with your message.</p>
      {#if live}<p class="muted">Connected to the SCOOP agent. It can search ADS and change the map; map changes are confirmed back to it after they are drawn.</p>
      {:else}<p class="muted">No agent connected (start <code>python -m scoop.server</code> with SCOOP_MODEL set). Replies only describe captured context.</p>{/if}
    </div>
    <div class="messages" role="log" aria-label="Conversation messages" aria-live="polite" aria-relevant="additions">
      {#each workspace.messages as message (message.id)}
        <article class:user-message={message.role === 'user'} class:tool-message={message.role === 'tool'} class:error-message={message.role === 'error'} class="message">
          <span class="eyebrow">{labels[message.role]}{message.status ? ` · ${message.status}` : ''}</span>
          <p>{message.text}</p>
          {#if message.role === 'user'}
            <details><summary>Attached context snapshot</summary><pre>{JSON.stringify(message.snapshot, null, 2)}</pre></details>
          {/if}
        </article>
      {/each}
    </div>
  </div>
  <form class="composer" onsubmit={(event) => { event.preventDefault(); workspace.send(); }}>
    <div class="context-strip" aria-label="Context for next message">
      <span class="eyebrow">NEXT MESSAGE CONTEXT</span>
      <span>◎ {source?.label ?? 'No sky source'}</span>
      <span>▤ {paper ? (paper.origin === 'ads' ? paper.id : paper.year) : 'No publication'}</span>
      <span class="muted">Viewport: {workspace.viewport.status}</span>
    </div>
    <label class="sr-only" for="chat-draft">Question with selected workspace context</label>
    <textarea id="chat-draft" bind:value={workspace.draft} rows="3" placeholder="Ask about your selection…" maxlength="4000"></textarea>
    <div class="composer-actions"><span class="muted">Selection is captured on send.</span><button class="primary" type="submit" disabled={!workspace.draft.trim() || workspace.busy}>Send <span aria-hidden="true">↗</span></button></div>
  </form>
</section>
