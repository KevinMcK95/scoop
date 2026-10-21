<script lang="ts">
  import type { Workspace } from '../workspace/state.svelte';
  import type { Layer } from '../field/types';
  import { catalog, groupLayers, type CatalogGroup } from '../field/catalog';
  import { adsUrl, bibcodeOf } from '../chat/markdown';
  import { download, layerCsv } from '../field/export';
  let { workspace }: { workspace: Workspace } = $props();
  const field = $derived(workspace.field);
  const target = $derived(field?.target);
  const ready = $derived(workspace.viewerPhase === 'ready');
  // On the map: layer groups on the user's list, collapsed until expanded.
  const onMap = $derived(groupLayers(workspace.layers.filter((layer) => layer.added ?? layer.visible)));
  let expanded = $state<Record<string, boolean>>({});
  // "Munoz2018ApJ...860...66M" -> "Munoz 2018" and its ADS link.
  const references = $derived(Object.entries(target?.references ?? {}).map(([property, ref]) => {
    const bibcode = bibcodeOf(ref);
    const who = bibcode ? ref.slice(0, -19) : ref;
    return { property: property.replace(/_/g, ' '), label: bibcode ? `${who} ${bibcode.slice(0, 4)}` : ref, url: bibcode ? adsUrl(bibcode) : null };
  }));
  const surveys = $derived(field?.surveys ?? [['CDS/P/DSS2/color', 'DSS2 color']]);
  let runKey = $state('');
  let runError = $state('');
  async function run() { runError = (await workspace.runField(runKey)) ?? ''; if (!runError) runKey = ''; }

  const update = (layers: Layer[], change: { visible?: boolean; added?: boolean }) =>
    workspace.request(layers.map((layer) => ({ type: 'layer.update' as const, layerId: layer.id, ...change })), 'user');
  // Off the list: pipeline layers go back to "Add data"; your and SCOOP's layers are deleted.
  const takeOff = (layers: Layer[]) => workspace.request(layers.map((layer) => layer.origin === 'pipeline'
    ? { type: 'layer.update' as const, layerId: layer.id, added: false } : { type: 'layer.remove' as const, layerId: layer.id }), 'user');
  const swatch = (layer: Layer) => `border-color:${layer.style.color};background:${layer.style.hollow ? 'transparent' : layer.style.color}`;

  let query = $state('');
  let browsing = $state(false);
  const available = $derived(catalog(field, workspace.layers, query, workspace.sources.filter((s) => s.queryable)));
  let addError = $state('');
  // Options a data source's query takes (e.g. "Tmag <" for the TIC), set before adding it
  // or to query again; values the user typed are kept per source.
  const optionsOf = (name: string | undefined) => workspace.sources.find((s) => s.name === name)?.options ?? [];
  let optionValues = $state<Record<string, Record<string, number>>>({});
  const valuesOf = (name: string) => Object.fromEntries(optionsOf(name).map((o) => [o.name, optionValues[name]?.[o.name] ?? o.default]));
  const setOption = (name: string, option: string, value: number) => {
    if (Number.isFinite(value)) optionValues = { ...optionValues, [name]: { ...optionValues[name], [option]: value } };
  };
  async function requery(name: string) { addError = (await workspace.addSource(name, valuesOf(name))) ?? ''; }
  async function add(group: CatalogGroup, layers = group.layers) {
    if (group.query) {
      addError = (await workspace.addSource(group.query, optionsOf(group.query).length ? valuesOf(group.query) : undefined)) ?? '';
      return;
    }
    if (group.table) workspace.request([{ type: 'layer.add', id: `user:all-${group.table}`, table: group.table,
      label: group.group, group: group.group, style: { color: '#9fd3ff', shape: 'circle', size: 8 } }], 'user');
    else update(layers, { visible: true });
  }
</script>

{#snippet options(name: string, again: boolean)}
  <div class="source-options">
    {#each optionsOf(name) as option (option.name)}
      <label title={option.help}>{option.label}
        <input type="number" min={option.min} max={option.max} step={option.step} value={valuesOf(name)[option.name]}
          aria-label={`${option.label} for ${name}`} onchange={(e) => setOption(name, option.name, e.currentTarget.valueAsNumber)} /></label>
    {/each}
    {#if again}<button class="text-button" title="Query again with these values" onclick={() => requery(name)}>↻ Query again</button>{/if}
    {#if optionsOf(name)[0]?.help}<small class="muted">{optionsOf(name)[0].help}</small>{/if}
  </div>
{/snippet}

<section class="layers-panel" aria-label="Field and layers">
  <div class="field-picker">
    <label for="field-select" class="eyebrow">FIELD</label>
    <select id="field-select" value={field?.key ?? ''} disabled={!workspace.fieldKeys.length}
      onchange={(event) => workspace.loadField(event.currentTarget.value)}>
      {#if !field}<option value="">{workspace.fieldKeys.length ? 'Choose a field' : 'No fields (run a smoke test)'}</option>{/if}
      {#each workspace.fieldKeys as key}<option value={key}>{key}</option>{/each}
    </select>
  </div>

  {#if field}
    <div class="add-data">
      <input type="search" bind:value={query} placeholder="＋ Add data: Gaia, spectra, RR Lyrae…" aria-label="Search data to add"
        onfocus={() => (browsing = true)} onkeydown={(e) => { if (e.key === 'Escape') { query = ''; browsing = false; e.currentTarget.blur(); } }} />
      {#if browsing || query}
        <div class="catalog">
          {#each available as group (group.group)}
            <button class="catalog-entry" onclick={() => add(group)} title={group.provenance}>
              {#if group.layers[0]}<span class="swatch" style={swatch(group.layers[0])}></span>{:else}<span class="swatch"></span>{/if}
              <span class="layer-label">{group.group}</span>
              {#if group.query}<span class="count" title="Not in this field yet: adding queries the archive">query</span>{:else}<span class="count">{group.count}</span>{/if}<span aria-hidden="true">＋</span>
            </button>
            {#if group.query}{@render options(group.query, false)}{/if}
            {#each group.matching as layer (layer.id)}
              <button class="catalog-entry sub" onclick={() => add(group, [layer])} title={group.provenance}>
                <span class="swatch" style={swatch(layer)}></span>
                <span class="layer-label">{layer.label}</span><span class="count">{layer.count}</span><span aria-hidden="true">＋</span>
              </button>
            {/each}
          {:else}
            <p class="muted">{query ? `Nothing matches “${query}”.` : 'Everything in this field is on the map.'}</p>
          {/each}
          {#if addError}<p class="muted">{addError}</p>{/if}
          {#if !query}<button class="text-button" onclick={() => (browsing = false)}>Done</button>{/if}
        </div>
      {/if}
    </div>

    <div class="on-map" aria-label="On the map">
      {#each onMap as [group, layers] (group)}
        {@const shown = layers.filter((layer) => layer.visible).length}
        <div class="layer-group">
          <div class="group-row">
            <button class="text-button icon" aria-expanded={!!expanded[group]} aria-label={`${expanded[group] ? 'Collapse' : 'Expand'} ${group}`}
              onclick={() => (expanded = { ...expanded, [group]: !expanded[group] })}>{expanded[group] ? '▾' : '▸'}</button>
            <input type="checkbox" checked={shown === layers.length} indeterminate={shown > 0 && shown < layers.length}
              disabled={!ready} aria-label={`Show ${group}`} onchange={(e) => update(layers, { visible: e.currentTarget.checked })} />
            <span class="swatch" style={swatch(layers[0])}></span>
            <span class="layer-label">{group}{#if layers.some((l) => l.origin !== 'pipeline')}<em class="origin" title="Includes layers added by you or SCOOP">{layers.some((l) => l.origin === 'agent') ? '✦' : '✎'}</em>{/if}</span>
            <span class="count">{layers.reduce((sum, l) => sum + l.count, 0)}</span>
            <button class="text-button icon remove" title="Download as CSV (one file per layer)" aria-label={`Download ${group} as CSV`}
              onclick={() => { if (field) for (const file of layerCsv(field, layers)) download(file.name, file.text); }}>⤓</button>
            <button class="text-button icon remove" title="Take off the map" aria-label={`Take ${group} off the map`} onclick={() => takeOff(layers)}>×</button>
          </div>
          {#if optionsOf(layers[0]?.table).length}{@render options(layers[0].table, true)}{/if}
          {#if expanded[group]}
            {#each layers as layer (layer.id)}
              <label class="map-layer-row sub" title={layer.note ?? layer.id}>
                <input type="checkbox" checked={layer.visible} disabled={!ready}
                  onchange={(event) => update([layer], { visible: event.currentTarget.checked })} />
                <span class="swatch" style={swatch(layer)}></span>
                <span class="layer-label">{layer.label}{#if layer.origin !== 'pipeline'}<em class="origin" title={layer.origin === 'agent' ? 'Added by SCOOP' : 'Added by you'}>{layer.origin === 'agent' ? '✦' : '✎'}</em>{/if}
                  {#if layer.note}<small>{layer.note}</small>{/if}</span>
                <span class="count">{layer.count}</span>
                <button class="text-button icon remove" title="Take off the map" aria-label={`Take ${layer.label} off the map`}
                  onclick={(event) => { event.preventDefault(); takeOff([layer]); }}>×</button>
              </label>
            {/each}
          {/if}
        </div>
      {/each}
    </div>
  {/if}

  {#if target}
    <details class="target-details">
      <summary>◉ {target.name}</summary>
      <dl class="kv">
        <dt>Center</dt><dd>{target.ra.toFixed(4)}, {target.dec >= 0 ? '+' : ''}{target.dec.toFixed(4)}</dd>
        {#if target.kind === 'cone'}
          <dt>Radius</dt><dd>{target.rhalf_arcmin}′ cone</dd>
        {:else}
          <dt>r_half</dt><dd>{target.rhalf_arcmin.toFixed(2)}′ (semi-major)</dd>
          <dt>Ellipticity / PA</dt><dd>{target.ellipticity.toFixed(2)} / {target.pa_deg.toFixed(0)}° E of N</dd>
        {/if}
        {#if target.distance_kpc != null}<dt>Distance</dt><dd>{target.distance_kpc.toFixed(1)} kpc</dd>{/if}
        {#if target.pmra != null && target.pmdec != null}<dt>Systemic PM</dt><dd>{target.pmra.toFixed(3)}, {target.pmdec.toFixed(3)} mas/yr</dd>{/if}
        <dt>Source</dt><dd>{target.kind === 'cone' ? 'Cone search' : 'Local Volume Database'}</dd>
      </dl>
      {#if references.length}
        <details class="references">
          <summary>📚 {references.length} references</summary>
          <dl class="kv">
            {#each references as ref}<dt>{ref.property}</dt><dd>{#if ref.url}<a href={ref.url} target="_blank" rel="noreferrer">{ref.label}</a>{:else}{ref.label}{/if}</dd>{/each}
          </dl>
        </details>
      {/if}
    </details>
  {:else}
    <p class="muted">{workspace.fieldStatus}</p>
  {/if}

  <div class="field-picker">
    <label for="survey-select" class="eyebrow">BACKGROUND</label>
    <select id="survey-select" value={workspace.survey} onchange={(event) => workspace.setSurvey(event.currentTarget.value)}>
      {#each surveys as [id, name]}<option value={id}>{name}</option>{/each}
    </select>
  </div>

  <form class="run-field" onsubmit={(e) => { e.preventDefault(); run(); }}>
    <input bind:value={runKey} placeholder="Run an LVDB object, e.g. ursa_minor_1" aria-label="LVDB key to run" />
    <button class="small-button" disabled={!runKey.trim()} aria-label="Run" title="Query the archives for this object">▶</button>
  </form>
  {#if runError}<p class="muted">{runError}</p>{/if}
  {#each workspace.jobs as job (job.id)}
    <div class="job" class:failed={job.status === 'failed'}>
      <strong>{job.key}</strong> <span title={job.status}>{job.status === 'running' ? `⟳ ${job.elapsed_s.toFixed(0)}s` : job.status === 'done' ? '✓' : '✗'}</span>
      {#if job.status === 'failed'}<small>{job.error}</small>
      {:else if job.decisions.length}<small>{job.decisions.at(-1)}</small>
      {:else if job.last}<small>{job.last}</small>{/if}
    </div>
  {/each}
</section>
