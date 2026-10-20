<script lang="ts">
  // A drag handle that resizes a neighbouring panel. `grow` is the pointer
  // direction that makes the panel bigger: 'left' for a right-hand drawer,
  // 'up' for a bottom drawer.
  let { value = $bindable(), min, max, grow, label, initial }: {
    value: number; min: number; max: () => number; grow: 'left' | 'up'; label: string; initial: number;
  } = $props();
  const vertical = $derived(grow === 'left');
  let start: { pointer: number; value: number } | null = null;
  const clamp = (v: number) => Math.round(Math.min(Math.max(v, min), Math.max(min, max())));
  const position = (event: PointerEvent) => (vertical ? event.clientX : event.clientY);

  function down(event: PointerEvent) {
    if (event.button !== 0) return;
    event.preventDefault();
    start = { pointer: position(event), value };
    (event.currentTarget as HTMLElement).setPointerCapture(event.pointerId);
    document.body.classList.add(vertical ? 'resizing-x' : 'resizing-y');
  }
  function move(event: PointerEvent) {
    if (start) value = clamp(start.value + (start.pointer - position(event)));
  }
  function up() {
    start = null;
    document.body.classList.remove('resizing-x', 'resizing-y');
  }
  function key(event: KeyboardEvent) {
    const step = event.shiftKey ? 50 : 10;
    const bigger = vertical ? 'ArrowLeft' : 'ArrowUp';
    const smaller = vertical ? 'ArrowRight' : 'ArrowDown';
    if (event.key === bigger) value = clamp(value + step);
    else if (event.key === smaller) value = clamp(value - step);
    else if (event.key === 'Home') value = clamp(initial);
    else return;
    event.preventDefault();
  }
</script>

<!-- A focusable separator is the WAI-ARIA window-splitter pattern; Svelte's check doesn't know it. -->
<!-- svelte-ignore a11y_no_noninteractive_tabindex, a11y_no_noninteractive_element_interactions -->
<div class="splitter" class:vertical class:horizontal={!vertical} role="separator" tabindex="0"
  aria-label={label} aria-orientation={vertical ? 'vertical' : 'horizontal'}
  aria-valuenow={value} aria-valuemin={min} aria-valuemax={max()}
  title={`${label}: drag to resize, double-click to reset`}
  onpointerdown={down} onpointermove={move} onpointerup={up} onpointercancel={up}
  ondblclick={() => (value = clamp(initial))} onkeydown={key}></div>
