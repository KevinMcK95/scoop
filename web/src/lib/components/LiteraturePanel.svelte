<script lang="ts">
  import type { Workspace } from '../workspace/state.svelte';
  let { workspace, onask }: { workspace: Workspace; onask: () => void } = $props();
  const selected = $derived(workspace.papers.find((paper) => paper.id === workspace.selection.publicationId));
  let query = $state('');
</script>

<section class="literature-panel" aria-label="Literature">
  <div class="paper-list" aria-label="Publication results">
    {#if workspace.services.literature}
      <form class="literature-search" onsubmit={(event) => { event.preventDefault(); workspace.searchLiterature(query); }}>
        <label class="sr-only" for="ads-query">ADS query</label>
        <input id="ads-query" bind:value={query} placeholder='abs:"M31" year:2020-2026' />
        <button type="submit" disabled={!query.trim()}>Search ADS</button>
      </form>
    {/if}
    <p class="muted">{workspace.literatureNote}</p>
    {#each workspace.papers as paper (paper.id)}
      <button class="paper-card" class:selected={workspace.selection.publicationId === paper.id} aria-pressed={workspace.selection.publicationId === paper.id} onclick={() => workspace.selectPublication(paper.id)}><span class="paper-meta">{paper.origin === 'ads' ? paper.id : `FICTIONAL · ${paper.year}`}</span><strong>{paper.title}</strong><small>{paper.authors}</small></button>
    {/each}
  </div>
  <div class="paper-detail" aria-live="polite">
    {#if selected}
      <h2>{selected.title}</h2><p class="muted">{selected.authors} · {selected.year}{selected.origin === 'ads' ? '' : ' · Fictional'}</p>
      {#if selected.url}<p><a href={selected.url} target="_blank" rel="noreferrer">Open in ADS ↗</a></p>{/if}<p>{selected.summary}</p>
      <button onclick={onask}>Ask about selection</button><button class="text-button" onclick={() => workspace.selectPublication(null)}>Clear publication</button>
    {:else}<p class="empty-state">Select a title to read its details and attach it to your next message. Paper and sky selections stay independent.</p>{/if}
  </div>
</section>
