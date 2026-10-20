<script lang="ts">
  import { onMount, untrack } from 'svelte';
  import type { Workspace } from '../workspace/state.svelte';
  import { layerRows, rowsOf } from '../field/actions';
  import type { Annotation, Field, Layer, Row } from '../field/types';
  import { boxFromDrag, circleFromDrag, polygonFromClicks, rowFromClick, wheelFactor, zoomAnchor, type AladinAPI, type AladinLayer, type AladinViewer } from '../sky/aladin';
  let { workspace }: { workspace: Workspace } = $props();
  let A: AladinAPI | null = null;
  let sky = $state<AladinViewer | null>(null);
  const phase = $derived(workspace.viewerPhase);
  let error = $state('');
  let host: HTMLDivElement;
  const HIGHLIGHT = '#ffd84d';
  const SELECTED = '#ffffff';
  const NOTE = { agent: '#f1cb82', user: '#9fd3ff' };

  // Aladin objects drawn for each layer, rebuilt only when what they draw changes.
  const drawn = new Map<string, { signature: string; objects: AladinLayer[] }>();
  let extras: AladinLayer[] = [];

  function captureViewport() {
    if (!sky) return;
    const [ra, dec] = sky.getRaDec();
    const [fov] = sky.getFov();
    if ([ra, dec, fov].every(Number.isFinite)) workspace.updateViewport({ ra, dec, fov, status: 'live' });
  }

  /** Draw rows as point sources and/or footprint and polygon outlines. */
  function draw(table: string, rows: Row[], name: string, style: { color: string; shape: string; size: number; dash?: boolean },
    lineWidth = 1.5): AladinLayer[] {
    if (!A || !sky) return [];
    const objects: AladinLayer[] = [];
    const points = rows.filter((row) => !row.s_region && !row.polygon && row.ra !== null && row.dec !== null);
    const outlines = rows.filter((row) => row.s_region || row.polygon);
    if (points.length) {
      const catalog = A.catalog({ name, shape: style.shape, sourceSize: style.size, color: style.color });
      sky.addCatalog(catalog);
      catalog.addSources(points.map((row) => A!.source(row.ra!, row.dec!, { _ref: { table, id: row.id }, name: row.id })));
      objects.push(catalog);
    }
    if (outlines.length) {
      const overlay = A.graphicOverlay({ name, color: style.color, lineWidth, ...(style.dash ? { lineDash: [6, 4] } : {}) });
      sky.addOverlay(overlay);
      for (const row of outlines) {
        const shapes = row.s_region ? A.footprintsFromSTCS(row.s_region, { color: style.color })
          : [A.polygon(row.polygon!, { color: style.color, lineWidth }) as Record<string, unknown>];
        for (const shape of shapes) { shape._ref = { table, id: row.id }; overlay.add(shape); }
      }
      objects.push(overlay);
    }
    return objects;
  }

  function renderLayers(field: Field | null, layers: Layer[]) {
    if (!sky) return;
    const keep = new Set<string>();
    for (const layer of layers) {
      keep.add(layer.id);
      const signature = JSON.stringify([field?.key, layer.table, layer.filter, layer.style]);
      let entry = drawn.get(layer.id);
      if (!entry || entry.signature !== signature) {
        entry?.objects.forEach((object) => sky!.removeOverlay(object));
        entry = { signature, objects: draw(layer.table, layerRows(field, layer), layer.label, layer.style) };
        drawn.set(layer.id, entry);
      }
      entry.objects.forEach((object) => (layer.visible ? object.show() : object.hide()));
    }
    for (const [id, entry] of drawn) {
      if (!keep.has(id)) { entry.objects.forEach((object) => sky!.removeOverlay(object)); drawn.delete(id); }
    }
  }

  function renderMarks(field: Field | null, annotations: Annotation[]) {
    if (!A || !sky) return;
    extras.forEach((object) => sky!.removeOverlay(object));
    extras = [];
    const { highlight, selection } = workspace.map;
    if (highlight) {
      const wanted = new Set(highlight.ids);
      extras.push(...draw(highlight.table, rowsOf(field, highlight.table).filter((row) => wanted.has(row.id)),
        'Highlighted', { color: HIGHLIGHT, shape: 'circle', size: 26 }, 3));
    }
    if (selection) {
      extras.push(...draw(selection.table, rowsOf(field, selection.table).filter((row) => row.id === selection.id),
        'Selected', { color: SELECTED, shape: 'square', size: 28 }, 3));
    }
    for (const origin of ['agent', 'user'] as const) {
      const mine = annotations.filter((note) => note.origin === origin);
      if (!mine.length) continue;
      const circles = A.graphicOverlay({ name: `${origin} notes`, color: NOTE[origin], lineWidth: 2 });
      sky.addOverlay(circles);
      mine.filter((note) => note.shape === 'circle').forEach((note) => circles.add(A!.circle(note.ra, note.dec, note.radius!)));
      mine.filter((note) => note.shape === 'polygon' && note.points).forEach((note) => circles.add(A!.polygon(note.points!)));
      const labels = A.catalog({ name: `${origin} labels`, shape: 'cross', sourceSize: 10, color: NOTE[origin],
        displayLabel: true, labelColumn: 'text', labelColor: NOTE[origin], labelFont: '13px sans-serif' });
      sky.addCatalog(labels);
      labels.addSources(mine.map((note) => A!.source(note.ra, note.dec, { text: note.text,
        ...(note.table && note.rowId ? { _ref: { table: note.table, id: note.rowId } } : {}) })));
      extras.push(circles, labels);
    }
  }

  $effect(() => {
    const [field, layers] = [workspace.field, workspace.map.layers];
    if (phase === 'ready') untrack(() => renderLayers(field, layers));
  });
  $effect(() => {
    const [field, , , annotations] = [workspace.field, workspace.map.highlight, workspace.map.selection, workspace.map.annotations];
    if (phase === 'ready') untrack(() => renderMarks(field, annotations));
  });
  $effect(() => {
    const target = workspace.camera;
    if (phase === 'ready' && target && sky) untrack(() => { sky!.gotoRaDec(target.ra, target.dec); sky!.setFoV(target.fov); captureViewport(); });
  });
  $effect(() => {
    const survey = workspace.survey;
    if (phase === 'ready' && sky) untrack(() => sky!.setBaseImageLayer(survey));
  });
  // Resolve map actions once the renderer can show them; acknowledgments go back to the agent.
  $effect(() => {
    const pending = workspace.pending;
    if (phase === 'loading') return;
    untrack(() => {
      for (const request of pending) {
        workspace.resolve(request.sequence, phase === 'error' ? 'Sky viewer unavailable; display was not changed.' : undefined);
      }
    });
  });

  let removeWheel: (() => void) | null = null;
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
        A = (await import('aladin-lite')).default as unknown as AladinAPI;
        await A.init;
        if (disposed || phase === 'error') return;
        const viewer = A.aladin('#scoop-sky-atlas', {
          target: `${workspace.viewport.ra} ${workspace.viewport.dec}`, fov: workspace.viewport.fov,
          survey: workspace.survey, cooFrame: 'ICRSd', showReticle: false,
          showSimbadPointerControl: false, showShareControl: false, showFullscreenControl: false,
          showLayersControl: false, showCooLocation: false, showFrame: false, showProjectionControl: false
        });
        viewer.on('objectClicked', (object) => { if (!disposed && object) workspace.selectRow(rowFromClick(object)); });
        viewer.on('footprintClicked', (shape) => { if (!disposed) workspace.selectRow(rowFromClick(shape)); });
        viewer.on('positionChanged', () => { if (!disposed) captureViewport(); });
        viewer.on('zoomChanged', () => { if (!disposed) captureViewport(); });
        // Zoom toward the cursor (this Aladin build zooms about the view center).
        const wheel = (event: WheelEvent) => {
          event.preventDefault();
          event.stopPropagation();
          const box = host.getBoundingClientRect();
          const [x, y] = [event.clientX - box.left, event.clientY - box.top];
          const before = viewer.pix2world?.(x, y);
          const [fov] = viewer.getFov();
          viewer.setFoV(Math.min(180, Math.max(1 / 3600, fov * wheelFactor(event.deltaY, event.deltaMode))));
          const after = viewer.pix2world?.(x, y);
          if (before && after && [...before, ...after].every(Number.isFinite)) {
            const [ra, dec] = zoomAnchor(viewer.getRaDec(), before, after);
            viewer.gotoRaDec(ra, dec);
          }
          captureViewport();
        };
        host.addEventListener('wheel', wheel, { capture: true, passive: false });
        removeWheel = () => host.removeEventListener('wheel', wheel, { capture: true });
        sky = viewer;
        workspace.viewerPhase = 'ready';
        captureViewport();
      } catch (cause) {
        console.warn('Aladin initialization failed', cause);
        fail('Aladin could not start. Network access and a WebGL2-capable browser are required.');
      } finally {
        window.clearTimeout(timeout);
      }
    }
    void initialize();
    return () => {
      disposed = true;
      removeWheel?.();
      window.clearTimeout(timeout);
      drawn.clear();
      host?.replaceChildren();
    };
  });

  const field = $derived(workspace.field);
  const highlight = $derived(workspace.map.highlight);
  const lastRequest = $derived(workspace.requests.at(-1));
  // Drawing tools: circle (press at the center, drag out the radius), box (drag
  // corner to corner) or polygon (click corners; double-click, Enter or the first
  // corner finishes). One shape per activation; Esc cancels.
  let drawing = $state<'circle' | 'box' | 'polygon' | null>(null);
  let vertices = $state<{ x: number; y: number }[]>([]);
  let cursor = $state<{ x: number; y: number } | null>(null);
  let drag = $state<{ x0: number; y0: number; x1: number; y1: number } | null>(null);
  let circleError = $state('');
  const toPixel = (event: PointerEvent) => {
    const box = (event.currentTarget as HTMLElement).getBoundingClientRect();
    return { x: event.clientX - box.left, y: event.clientY - box.top };
  };
  const pix2world = (x: number, y: number) => sky?.pix2world?.(x, y) ?? null;
  const preview = $derived(drag && drawing === 'circle' ? circleFromDrag(pix2world, drag.x0, drag.y0, drag.x1, drag.y1) : null);
  const boxPreview = $derived(drag && drawing === 'box' ? boxFromDrag(pix2world, drag.x0, drag.y0, drag.x1, drag.y1) : null);
  const arcmin = (v: number) => (v < 1 ? `${(v * 60).toFixed(1)}″` : `${v.toFixed(2)}′`);
  function finishPolygon() {
    const shape = polygonFromClicks(pix2world, vertices);
    if (!shape) return;
    circleError = workspace.drawPolygon(shape.points, shape.ra, shape.dec) ?? '';
    vertices = [];
    drawing = null;
  }
  function startDraw(event: PointerEvent) {
    if (event.button !== 0) return;
    const p = toPixel(event);
    if (drawing === 'polygon') {
      // Clicking near the first corner closes the shape.
      if (vertices.length >= 3 && Math.hypot(p.x - vertices[0].x, p.y - vertices[0].y) < 8) finishPolygon();
      else vertices = [...vertices, p];
      return;
    }
    drag = { x0: p.x, y0: p.y, x1: p.x, y1: p.y };
    (event.currentTarget as HTMLElement).setPointerCapture(event.pointerId);
  }
  function moveDraw(event: PointerEvent) {
    const p = toPixel(event);
    cursor = p;
    if (drag) drag = { ...drag, x1: p.x, y1: p.y };
  }
  function endDraw() {
    if (!drag) return;
    const { x0, y0, x1, y1 } = drag;
    drag = null;
    if (drawing === 'circle') {
      const circle = circleFromDrag(pix2world, x0, y0, x1, y1);
      if (!circle) return; // a click, not a drag: keep the tool on
      circleError = workspace.drawCircle(circle.ra, circle.dec, circle.radiusArcmin) ?? '';
    } else {
      const box = boxFromDrag(pix2world, x0, y0, x1, y1);
      if (!box) return;
      circleError = workspace.drawBox(box.points, box.ra, box.dec, box.widthArcmin, box.heightArcmin) ?? '';
    }
    drawing = null;
  }
  function toggleDrawing(tool: 'circle' | 'box' | 'polygon') { drawing = drawing === tool ? null : tool; drag = null; vertices = []; circleError = ''; }
  function key(event: KeyboardEvent) {
    const target = event.target as HTMLElement | null;
    if (target?.closest('input, textarea, select, [contenteditable]')) return;
    if (event.metaKey || event.ctrlKey || event.altKey) return;
    if (event.key === 'Escape' && drawing) { drawing = null; drag = null; vertices = []; }
    else if (drawing === 'polygon' && event.key === 'Enter') { event.preventDefault(); finishPolygon(); }
    else if (drawing === 'polygon' && event.key === 'Backspace') { event.preventDefault(); vertices = vertices.slice(0, -1); }
    else if (phase !== 'ready') return;
    else if (event.key.toLowerCase() === 'c') toggleDrawing('circle');
    else if (event.key.toLowerCase() === 'b') toggleDrawing('box');
    else if (event.key.toLowerCase() === 'p') toggleDrawing('polygon');
  }
  let linkStatus = $state('');
  async function copyLink() {
    const url = `${location.origin}${location.pathname}#${workspace.viewLink()}`;
    try { await navigator.clipboard.writeText(url); linkStatus = 'Link copied'; }
    catch { history.replaceState(null, '', url); linkStatus = 'Link is in the address bar'; }
    setTimeout(() => (linkStatus = ''), 2500);
  }
  function fitField() {
    if (field) workspace.request([{ type: 'focus', ra: field.target.ra, dec: field.target.dec, fov: field.fov }], 'user');
  }
</script>

<svelte:window onkeydown={key} />

<section class="panel sky-panel" aria-label="Sky map">
  {#if workspace.progress.length}
    <!-- Data being retrieved: field loads, archive queries, ADS searches, pipeline runs. -->
    <div class="progress" role="progressbar" aria-label={workspace.progress.join('; ')}>
      <div class="progress-bar"></div>
      <span class="progress-label">{workspace.progress[0]}{workspace.progress.length > 1 ? ` (+${workspace.progress.length - 1})` : ''}</span>
    </div>
  {/if}
  <div class="map-actions" role="group" aria-label="Map tools">
    <button class="small-button" disabled={phase !== 'ready' || !field} title="Fit the field" aria-label="Fit the field" onclick={fitField}>⌖</button>
    <button class="small-button" disabled={!workspace.canUndo} title="Undo" aria-label="Undo" onclick={() => workspace.undo()}>↶</button>
    <button class="small-button" disabled={phase !== 'ready' || !field} title="Copy a link to this view (field, layers, highlight, notes)"
      aria-label="Copy a link to this view" onclick={copyLink}>🔗</button>
    {#if linkStatus}<span class="map-hint inline" role="status">{linkStatus}</span>{/if}
    <button class="small-button" disabled={!workspace.map.annotations.length} title="Clear circles, boxes and labels" aria-label="Clear notes" onclick={() => workspace.request([{ type: 'annotations.clear' }], 'user')}>⌫</button>
    <button class="small-button" class:selected={drawing === 'circle'} aria-pressed={drawing === 'circle'} disabled={phase !== 'ready'}
      title="Draw a circle: drag from its center outward (C; Esc cancels)" aria-label="Circle tool" onclick={() => toggleDrawing('circle')}>◯</button>
    <button class="small-button" class:selected={drawing === 'box'} aria-pressed={drawing === 'box'} disabled={phase !== 'ready'}
      title="Draw a box: drag from one corner to the opposite one (B; Esc cancels)" aria-label="Box tool" onclick={() => toggleDrawing('box')}>▭</button>
    <button class="small-button" class:selected={drawing === 'polygon'} aria-pressed={drawing === 'polygon'} disabled={phase !== 'ready'}
      title="Draw a polygon: click corners; double-click, Enter or the first corner finishes (P; ⌫ undoes a corner; Esc cancels)"
      aria-label="Polygon tool" onclick={() => toggleDrawing('polygon')}>⬠</button>
  </div>
  {#if drawing}<p class="map-hint" role="status">{drawing === 'circle' ? '◯ drag from the center' : drawing === 'box' ? '▭ drag corner to corner' : '⬠ click corners · double-click or ↵ finishes · ⌫ undo'} · Esc cancels</p>{/if}
  {#if circleError}<p class="map-command-status" role="status">Circle: {circleError}</p>{/if}
  {#if highlight}
    <div class="map-banner" class:agent={highlight.origin === 'agent'} role="status">
      <span><strong>{highlight.origin === 'agent' ? 'SCOOP' : 'You'}</strong> highlighted {highlight.ids.length} {field?.tables[highlight.table]?.label ?? highlight.table}{highlight.reason ? `: ${highlight.reason}` : ''}</span>
      <button class="text-button" onclick={() => workspace.request([{ type: 'highlight', table: highlight.table, ids: [] }], 'user')}>Clear</button>
    </div>
  {/if}
  {#if lastRequest?.status === 'failed'}
    <p class="map-command-status" role="status">Map action failed: {lastRequest.error}</p>
  {/if}
  <div class="sky-viewport">
    <div id="scoop-sky-atlas" bind:this={host} class="atlas" aria-label="Interactive Aladin sky map"></div>
    {#if drawing}
      <!-- Takes the drag while the tool is on, so the map doesn't pan. -->
      <div class="draw-layer" role="application" aria-label={drawing === 'circle' ? 'Draw a circle by dragging' : drawing === 'box' ? 'Draw a box by dragging' : 'Draw a polygon by clicking its corners'}
        onpointerdown={startDraw} onpointermove={moveDraw} onpointerup={endDraw} onpointercancel={() => (drag = null)}
        ondblclick={() => drawing === 'polygon' && finishPolygon()}>
        {#if drawing === 'polygon' && vertices.length}
          <svg class="draw-preview" aria-hidden="true">
            <polyline points={[...vertices, ...(cursor ? [cursor] : [])].map((v) => `${v.x},${v.y}`).join(' ')} />
            {#each vertices as v, i}<circle class="center" cx={v.x} cy={v.y} r={i === 0 && vertices.length >= 3 ? 5 : 2.5} />{/each}
          </svg>
        {/if}
        {#if drag && drawing === 'circle'}
          {@const r = Math.hypot(drag.x1 - drag.x0, drag.y1 - drag.y0)}
          <svg class="draw-preview" aria-hidden="true">
            <circle cx={drag.x0} cy={drag.y0} r={r} />
            <line x1={drag.x0} y1={drag.y0} x2={drag.x1} y2={drag.y1} />
            <circle class="center" cx={drag.x0} cy={drag.y0} r="2.5" />
          </svg>
          {#if preview}<span class="draw-readout" style:left={`${drag.x1 + 10}px`} style:top={`${drag.y1 + 10}px`}>{arcmin(preview.radiusArcmin)}</span>{/if}
        {:else if drag}
          <svg class="draw-preview" aria-hidden="true">
            <rect x={Math.min(drag.x0, drag.x1)} y={Math.min(drag.y0, drag.y1)} width={Math.abs(drag.x1 - drag.x0)} height={Math.abs(drag.y1 - drag.y0)} />
          </svg>
          {#if boxPreview}<span class="draw-readout" style:left={`${drag.x1 + 10}px`} style:top={`${drag.y1 + 10}px`}>{arcmin(boxPreview.widthArcmin)} × {arcmin(boxPreview.heightArcmin)}</span>{/if}
        {/if}
      </div>
    {/if}
    {#if phase !== 'ready'}
      <div class="sky-fallback" role="status">
        <div class="reticle" aria-hidden="true">＋</div>
        <h3>{phase === 'loading' ? 'Opening the sky map' : 'Map unavailable'}</h3>
        <p>{phase === 'loading' ? 'Loading Aladin Lite. Survey imagery requires a network connection and WebGL2.' : error}</p>
      </div>
    {/if}
  </div>
  <div class="coordinates" aria-label="Sky viewport coordinates"><span>RA <strong>{workspace.viewport.ra.toFixed(4)}°</strong></span><span>DEC <strong>{workspace.viewport.dec.toFixed(4)}°</strong></span><span>FoV <strong>{workspace.viewport.fov.toFixed(3)}°</strong></span><span>{workspace.fieldStatus}</span></div>
</section>
