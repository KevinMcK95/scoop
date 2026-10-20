<script lang="ts">
  import type { Workspace } from '../workspace/state.svelte';
  import { boxFilter, histogram, presets as fieldPresets, scatter } from '../field/plot';
  import type { PlotSpec } from '../field/types';
  import { figureFileName } from '../chat/figures';
  let { workspace, onreveal }: { workspace: Workspace; onreveal?: (stepId: number) => void } = $props();
  const figures = $derived(workspace.figures);
  const W = 640, H = 300, L = 48, R = 12, T = 10, B = 34;
  const spec = $derived(workspace.map.plot ?? null);
  const field = $derived(workspace.field);
  const presets = $derived(fieldPresets(field));
  const numeric = $derived(spec && field?.tables[spec.table]
    ? field.tables[spec.table].columns.filter((c) => field.tables[spec.table].rows.some((row) => typeof row[c] === 'number')) : []);
  const xy = $derived(spec?.y ? scatter(field, workspace.map, spec as PlotSpec & { y: string }) : null);
  const hist = $derived(spec && !spec.y ? histogram(field, workspace.map, spec) : null);
  const xAxis = $derived(xy?.x ?? hist?.x ?? { min: 0, max: 1, ticks: [] });
  const yMax = $derived(hist ? Math.max(1, ...hist.total) : 1);
  const yAxis = $derived(xy?.y ?? { min: 0, max: yMax, ticks: [0, Math.round(yMax / 2), yMax] });
  const unit = (column?: string) => (column && spec && field?.tables[spec.table]?.units?.[column]) ? ` (${field.tables[spec.table].units![column]})` : '';

  const sx = (v: number) => L + ((v - xAxis.min) / (xAxis.max - xAxis.min)) * (W - L - R);
  const sy = (v: number) => {
    const f = (v - yAxis.min) / (yAxis.max - yAxis.min);
    return spec?.invert_y ? T + f * (H - T - B) : H - B - f * (H - T - B);
  };
  const vx = (px: number) => xAxis.min + ((px - L) / (W - L - R)) * (xAxis.max - xAxis.min);
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
  function local(event: PointerEvent) {
    const point = svg!.createSVGPoint();
    point.x = event.clientX; point.y = event.clientY;
    const p = point.matrixTransform(svg!.getScreenCTM()!.inverse());
    return { x: Math.min(Math.max(p.x, L), W - R), y: Math.min(Math.max(p.y, T), H - B) };
  }
  function down(event: PointerEvent) {
    const p = local(event);
    drag = { x0: p.x, y0: p.y, x1: p.x, y1: p.y };
    svg?.setPointerCapture(event.pointerId);
  }
  function move(event: PointerEvent) { if (drag) { const p = local(event); drag = { ...drag, x1: p.x, y1: p.y }; } }
  function up() {
    if (!drag || !spec) return;
    const { x0, y0, x1, y1 } = drag;
    drag = null;
    if (Math.abs(x1 - x0) < 4 && (!spec.y || Math.abs(y1 - y0) < 4)) return pick(x0, y0);
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
      <label><input type="checkbox" checked={!!spec.invert_y} onchange={(e) => show({ ...spec, invert_y: e.currentTarget.checked })} /> flip y</label>
    {/if}
  </div>
  {#if !field}
    <p class="empty-state">Load a field to plot its tables.</p>
  {:else if !spec}
    <p class="empty-state">Pick a plot. Points use the map's layer colours; click one to select it, drag a box to make a layer from it.</p>
  {:else}
    <p class="muted">{spec.title ?? `${spec.table}: ${spec.y ? `${spec.y} vs ` : ''}${spec.x}`}{xy ? ` · ${xy.total} rows${xy.outside ? `, ${xy.outside} outside the 1–99% range not shown` : ''}` : ''}{hist ? ` · ${hist.n} values` : ''}</p>
    <svg bind:this={svg} viewBox={`0 0 ${W} ${H}`} class="plot" role="img" aria-label="Linked plot"
      onpointerdown={down} onpointermove={move} onpointerup={up}>
      {#each xAxis.ticks as t}<line class="grid" x1={sx(t)} x2={sx(t)} y1={T} y2={H - B} /><text class="tick" x={sx(t)} y={H - B + 14} text-anchor="middle">{t}</text>{/each}
      {#each yAxis.ticks as t}<line class="grid" x1={L} x2={W - R} y1={sy(t)} y2={sy(t)} /><text class="tick" x={L - 6} y={sy(t) + 4} text-anchor="end">{t}</text>{/each}
      <text class="axis-label" x={(L + W - R) / 2} y={H - 4} text-anchor="middle">{spec.x}{unit(spec.x)}</text>
      <text class="axis-label" x={12} y={(T + H - B) / 2} text-anchor="middle" transform={`rotate(-90 12 ${(T + H - B) / 2})`}>{spec.y ? `${spec.y}${unit(spec.y)}` : 'count'}</text>
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
