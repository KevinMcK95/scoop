<script lang="ts">
  import { onMount, untrack } from 'svelte';
  import type { Workspace } from '../workspace/state.svelte';
  import { sources } from '../workspace/fixtures';
  import { fixtureIdFromClick, type AladinViewer } from '../sky/aladin';
  import type { ViewCommand } from '../workspace/contracts';
  let { workspace }: { workspace: Workspace } = $props();
  let viewer = $state<AladinViewer | null>(null);
  const phase = $derived(workspace.viewerPhase);
  let error = $state('');
  let host: HTMLDivElement;
  let applyCommand: ((command: ViewCommand) => void) | null = null;
  const selectedSource = $derived(sources.find((source) => source.id === workspace.selection.sourceId));
  const lastRequest = $derived(workspace.viewRequests.at(-1));

  function captureViewport() {
    if (!viewer) return;
    const [ra, dec] = viewer.getRaDec();
    const [fov] = viewer.getFov();
    if ([ra, dec, fov].every(Number.isFinite)) {
      workspace.updateViewport({ ...workspace.viewport, ra, dec, fov, status: 'live' });
    }
  }

  $effect(() => {
    const pending = workspace.viewRequests.filter((request) => request.status === 'pending');
    const status = phase;
    if (status === 'loading') return;
    untrack(() => {
      for (const request of pending) {
        if (status === 'error' || !applyCommand) {
          workspace.acknowledgeView(request.sequence, 'Sky viewer unavailable; display was not changed.');
          continue;
        }
        try {
          applyCommand(request.command);
          workspace.acknowledgeView(request.sequence);
          captureViewport();
        } catch {
          workspace.acknowledgeView(request.sequence, 'The sky renderer could not apply this command.');
        }
      }
    });
  });

  onMount(() => {
    let disposed = false;
    const timeout = window.setTimeout(() => fail('The atlas did not initialize in time. Network access or WebGL2 may be unavailable.'), 20000);
    function fail(reason: string) {
      if (disposed) return;
      workspace.viewerPhase = 'error';
      error = reason;
      workspace.updateViewport({ ...workspace.viewport, status: 'unavailable' });
    }
    async function initialize() {
      try {
        const { default: A } = await import('aladin-lite');
        await A.init;
        if (disposed || phase === 'error') return;
        const sky = A.aladin('#scoop-sky-atlas', {
          target: `${workspace.viewport.ra} ${workspace.viewport.dec}`,
          fov: workspace.viewport.fov, survey: workspace.viewport.survey,
          cooFrame: 'ICRS', showSimbadPointerControl: false,
          showShareControl: false, showFullscreenControl: false,
          showLayersControl: false, showCooLocation: false, showFrame: false,
          showProjectionControl: false
        });
        const catalog = A.catalog({ name: 'Illustrative fixture sources — not catalog data', color: '#79d9cb', sourceSize: 16, shape: 'circle' });
        const markers = new Map(sources.map((source) => [source.id, A.source(source.ra, source.dec, { fixtureId: source.id, name: source.label })]));
        catalog.addSources([...markers.values()]);
        sky.addCatalog(catalog);
        const overlay = A.graphicOverlay({ name: 'Local angular annotations', color: '#f1cb82', lineWidth: 2 });
        sky.addOverlay(overlay);
        applyCommand = (command) => {
          switch (command.type) {
            case 'center': sky.gotoRaDec(command.ra, command.dec); sky.setFoV(command.fov); break;
            case 'layer.visibility': {
              const layer = command.layerId === 'fixture-sources' ? catalog : overlay;
              if (command.visible) layer.show(); else layer.hide();
              break;
            }
            case 'sources.highlight':
              for (const [id, marker] of markers) {
                if (command.sourceIds.includes(id)) marker.select(); else marker.deselect();
              }
              break;
            case 'annotation.circle': overlay.addFootprints([A.circle(command.ra, command.dec, command.radius)]); break;
            case 'annotations.clear': overlay.removeAll(); break;
          }
        };
        sky.on('objectClicked', (object) => {
          if (!disposed) workspace.selectSource(fixtureIdFromClick(object));
        });
        sky.on('positionChanged', () => { if (!disposed) captureViewport(); });
        sky.on('zoomChanged', () => { if (!disposed) captureViewport(); });
        viewer = sky;
        workspace.viewerPhase = 'ready';
        captureViewport();
      } catch (cause) {
        console.warn('Aladin demo initialization failed', cause);
        fail('Aladin could not start. Network access and a WebGL2-capable browser are required.');
      } finally {
        window.clearTimeout(timeout);
      }
    }
    void initialize();
    return () => {
      disposed = true;
      window.clearTimeout(timeout);
      host?.replaceChildren();
    };
  });

  function centerSelected() {
    const source = selectedSource ?? sources[0];
    workspace.requestView({ type: 'center', ra: source.ra, dec: source.dec, fov: 0.8 });
  }
  function circleSelected() {
    if (!selectedSource) return;
    workspace.requestView({ type: 'annotation.circle', ra: selectedSource.ra, dec: selectedSource.dec,
      radius: 2 / 60, label: `2 arcmin around ${selectedSource.label}` });
  }
</script>

<section class="panel sky-panel" aria-labelledby="sky-heading">
  <header class="panel-heading"><div><span class="eyebrow">02 / EXPLORE</span><h2 id="sky-heading">Sky workspace</h2></div><span class="badge">ICRS</span></header>
  <div class="sky-toolbar"><span><span class="status-dot"></span> DSS2 · color</span><span class="badge">Local view actions</span></div>
  <div class="map-actions" role="group" aria-label="Sky tools">
    <button class="small-button" disabled={phase !== 'ready'} onclick={centerSelected}>⌖ Center {selectedSource ? 'selection' : 'field'}</button>
    <button class="small-button" disabled={phase !== 'ready' || !selectedSource} onclick={() => workspace.requestView({ type: 'sources.highlight', sourceIds: selectedSource ? [selectedSource.id] : [] })}>Highlight selected</button>
    <button class="small-button" disabled={phase !== 'ready' || !selectedSource} onclick={circleSelected}>Circle · 2′ radius</button>
    <button class="small-button" disabled={phase !== 'ready' || workspace.annotations.length === 0} onclick={() => workspace.requestView({ type: 'annotations.clear' })}>Clear circles</button>
  </div>

  {#if lastRequest}
    <p class="map-command-status" role="status">View #{lastRequest.sequence} · {lastRequest.command.type} · {lastRequest.status}{lastRequest.error ? ` — ${lastRequest.error}` : ''}</p>
  {/if}
  <div class="sky-viewport">
    <div id="scoop-sky-atlas" bind:this={host} class="atlas" aria-label="Interactive Aladin sky atlas with illustrative fixture markers"></div>
    {#if phase !== 'ready'}
      <div class="sky-fallback" role="status">
        <div class="reticle" aria-hidden="true">＋</div>
        <h3>{phase === 'loading' ? 'Opening the sky atlas' : 'Atlas unavailable'}</h3>
        <p>{phase === 'loading' ? 'Loading Aladin Lite. Survey imagery requires a network connection and WebGL2.' : error}</p>
        <p class="muted">The fixture list in Layers still works. {phase === 'error' ? 'Reload the page to retry.' : ''}</p>
      </div>
    {/if}
  </div>
  <div class="coordinates" aria-label="Sky viewport coordinates"><span>RA <strong>{workspace.viewport.ra.toFixed(4)}°</strong></span><span>DEC <strong>{workspace.viewport.dec.toFixed(4)}°</strong></span><span>FoV <strong>{workspace.viewport.fov.toFixed(2)}°</strong></span></div>

</section>
