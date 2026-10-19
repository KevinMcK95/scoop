<script lang="ts">
  import type { Workspace } from '../workspace/state.svelte';
  import { sources } from '../workspace/fixtures';
  let { workspace }: { workspace: Workspace } = $props();
  function clearSelection() {
    workspace.selectSource(null);
    if (workspace.viewerPhase === 'ready') workspace.requestView({ type: 'sources.highlight', sourceIds: [] });
  }
</script>
<section class="layers-panel" aria-label="Layers and fixture sources">
  <h2>Layers & sources</h2>
  <p class="muted">DSS2 color · ICRS</p>
  <h3>Overlay visibility</h3>
  {#each workspace.layers as layer}
    <label class="map-layer-row"><input type="checkbox" checked={layer.visible} disabled={workspace.viewerPhase !== 'ready'} onchange={(event) => workspace.requestView({ type: 'layer.visibility', layerId: layer.id, visible: event.currentTarget.checked })} /><span>{layer.label}<small>{layer.origin} · {layer.count} items</small></span></label>
  {/each}
  <p class="muted">Visibility does not delete data. Controls enable when the atlas is ready.</p>
  <div class="list-heading"><h3>Fixture sources</h3><button class="text-button" disabled={!workspace.selection.sourceId} onclick={clearSelection}>Clear source</button></div>
  <p class="muted">Invented markers near M31, not identified objects.</p>
  <div class="source-list">
    {#each sources as source}
      <button class="source-row" class:selected={workspace.selection.sourceId === source.id} aria-pressed={workspace.selection.sourceId === source.id} onclick={() => workspace.selectSource(source.id)}><strong>{source.label}</strong><small>{source.kind}</small><small>{source.ra.toFixed(4)}° / {source.dec.toFixed(4)}°</small></button>
    {/each}
  </div>
  <p class="muted">Real survey backdrop: CDS / Aladin Lite. If tiles stay blank, this source list still works. Atlas readiness does not verify tile delivery.</p>
</section>
