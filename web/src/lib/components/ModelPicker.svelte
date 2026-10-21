<script lang="ts">
  import type { ModelInfo } from '../api/client';
  import { contextLabel, searchModels } from '../settings/models';
  // A text box that searches the endpoint's models as you type; any id can still be typed.
  let { value = $bindable(''), models, id, placeholder = '' }:
    { value: string; models: ModelInfo[]; id: string; placeholder?: string } = $props();
  let open = $state(false);
  let active = $state(-1);
  let list = $state<HTMLUListElement | null>(null);
  const results = $derived(searchModels(models, value));

  function choose(model: ModelInfo) {
    value = model.id;
    open = false;
  }

  function move(step: number) {
    if (!results.shown.length) return;
    open = true;
    active = (active + step + results.shown.length) % results.shown.length;
    list?.children[active]?.scrollIntoView({ block: 'nearest' });
  }

  function keydown(event: KeyboardEvent) {
    if (event.key === 'ArrowDown') { event.preventDefault(); move(1); }
    else if (event.key === 'ArrowUp') { event.preventDefault(); move(-1); }
    else if (event.key === 'Enter' && open && active >= 0) { event.preventDefault(); choose(results.shown[active]); }
    else if (event.key === 'Escape' && open) { event.preventDefault(); open = false; }
  }
</script>

<div class="model-picker">
  <input {id} {placeholder} bind:value autocomplete="off" spellcheck="false" role="combobox"
    aria-expanded={open && results.shown.length > 0} aria-controls="{id}-list" aria-autocomplete="list"
    aria-activedescendant={open && active >= 0 ? `${id}-option-${active}` : undefined}
    onfocus={() => { open = models.length > 0; active = -1; }}
    oninput={() => { open = true; active = -1; }}
    onblur={() => (open = false)} onkeydown={keydown} />
  {#if open && results.shown.length}
    <ul id="{id}-list" role="listbox" bind:this={list}>
      {#each results.shown as model, i (model.id)}
        <!-- mousedown, not click: choosing must happen before the input's blur closes the list -->
        <li id="{id}-option-{i}" role="option" aria-selected={i === active} class:active={i === active}
          onmousedown={(e) => { e.preventDefault(); choose(model); }}>
          <span class="model-id">{model.id}</span>
          {#if model.name || model.context_length}<small>{[model.name, contextLabel(model.context_length)].filter(Boolean).join(' · ')}</small>{/if}
        </li>
      {/each}
      {#if results.total > results.shown.length}<li class="more" aria-hidden="true">{results.total - results.shown.length} more; keep typing</li>{/if}
    </ul>
  {/if}
</div>
