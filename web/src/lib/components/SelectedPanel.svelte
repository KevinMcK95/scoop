<script lang="ts">
  import type { Workspace } from '../workspace/state.svelte';
  let { workspace, onask, onlookup }: { workspace: Workspace; onask: () => void; onlookup: () => void } = $props();
  const row = $derived(workspace.selectedRow);
  const field = $derived(workspace.field);
  const table = $derived(row ? field?.tables[row.table] : undefined);
  const hidden = new Set(['id', 'table', 'polygon', 's_region']);
  const values = $derived(row ? Object.entries(row).filter(([key, value]) => !hidden.has(key) && !key.startsWith('_') && value !== null && value !== '') : []);
  const format = (v: unknown) => typeof v === 'number' ? String(+v.toFixed(6)) : String(v);
</script>

<section class="object-detail" aria-live="polite">
  {#if row}
    <h2>{table?.label ?? row.table} · {row.id}</h2>
    <p class="muted">{table?.provenance}</p>
    <dl class="kv">
      {#each values as [key, value]}
        <dt title={table?.descriptions?.[key] ?? ''}>{key}</dt>
        <dd>{format(value)}{#if table?.units?.[key]}<span class="muted"> {table.units[key]}</span>{/if}</dd>
      {/each}
    </dl>
    {#if row._links?.length}
      <p>{#each row._links as [url, label], i}{i ? ' · ' : ''}<a href={url} target="_blank" rel="noreferrer">{label} ↗</a>{/each}</p>
    {/if}
    {#each Object.entries(row._details ?? {}) as [group, items]}
      <h3>{items.length} {group.toLowerCase()}</h3>
      {#each items as item}
        <div class="spec">
          <strong>{item.title}</strong>
          {#if Object.keys(item.fields).length}<div class="muted">{Object.entries(item.fields).map(([k, v]) => `${k} ${format(v)}`).join(' · ')}</div>{/if}
          {#if item.links.length}<div>{#each item.links as [url, label], i}{i ? ' · ' : ''}{#if url}<a href={url} target="_blank" rel="noreferrer">{label}</a>{:else}<span class="muted">{label}</span>{/if}{/each}</div>{/if}
        </div>
      {/each}
    {/each}
    <button onclick={onlookup} disabled={!workspace.services.chat || workspace.busy}>What's known here?</button>
    <button onclick={onask}>Ask about this object</button>
    <button class="text-button icon" title="Clear selection" aria-label="Clear selection" onclick={() => workspace.selectRow(null)}>×</button>
  {:else}
    <p class="empty-state">Click a source or footprint on the map, or a row in Tables. The agent sees what you select.</p>
  {/if}
</section>
