<script lang="ts">
  import type { Workspace } from '../workspace/state.svelte';
  import type { MapAction } from '../field/types';
  let { workspace, view }: { workspace: Workspace; view: 'summary' | 'decisions' | 'activity' } = $props();
  const field = $derived(workspace.field);
  // Coverage fractions as percentages; everything else as it is.
  const format = (v: unknown, column: string) => typeof v === 'number' && /covered/.test(column)
    ? (v > 0 && v < 0.005 ? '<1%' : `${Math.round(100 * v)}%`) : String(v ?? '');
  const describe = (action: MapAction) => {
    const a = action as unknown as Record<string, unknown>;
    return action.type + (a.layerId ? ` ${a.layerId}` : a.id ? ` ${a.id}` : a.table ? ` ${a.table}` : '');
  };
</script>

<div class="notes-panel">
  {#if view === 'summary'}
    {#each field?.summaries ?? [] as summary}
      <h3 title={`From ${field?.tables[summary.source]?.label ?? summary.source}`}>{summary.title}</h3>
      <table class="data-table">
        <thead><tr>{#each summary.columns as column}<th>{column}</th>{/each}</tr></thead>
        <tbody>{#each summary.rows as row}<tr>{#each row as cell, i}<td class:num={typeof cell === 'number'}>{format(cell, summary.columns[i])}</td>{/each}</tr>{/each}</tbody>
      </table>
      {#if summary.note}<p class="muted">{summary.note}</p>{/if}
    {:else}<p class="empty-state">{field ? "This field's data sources provide no summaries." : 'No field loaded.'}</p>{/each}
  {:else if view === 'decisions'}
    {#if field}
      <ul>{#each field.decisions as decision}<li>{decision}</li>{/each}</ul>
    {:else}<p class="empty-state">No field loaded.</p>{/if}
  {:else}
    <p class="muted">Map actions by you and SCOOP, newest first. The agent hears whether each was applied.</p>
    {#each workspace.requests.toReversed() as request (request.sequence)}
      <p>#{request.sequence} · <strong title={request.origin === 'agent' ? 'SCOOP' : 'you'}>{request.origin === 'agent' ? '✦' : '✎'}</strong> · {request.actions.map(describe).join(', ')} · {request.status === 'applied' ? '✓' : request.status === 'failed' ? '✗' : '◌'}{request.error ? ` — ${request.error}` : ''}</p>
    {:else}<p>No map actions yet.</p>{/each}
  {/if}
</div>
