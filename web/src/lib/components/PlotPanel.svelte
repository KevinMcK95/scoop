<script lang="ts">
  import type { Workspace } from '../workspace/state.svelte';
  import { boxFilter, boxPolygon, histogram, presets as fieldPresets, scatter, simplifyPath, zoomView, type View } from '../field/plot';
  import { download, layerCsv } from '../field/export';
  import type { Clause, Lasso, PlotSpec } from '../field/types';
  import { figureFileName } from '../chat/figures';
  let { workspace, onreveal }: { workspace: Workspace; onreveal?: (stepId: number) => void } = $props();
  const figures = $derived(workspace.figures);
  const W = 640, H = 300, L = 48, R = 12, T = 10, B = 34;
  const spec = $derived(workspace.map.plot ?? null);
  const field = $derived(workspace.field);
  const presets = $derived(fieldPresets(field));
  const numeric = $derived(spec && field?.tables[spec.table]
    ? field.tables[spec.table].columns.filter((c) => field.tables[spec.table].rows.some((row) => typeof row[c] === 'number')) : []);
  // Zoom: the scroll wheel zooms about the cursor, the Zoom tool zooms to a dragged box,
  // Pan drags the view; Reset goes back to the automatic 1–99% range. A new plot resets it.
  let view = $state<View | null>(null);
  const plotKey = $derived(spec ? `${spec.table}|${spec.x}|${spec.y ?? ''}` : '');
  $effect(() => { void plotKey; view = null; });
  const xy = $derived(spec?.y ? scatter(field, workspace.map, spec as PlotSpec & { y: string }, { view, size: [W - L - R, H - T - B] }) : null);
  const hist = $derived(spec && !spec.y ? histogram(field, workspace.map, spec, 30, { view }) : null);
  const xAxis = $derived(xy?.x ?? hist?.x ?? { min: 0, max: 1, ticks: [] });
  const yMax = $derived(hist ? Math.max(1, ...hist.total) : 1);
  const yAxis = $derived(xy?.y ?? { min: 0, max: yMax, ticks: [0, Math.round(yMax / 2), yMax] });
  const unit = (column?: string) => (column && spec && field?.tables[spec.table]?.units?.[column]) ? ` (${field.tables[spec.table].units![column]})` : '';

  const sx = (v: number) => {
    const f = (v - xAxis.min) / (xAxis.max - xAxis.min);
    return spec?.invert_x ? W - R - f * (W - L - R) : L + f * (W - L - R);
  };
  const sy = (v: number) => {
    const f = (v - yAxis.min) / (yAxis.max - yAxis.min);
    return spec?.invert_y ? T + f * (H - T - B) : H - B - f * (H - T - B);
  };
  const vx = (px: number) => {
    const f = spec?.invert_x ? (W - R - px) / (W - L - R) : (px - L) / (W - L - R);
    return xAxis.min + f * (xAxis.max - xAxis.min);
  };
  const vy = (py: number) => {
    const f = spec?.invert_y ? (py - T) / (H - T - B) : (H - B - py) / (H - T - B);
    return yAxis.min + f * (yAxis.max - yAxis.min);
  };

  const marked = $derived.by(() => {
    if (!xy || !spec) return [];
    const { highlight, selection } = workspace.map;
    const byId = new Map(xy.points.map((p) => [p.id, p]));
    const rings: { x: number; y: number; color: string }[] = [];
    if (highlight?.table === spec.table) for (const id of highlight.ids) { const p = byId.get(id); if (p) rings.push({ x: p.x, y: p.y, color: '#ffd84d' }); }
    if (selection?.table === spec.table) { const p = byId.get(selection.id); if (p) rings.push({ x: p.x, y: p.y, color: '#ffffff' }); }
    return rings;
  });

  let svg = $state<SVGSVGElement>();
  let drag = $state<{ x0: number; y0: number; x1: number; y1: number } | null>(null);
  let boxes = 0;

  // Member selection, after the BP3M member-selection notebook: one selection per panel
  // (box or lasso), panels intersect (a member is inside every panel drawn on), redrawing
  // replaces a panel's selection unless Extend is on, and a panel only constrains stars it
  // can plot unless Strict is on. Stored as one user layer per table whose filter holds one
  // 'lasso' clause per panel, so the map, the tables and the agent all see the same members.
  let tool = $state<'box' | 'lasso' | 'zoom' | 'pan'>('box');
  let pan = $state<{ px: number; py: number; view: View } | null>(null);
  const currentView = (): View => ({ x: [xAxis.min, xAxis.max], ...(spec?.y ? { y: [yAxis.min, yAxis.max] as [number, number] } : {}) });
  const zoomBy = (factor: number, cx = (xAxis.min + xAxis.max) / 2, cy = (yAxis.min + yAxis.max) / 2) =>
    (view = zoomView(currentView(), factor, cx, spec?.y ? cy : undefined));
  $effect(() => {
    const el = svg;
    if (!el) return;
    const wheel = (event: WheelEvent) => {
      event.preventDefault();
      const p = local(event);
      zoomBy(event.deltaY > 0 ? 1.25 : 0.8, vx(p.x), vy(p.y));
    };
    el.addEventListener('wheel', wheel, { passive: false });
    return () => el.removeEventListener('wheel', wheel);
  });
  let target = $state<'members' | 'layer'>('members');
  let extend = $state(false);
  let strict = $state(false);
  let path = $state<[number, number][] | null>(null);
  const membersId = (table: string) => `user:members:${table}`;
  const members = $derived(spec ? workspace.map.layers.find((l) => l.id === membersId(spec.table)) ?? null : null);
  const isPanel = (c: Clause) => spec?.y && c.op === 'lasso' && c.column === spec.x && (c.value as Lasso).y === spec.y;
  const panelPolygons = $derived(members && spec?.y ? members.filter.filter(isPanel).flatMap((c) => (c.value as Lasso).polygons) : []);

  function setMembers(filter: Clause[]) {
    if (!spec) return;
    const label = `Members (${filter.length} panel${filter.length === 1 ? '' : 's'})`;
    if (!filter.length && members) return workspace.request([{ type: 'layer.remove', layerId: members.id }], 'user');
    workspace.request([members
      ? { type: 'layer.update', layerId: members.id, filter, label }
      : { type: 'layer.add', id: membersId(spec.table), table: spec.table, filter, label, group: 'Your selections',
          note: 'Stars inside your selection on every plot you drew on (BP3M-style member selection)',
          style: { color: '#ff7eb6', shape: 'circle', size: 12, hollow: true } }], 'user');
  }
  function addPanelSelection(polygon: [number, number][]) {
    if (!spec?.y || polygon.length < 3) return;
    const others = (members?.filter ?? []).filter((c) => !isPanel(c));
    const previous = extend ? panelPolygons : [];
    setMembers([...others, { column: spec.x, op: 'lasso',
      value: { y: spec.y, polygons: [...previous, polygon].slice(-10), missing: strict ? 'reject' : 'keep' } }]);
  }
  const clearPanel = () => members && setMembers(members.filter.filter((c) => !isPanel(c)));
  const clearAll = () => members && setMembers([]);
  function saveMembers() {
    if (!field || !members) return;
    for (const file of layerCsv(field, [members])) download(file.name, file.text);
  }
  function local(event: MouseEvent) {
    const point = svg!.createSVGPoint();
    point.x = event.clientX; point.y = event.clientY;
    const p = point.matrixTransform(svg!.getScreenCTM()!.inverse());
    return { x: Math.min(Math.max(p.x, L), W - R), y: Math.min(Math.max(p.y, T), H - B) };
  }
  function down(event: PointerEvent) {
    const p = local(event);
    if (tool === 'lasso' && spec?.y) path = [[p.x, p.y]];
    else if (tool === 'pan') pan = { px: p.x, py: p.y, view: currentView() };
    else drag = { x0: p.x, y0: p.y, x1: p.x, y1: p.y };
    svg?.setPointerCapture(event.pointerId);
  }
  function move(event: PointerEvent) {
    const p = local(event);
    if (pan) {
      const { view: v0 } = pan;
      const dx = ((p.x - pan.px) / (W - L - R)) * (v0.x[1] - v0.x[0]) * (spec?.invert_x ? 1 : -1);
      const dy = v0.y ? ((p.y - pan.py) / (H - T - B)) * (v0.y[1] - v0.y[0]) * (spec?.invert_y ? -1 : 1) : 0;
      view = { x: [v0.x[0] + dx, v0.x[1] + dx], ...(v0.y ? { y: [v0.y[0] + dy, v0.y[1] + dy] as [number, number] } : {}) };
      return;
    }
    if (path) { const last = path[path.length - 1]; if (Math.hypot(p.x - last[0], p.y - last[1]) > 2) path = [...path, [p.x, p.y]]; }
    else if (drag) drag = { ...drag, x1: p.x, y1: p.y };
  }
  function up() {
    if (pan) { pan = null; return; }
    if (path && spec) {
      const drawn = path;
      path = null;
      if (drawn.length < 3) return pick(drawn[0][0], drawn[0][1]);
      return addPanelSelection(simplifyPath(drawn.map(([px, py]) => [vx(px), vy(py)])));
    }
    if (!drag || !spec) return;
    const { x0, y0, x1, y1 } = drag;
    drag = null;
    if (Math.abs(x1 - x0) < 4 && (!spec.y || Math.abs(y1 - y0) < 4)) return pick(x0, y0);
    if (tool === 'zoom') {
      view = { x: [vx(x0), vx(x1)], ...(spec.y ? { y: [vy(y0), vy(y1)] as [number, number] } : {}) };
      return;
    }
    if (spec.y && target === 'members') return addPanelSelection(boxPolygon(vx(x0), vx(x1), vy(y0), vy(y1)));
    const name = spec.y ? `${spec.y} vs ${spec.x}` : spec.x;
    workspace.request([{ type: 'layer.add', id: `user:box-${++boxes}-${Date.now() % 100000}`, table: spec.table,
      filter: boxFilter(spec, vx(x0), vx(x1), spec.y ? vy(y0) : undefined, spec.y ? vy(y1) : undefined),
      label: `Box in ${name}`, group: 'Your selections', style: { color: '#9fd3ff', shape: 'square', size: 10, hollow: true } }], 'user');
  }
  function pick(px: number, py: number) {
    if (!xy || !spec) return;
    let best: { id: string; d: number } | null = null;
    for (const p of xy.points) {
      const d = Math.hypot(sx(p.x) - px, sy(p.y) - py);
      if (d < 8 && (!best || d < best.d)) best = { id: p.id, d };
    }
    if (best) workspace.selectRow({ table: spec.table, id: best.id });
  }
  const show = (next: PlotSpec) => workspace.request([{ type: 'plot.show', ...next }], 'user');
</script>

<section class="plot-panel">
  <div class="plot-tools">
    {#each presets as preset}
      <button class="small-button" class:selected={spec?.table === preset.table && spec.x === preset.x && spec.y === preset.y}
        onclick={() => { const { label: _, ...rest } = preset; show(rest); }}>{preset.label}</button>
    {/each}
    {#if spec}
      <label>x <select value={spec.x} onchange={(e) => show({ ...spec, x: e.currentTarget.value, title: undefined })}>{#each numeric as c}<option>{c}</option>{/each}</select></label>
      <label>y <select value={spec.y ?? ''} onchange={(e) => show({ ...spec, y: e.currentTarget.value || undefined, title: undefined })}><option value="">— histogram —</option>{#each numeric as c}<option>{c}</option>{/each}</select></label>
      <label><input type="checkbox" checked={!!spec.invert_x} onchange={(e) => show({ ...spec, invert_x: e.currentTarget.checked })} /> flip x</label>
      <label><input type="checkbox" checked={!!spec.invert_y} onchange={(e) => show({ ...spec, invert_y: e.currentTarget.checked })} /> flip y</label>
      <span class="zoom-tools" role="group" aria-label="Zoom">
        <button class="small-button" class:selected={tool === 'zoom'} onclick={() => (tool = 'zoom')} title="Drag a box to zoom in (or scroll on the plot)">⌕ Zoom</button>
        <button class="small-button" class:selected={tool === 'pan'} onclick={() => (tool = 'pan')} title="Drag to move the view">✥ Pan</button>
        <button class="small-button" onclick={() => zoomBy(0.8)} title="Zoom in" aria-label="Zoom in">＋</button>
        <button class="small-button" onclick={() => zoomBy(1.25)} title="Zoom out" aria-label="Zoom out">－</button>
        <button class="small-button" onclick={() => (view = null)} disabled={!view} title="Back to the full range">Reset view</button>
      </span>
    {/if}
  </div>
  {#if spec?.y}
    <div class="plot-tools members-tools" role="group" aria-label="Selection">
      <button class="small-button" class:selected={tool === 'box'} onclick={() => (tool = 'box')} title="Drag a box">▭ Box</button>
      <button class="small-button" class:selected={tool === 'lasso'} onclick={() => (tool = 'lasso')} title="Draw around points">◌ Lasso</button>
      <label title="Where a selection goes">→ <select bind:value={target} disabled={tool === 'lasso'}>
        <option value="members">Members</option><option value="layer">New layer</option></select></label>
      <label title="Add to this plot's selection instead of replacing it"><input type="checkbox" bind:checked={extend} /> Extend</label>
      <label title="Stars this plot can't show (e.g. no velocity) are left out instead of kept"><input type="checkbox" bind:checked={strict} /> Strict</label>
      {#if members}
        <span class="muted">Members: <strong>{members.count}</strong> ({members.filter.length} panel{members.filter.length === 1 ? '' : 's'})</span>
        <button class="small-button" onclick={clearPanel} disabled={!panelPolygons.length}>Clear panel</button>
        <button class="small-button" onclick={clearAll}>Clear all</button>
        <button class="small-button" onclick={saveMembers} title="CSV of the member rows, with the filter that defines them">⤓ Members CSV</button>
      {/if}
    </div>
  {/if}
  {#if !field}
    <p class="empty-state">Load a field to plot its tables.</p>
  {:else if !spec}
    <p class="empty-state">Pick a plot. Points use the map's layer colours; click one to select it. On scatter plots, drag a box or draw a lasso to select members: selections on different plots intersect.</p>
  {:else}
    <p class="muted">{spec.title ?? `${spec.table}: ${spec.y ? `${spec.y} vs ` : ''}${spec.x}`}{xy ? ` · ${xy.total} rows${xy.outside ? `, ${xy.outside} outside ${xy.zoomed ? 'the zoomed view' : 'the 1–99% range'} not shown` : ''}` : ''}{hist ? ` · ${hist.n} values` : ''}</p>
    <svg bind:this={svg} viewBox={`0 0 ${W} ${H}`} class="plot" role="img" aria-label="Linked plot"
      class:panning={tool === 'pan'} onpointerdown={down} onpointermove={move} onpointerup={up}>
      <defs><clipPath id="plot-area"><rect x={L} y={T} width={W - L - R} height={H - T - B} /></clipPath></defs>
      {#each xAxis.ticks as t}<line class="grid" x1={sx(t)} x2={sx(t)} y1={T} y2={H - B} /><text class="tick" x={sx(t)} y={H - B + 14} text-anchor="middle">{t}</text>{/each}
      {#each yAxis.ticks as t}<line class="grid" x1={L} x2={W - R} y1={sy(t)} y2={sy(t)} /><text class="tick" x={L - 6} y={sy(t) + 4} text-anchor="end">{t}</text>{/each}
      <text class="axis-label" x={(L + W - R) / 2} y={H - 4} text-anchor="middle">{spec.x}{unit(spec.x)}</text>
      <text class="axis-label" x={12} y={(T + H - B) / 2} text-anchor="middle" transform={`rotate(-90 12 ${(T + H - B) / 2})`}>{spec.y ? `${spec.y}${unit(spec.y)}` : 'count'}</text>
      <g clip-path="url(#plot-area)">
      {#if xy}
        {#each xy.points as p}<circle cx={sx(p.x)} cy={sy(p.y)} r={p.r} fill={p.color} />{/each}
        {#each marked as m}<circle cx={sx(m.x)} cy={sy(m.y)} r="6" fill="none" stroke={m.color} stroke-width="2" />{/each}
      {:else if hist}
        {#each hist.total as c, i}<rect x={sx(hist.x.min + i * hist.width)} width={Math.max(sx(hist.x.min + hist.width) - sx(hist.x.min) - 1, 1)} y={sy(c)} height={H - B - sy(c)} fill="#3a4a5c" />{/each}
        {#each hist.layers as layer}
          <polyline fill="none" stroke={layer.color} stroke-width="1.5"
            points={layer.counts.flatMap((c, i) => [`${sx(hist.x.min + i * hist.width)},${sy(c)}`, `${sx(hist.x.min + (i + 1) * hist.width)},${sy(c)}`]).join(' ')} />
        {/each}
      {/if}
      {#each panelPolygons as poly}<polygon points={poly.map(([x, y]) => `${sx(x)},${sy(y)}`).join(' ')} fill="rgba(255,126,182,0.08)" stroke="#ff7eb6" stroke-width="1.5" stroke-dasharray="4 3" />{/each}
      </g>
      {#if path}<polyline points={path.map(([x, y]) => `${x},${y}`).join(' ')} fill="rgba(255,126,182,0.1)" stroke="#ff7eb6" stroke-width="1.5" />{/if}
      {#if drag}<rect class="brush" x={Math.min(drag.x0, drag.x1)} y={spec.y ? Math.min(drag.y0, drag.y1) : T} width={Math.abs(drag.x1 - drag.x0)} height={spec.y ? Math.abs(drag.y1 - drag.y0) : H - T - B} />{/if}
    </svg>
  {/if}

  {#if figures.length}
    <!-- Figures SCOOP made in this conversation (run_python plots, sky images), newest first. -->
    <h4 class="eyebrow figures-heading">FIGURES FROM THIS CONVERSATION ({figures.length})</h4>
    <div class="figures">
      {#each figures as figure, i (figure.stepId)}
        <div class="figure-card" role="group" aria-label={figure.question || figure.label}>
          <a href={figure.image} target="_blank" rel="noreferrer" title="Open full size"><img src={figure.image} alt={`${figure.label} for: ${figure.question}`} loading="lazy" /></a>
          <div class="figure-caption">
            <span class="figure-question" title={figure.question}>{figure.question || figure.label}</span>
            <span class="figure-actions">
              {#if onreveal}<button class="text-button icon" title="Show where it was made in the chat" aria-label="Show in chat" onclick={() => onreveal(figure.stepId)}>↩</button>{/if}
              <a class="text-button icon" href={figure.image} download={figureFileName(figure, figures.length - i)} title="Download" aria-label="Download figure">⤓</a>
            </span>
          </div>
          {#if figure.code}<details class="figure-code"><summary>code</summary><pre>{figure.code}</pre></details>{/if}
        </div>
      {/each}
    </div>
  {/if}
</section>
