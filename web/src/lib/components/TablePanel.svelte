<script lang="ts">
  import type { Workspace } from '../workspace/state.svelte';
  import type { Row } from '../field/types';
  import { download, toCsv } from '../field/export';
  let { workspace }: { workspace: Workspace } = $props();
  // Tables whose data source declares browse columns, in field order.
  const browsable = $derived(Object.entries(workspace.field?.tables ?? {}).filter(([, t]) => t.browse?.length && t.rows.length));
  let chosen = $state<string | null>(null);
  const name = $derived(chosen && browsable.some(([n]) => n === chosen) ? chosen : browsable[0]?.[0] ?? null);
  const table = $derived(name ? workspace.field!.tables[name] : null);
  let sortKey = $state<string | null>(null);
  let direction = $state(1);
  const LIMIT = 500;
  const rows = $derived.by(() => {
    if (!table) return [];
    const key = sortKey && table.browse!.includes(sortKey) ? sortKey : null;
    const all = key ? [...table.rows].sort((a, b) => {
      const [x, y] = [a[key] as string | number | null, b[key] as string | number | null];
      if (x === y) return 0;
      if (x === null || x === undefined) return 1;
      if (y === null || y === undefined) return -1;
      return (x > y ? 1 : -1) * direction;
    }) : table.rows;
    return all.slice(0, LIMIT);
  });
  const format = (v: unknown) => typeof v === 'number' ? String(+v.toPrecision(6)) : v === null || v === undefined ? '' : String(v);
  function sortBy(key: string) {
    direction = key === sortKey ? -direction : 1;
    sortKey = key;
  }
  function open(row: Row) {
    if (!name) return;
    workspace.selectRow({ table: name, id: row.id });
    workspace.request([{ type: 'focus.rows', table: name, ids: [row.id] }], 'user');
  }
</script>

{#if table && name}
  <div class="table-tools">
    <select aria-label="Table" value={name} onchange={(e) => { chosen = e.currentTarget.value; sortKey = null; }}>
      {#each browsable as [n, t]}<option value={n}>{t.label} ({t.rows.length})</option>{/each}
    </select>
    <button class="text-button icon" title="Download this table as CSV" aria-label="Download table as CSV"
      onclick={() => download(`${workspace.field!.key}_${name}.csv`, toCsv(workspace.field!, name, table.rows, { label: table.label }))}>⤓</button>
    <span class="muted">{table.provenance}{table.rows.length > LIMIT ? ` · first ${LIMIT} rows; ask SCOOP to filter` : ''}</span>
  </div>
  <table class="data-table">
    <thead><tr><th>id</th>{#each table.browse ?? [] as key}<th><button class="text-button" title={table.descriptions?.[key] ?? ''} onclick={() => sortBy(key)}>{key}{table.units?.[key] ? ` (${table.units[key]})` : ''}{sortKey === key ? (direction > 0 ? ' ▴' : ' ▾') : ''}</button></th>{/each}</tr></thead>
    <tbody>
      {#each rows as row (row.id)}
        <tr class:selected={workspace.map.selection?.table === name && workspace.map.selection.id === row.id} onclick={() => open(row)}>
          <td>{row.id}</td>{#each table.browse ?? [] as key}<td class:num={typeof row[key] === 'number'}>{format(row[key])}</td>{/each}
        </tr>
      {/each}
    </tbody>
  </table>
{:else}
  <p class="empty-state">No tables to browse{workspace.field ? '' : ' (no field loaded)'}.</p>
{/if}
