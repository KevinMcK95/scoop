<script lang="ts">
  import { onMount, tick } from 'svelte';
  import ChatPanel from '../lib/components/ChatPanel.svelte';
  import SkyPanel from '../lib/components/SkyPanel.svelte';
  import LayersPanel from '../lib/components/LayersPanel.svelte';
  import LiteraturePanel from '../lib/components/LiteraturePanel.svelte';
  import { createWorkspace } from '../lib/workspace/state.svelte';
  import { httpBackend } from '../lib/api/client';
  import { sources } from '../lib/workspace/fixtures';
  import '../app.css';

  const workspace = createWorkspace(httpBackend());
  let layersOpen = $state(true);
  let chatOpen = $state(true);
  let resultsOpen = $state(false);
  let chatWidth = $state(300);
  let resultsHeight = $state(170);
  const tabs = ['Literature', 'Selected object', 'Observations', 'Activity'] as const;
  let activeTab = $state<(typeof tabs)[number]>('Literature');
  const source = $derived(sources.find((item) => item.id === workspace.selection.sourceId));
  onMount(() => {
    workspace.connect();
    if (window.matchMedia('(max-width: 800px)').matches) {
      layersOpen = false;
      chatOpen = false;
    }
  });
  async function ask() {
    chatOpen = true;
    if (window.matchMedia('(max-width: 800px)').matches) layersOpen = false;
    workspace.askAboutSelection();
    await tick();
    document.getElementById('chat-draft')?.focus();
  }
  function showResults(open: boolean) {
    resultsOpen = open;
    if (open && window.matchMedia('(max-width: 800px)').matches) {
      layersOpen = false;
      chatOpen = false;
    }
  }
  function toggleLayers() {
    layersOpen = !layersOpen;
    if (layersOpen && window.matchMedia('(max-width: 800px)').matches) chatOpen = false;
  }
  function toggleChat() {
    chatOpen = !chatOpen;
    if (chatOpen && window.matchMedia('(max-width: 800px)').matches) layersOpen = false;
  }
</script>

<svelte:head>
  <title>SCOOP — Scientific atlas demo</title>
  <meta name="description" content="Map-first sky atlas with local fixture sources, literature, and context snapshots. No backend connected." />
</svelte:head>

<div class="app-shell">
  <a class="skip-link" href="#workspace">Skip to atlas workspace</a>
  <header class="app-header">
    <h1 class="wordmark">◉ SCOOP <span>Sky atlas</span></h1>
    <nav aria-label="Workspace panels">
      <button aria-expanded={layersOpen} aria-controls="layers-drawer" onclick={toggleLayers}>Layers</button>
      <button aria-expanded={resultsOpen} aria-controls="results-drawer" onclick={() => showResults(!resultsOpen)}>Results</button>
      <button aria-expanded={chatOpen} aria-controls="chat-drawer" onclick={toggleChat}>Chat</button>
    </nav>
    <span class="demo-label" title="Survey imagery uses external services. Sources are illustrative fixtures.">{workspace.services.chat || workspace.services.literature
      ? `Agent ${workspace.services.chat ? 'on' : 'off'} · ADS ${workspace.services.literature ? 'on' : 'off'} · fixture sources`
      : 'Demo · No backend'}</span>
  </header>
  <main id="workspace" tabindex="-1" class="atlas-workspace" style:--chat-width={`${chatWidth}px`}>
    <aside id="layers-drawer" class="left-drawer" hidden={!layersOpen}>
      <button class="drawer-close" aria-label="Close layers and sources" onclick={() => layersOpen = false}>Close layers ×</button>
      <LayersPanel {workspace} />
    </aside>
    <div class="map-column">
      <!-- Never conditionally mount the renderer: panel changes preserve Aladin. -->
      <SkyPanel {workspace} />
      <section id="results-drawer" class="results-drawer" aria-label="Workspace results">
        <div class="results-bar">
          <button aria-expanded={resultsOpen} aria-controls="results-content" onclick={() => showResults(!resultsOpen)}>{resultsOpen ? '▾' : '▴'} <span class="sr-only">Toggle results drawer</span></button>
          <div class="result-tabs" role="tablist" aria-label="Results">
            {#each tabs as tab, i}
              <button id={`result-tab-${i}`} role="tab" aria-selected={activeTab === tab} aria-controls="results-content" tabindex={activeTab === tab ? 0 : -1} onclick={() => { activeTab = tab; showResults(true); }} onkeydown={(event) => {
                if (['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(event.key)) {
                  event.preventDefault();
                  const next = event.key === 'Home' ? 0 : event.key === 'End' ? tabs.length - 1 : (i + (event.key === 'ArrowRight' ? 1 : -1) + tabs.length) % tabs.length;
                  activeTab = tabs[next]; showResults(true);
                  document.getElementById(`result-tab-${next}`)?.focus();
                }
              }}>{tab}</button>
            {/each}
          </div>
        </div>
        <div id="results-content" role="tabpanel" aria-labelledby={`result-tab-${tabs.indexOf(activeTab)}`} tabindex="0" hidden={!resultsOpen} style:height={`${resultsHeight}px`}>
          <label class="resize-control">Results height <input aria-label="Results drawer height" type="range" min="130" max="400" step="10" bind:value={resultsHeight} /><span>{resultsHeight}px</span></label>
          <div class="results-body">
            {#if activeTab === 'Literature'}
              <LiteraturePanel {workspace} onask={ask} />
            {:else if activeTab === 'Selected object'}
              {#if source}
                <article class="object-detail"><h2>{source.label}</h2><p>{source.kind} · Illustrative fixture, not an identified object.</p><p>ICRS: {source.ra.toFixed(4)}° / {source.dec.toFixed(4)}°</p><button onclick={ask}>Ask about selection</button><button class="text-button" onclick={() => workspace.selectSource(null)}>Clear source</button></article>
              {:else}<p class="empty-state">No source selected. Choose a marker or a fixture row in Layers. Publication selection is independent.</p>{/if}
            {:else if activeTab === 'Observations'}
              <p class="empty-state">No observations loaded. No archive or observation service is connected; the DSS2 backdrop and invented markers are not observation results.</p>
            {:else}
              <div class="activity-list"><p class="muted">Atlas: {workspace.viewerPhase} · {workspace.messages.filter((message) => message.role === 'user').length} local messages. View commands below are local renderer actions, not backend activity.</p>
              {#each workspace.viewRequests.toReversed() as request (request.sequence)}<p>#{request.sequence} · {request.command.type} · {request.status}{request.error ? ` — ${request.error}` : ''}</p>{:else}<p>No view commands yet.</p>{/each}</div>
            {/if}
          </div>
        </div>
      </section>
    </div>
    <aside id="chat-drawer" class="right-drawer" hidden={!chatOpen}>
      <div class="chat-drawer-tools"><label class="resize-control">Chat width <input aria-label="Chat drawer width" type="range" min="260" max="460" step="10" bind:value={chatWidth} /></label><button class="text-button" aria-label="Close chat drawer" onclick={() => chatOpen = false}>Close ×</button></div>
      <ChatPanel {workspace} />
    </aside>
  </main>
</div>
