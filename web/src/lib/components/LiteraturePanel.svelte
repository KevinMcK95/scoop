<script lang="ts">
  import type { Workspace } from '../workspace/state.svelte';
  let { workspace, onask }: { workspace: Workspace; onask: () => void } = $props();
  const selected = $derived(workspace.papers.find((paper) => paper.id === workspace.publicationId));
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
      <button class="paper-card" class:selected={workspace.publicationId === paper.id} aria-pressed={workspace.publicationId === paper.id} onclick={() => workspace.selectPublication(paper.id)}><span class="paper-meta">{paper.id}</span><strong>{paper.title}</strong><small>{paper.authors}</small></button>
    {/each}
  </div>
  <div class="paper-detail" aria-live="polite">
    {#if selected}
      <h2>{selected.title}</h2><p class="muted">{selected.authors} · {selected.year}</p>
      {#if selected.url}<p><a href={selected.url} target="_blank" rel="noreferrer">Open in ADS ↗</a></p>{/if}<p>{selected.summary}</p>
      <button onclick={onask}>Ask about selection</button><button class="text-button icon" title="Clear publication" aria-label="Clear publication" onclick={() => workspace.selectPublication(null)}>×</button>
    {:else}<p class="empty-state">Select a paper to read its abstract and attach it to your next message.</p>{/if}
  </div>
</section>
