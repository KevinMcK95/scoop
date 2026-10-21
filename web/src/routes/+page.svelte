<script lang="ts">
  import { onMount, tick } from 'svelte';
  import ChatPanel from '../lib/components/ChatPanel.svelte';
  import SkyPanel from '../lib/components/SkyPanel.svelte';
  import LayersPanel from '../lib/components/LayersPanel.svelte';
  import LiteraturePanel from '../lib/components/LiteraturePanel.svelte';
  import SelectedPanel from '../lib/components/SelectedPanel.svelte';
  import TablePanel from '../lib/components/TablePanel.svelte';
  import FieldNotesPanel from '../lib/components/FieldNotesPanel.svelte';
  import PlotPanel from '../lib/components/PlotPanel.svelte';
  import DebugPanel from '../lib/components/DebugPanel.svelte';
  import Landing from '../lib/components/Landing.svelte';
  import { fade } from 'svelte/transition';
  import Splitter from '../lib/components/Splitter.svelte';
  import { createWorkspace } from '../lib/workspace/state.svelte';
  import { httpBackend } from '../lib/api/client';
  import 'katex/dist/katex.min.css';
  import '../app.css';

  const workspace = createWorkspace(httpBackend());
  // The start page covers the workspace until the first question (or a view link,
  // a saved conversation, or "Open the sky map"); the map stays mounted beneath it.
  let landing = $state(true);
  let layersOpen = $state(false);
  let chatOpen = $state(false);
  let resultsOpen = $state(false);
  const narrow = () => window.matchMedia('(max-width: 800px)').matches;
  // Leaving the start page: the chat slides in from the right, layers open on wide screens.
  function enterWorkspace() {
    landing = false;
    chatOpen = true;
    layersOpen = !narrow();
  }
  async function startWith(question: string) {
    if (workspace.messages.length) workspace.newConversation();
    enterWorkspace();
    workspace.draft = question;
    await tick();
    await workspace.send();
  }
  // The sky map opens on whatever is loaded (nothing after a new search): a field is
  // chosen from the Layers menu or by a question, never the alphabetically first one.
  function explore() {
    enterWorkspace();
  }
  function continueConversation(id: string) {
    workspace.openConversation(id);
    enterWorkspace();
    const saved = workspace.conversations.find((c) => c.id === id)?.field;
    if (saved && saved !== workspace.field?.key) void workspace.loadField(saved);
  }
  const CHAT_WIDTH = 340, RESULTS_HEIGHT = 300;
  // Sizes are a per-browser convenience; storage may be unavailable.
  const stored = (name: string, fallback: number) => {
    try { const v = Number(localStorage.getItem(`scoop.${name}`)); return Number.isFinite(v) && v > 0 ? v : fallback; }
    catch { return fallback; }
  };
  let chatWidth = $state(CHAT_WIDTH);
  let resultsHeight = $state(RESULTS_HEIGHT);
  let workspaceEl = $state<HTMLElement>();
  onMount(() => { chatWidth = stored('chatWidth', CHAT_WIDTH); resultsHeight = stored('resultsHeight', RESULTS_HEIGHT); });
  $effect(() => {
    const [w, h] = [chatWidth, resultsHeight];
    try { localStorage.setItem('scoop.chatWidth', String(w)); localStorage.setItem('scoop.resultsHeight', String(h)); } catch { /* not persisted */ }
  });
  // These run during server rendering too, where there is no window.
  const browser = typeof window !== 'undefined';
  const chatMax = () => (browser ? Math.min(900, window.innerWidth * 0.7) : 900);
  const resultsMax = () => (workspaceEl?.clientHeight ?? (browser ? window.innerHeight : 1000)) * 0.75;
  const allTabs = ['Selected', 'Plot', 'Tables', 'Summary', 'Literature', 'Decisions', 'Activity', 'Debug'] as const;
  // Debug appears in developer mode (⚙ Settings).
  const tabs = $derived(allTabs.filter((tab) => tab !== 'Debug' || workspace.debug));
  let activeTab = $state<(typeof allTabs)[number]>('Tables');
  $effect(() => { if (!tabs.includes(activeTab)) activeTab = 'Tables'; });
  // A new selection (by you or the agent) brings its details forward.
  let lastSelection = '';
  $effect(() => {
    const ref = workspace.map.selection;
    const key = ref ? `${ref.table}:${ref.id}` : '';
    if (key && key !== lastSelection) { activeTab = 'Selected'; resultsOpen = true; }
    lastSelection = key;
  });
  // A new plot (usually the agent's) brings the Plot tab forward.
  let lastPlot = '';
  $effect(() => {
    const key = JSON.stringify(workspace.map.plot ?? null);
    if (workspace.map.plot && key !== lastPlot) { activeTab = 'Plot'; resultsOpen = true; }
    lastPlot = key;
  });
  onMount(() => {
    // A shared view link skips the start page and opens once its field is loaded.
    if (location.hash) {
      landing = false;
      layersOpen = !narrow();
      chatOpen = !narrow();
      resultsOpen = true;
      void workspace.connect().then(() => workspace.openViewLink(location.hash));
    } else {
      void workspace.connect({ autoload: false });
    }
  });
  async function ask() {
    chatOpen = true;
    if (narrow()) layersOpen = false;
    workspace.askAboutSelection();
    await tick();
    document.getElementById('chat-draft')?.focus();
  }
  // One click: the agent checks SIMBAD and the literature at the selected row.
  // From the Layers panel (e.g. constraints on planet hosts) to the chat, sent at once.
  async function askInChat(text: string) {
    workspace.draft = text;
    chatOpen = true;
    await workspace.send();
  }
  async function lookupSelection() {
    const row = workspace.selectedRow;
    if (!row || workspace.busy) return;  // send() would refuse, after the user's draft was replaced
    workspace.draft = `What is known about ${row.table} ${row.id}? Check SIMBAD near it and summarize the key references.`;
    chatOpen = true;
    await workspace.send();
  }
  // From a figure in the Plot tab back to where it was made in the chat.
  async function revealStep(stepId: number) {
    chatOpen = true;
    await tick();
    const step = document.getElementById(`step-${stepId}`);
    step?.scrollIntoView({ behavior: 'smooth', block: 'center' });
    step?.classList.add('flash');
    setTimeout(() => step?.classList.remove('flash'), 1500);
  }
  function showResults(open: boolean) {
    resultsOpen = open;
    if (open && narrow()) {
      layersOpen = false;
      chatOpen = false;
    }
  }
  function toggleLayers() {
    layersOpen = !layersOpen;
    if (layersOpen && narrow()) chatOpen = false;
  }
  function toggleChat() {
    chatOpen = !chatOpen;
    if (chatOpen && narrow()) layersOpen = false;
  }
</script>

<svelte:head>
  <title>SCOOP</title>
  <meta name="description" content="A sky map you explore together with an agent: archive data, literature and chat around a field." />
</svelte:head>

<div class="app-shell">
  <a class="skip-link" href="#workspace">Skip to atlas workspace</a>
  <header class="app-header">
    <h1 class="wordmark"><button class="wordmark-home" title="Start page" onclick={() => (landing = true)} disabled={landing}><img class="wordmark-logo" src="/scoop-mark-small.svg" alt="" width="24" height="24" /> SCOOP <span>Sky atlas</span></button></h1>
    <nav aria-label="Workspace panels" hidden={landing}>
      <button aria-expanded={layersOpen} aria-controls="layers-drawer" onclick={toggleLayers}>Layers</button>
      <button aria-expanded={resultsOpen} aria-controls="results-drawer" onclick={() => showResults(!resultsOpen)}>Results</button>
      <button aria-expanded={chatOpen} aria-controls="chat-drawer" onclick={toggleChat}>Chat</button>
      <button class="new-search" disabled={workspace.busy} title="Clear the field, layers and chat, and start from the start page"
        onclick={() => { workspace.newSearch(); landing = true; }}>New search</button>
    </nav>
    <span class="demo-label">
      {workspace.field?.target.name ?? ''}
      <span class="dot" class:on={workspace.services.chat} title={workspace.services.chat ? 'Agent connected' : 'No agent'} aria-label={workspace.services.chat ? 'Agent connected' : 'No agent'}></span>agent
      <span class="dot" class:on={workspace.services.literature} title={workspace.services.literature ? 'ADS connected' : 'ADS not configured'} aria-label={workspace.services.literature ? 'ADS connected' : 'ADS not configured'}></span>ADS
    </span>
  </header>
  {#if landing}
    <div class="landing-layer" out:fade={{ duration: 280 }}>
      <Landing {workspace} onask={startWith} onexplore={explore} onopen={continueConversation} />
    </div>
  {/if}
  <main id="workspace" tabindex="-1" class="atlas-workspace" style:--chat-width={`${chatWidth}px`} bind:this={workspaceEl} inert={landing}>
    <!-- Drawers stay mounted and animate their size; inert keeps a closed one out of focus and reading order. -->
    <aside id="layers-drawer" class="left-drawer" class:collapsed={!layersOpen} inert={!layersOpen} aria-hidden={!layersOpen}>
      <div class="drawer-inner">
        <button class="drawer-close" aria-label="Close layers" title="Close layers" onclick={() => layersOpen = false}>×</button>
        <LayersPanel {workspace} onask={askInChat} />
      </div>
    </aside>
    <div class="map-column">
      <!-- Never conditionally mount the renderer: panel changes preserve Aladin. -->
      <SkyPanel {workspace} />
      <section id="results-drawer" class="results-drawer" aria-label="Workspace results">
        {#if resultsOpen}<Splitter bind:value={resultsHeight} min={120} max={resultsMax} grow="up" label="Results height" initial={RESULTS_HEIGHT} />{/if}
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
              }}>{tab}{tab === 'Plot' && workspace.figures.length ? ` (${workspace.figures.length})` : ''}</button>
            {/each}
          </div>
        </div>
        <div id="results-content" role="tabpanel" aria-labelledby={`result-tab-${tabs.indexOf(activeTab)}`} tabindex="0" class:collapsed={!resultsOpen} inert={!resultsOpen} style:height={`${resultsOpen ? resultsHeight : 0}px`}>
          <div class="results-body">
            {#if activeTab === 'Selected'}
              <SelectedPanel {workspace} onask={ask} onlookup={lookupSelection} />
            {:else if activeTab === 'Debug'}
              <DebugPanel {workspace} />
            {:else if activeTab === 'Plot'}
              <PlotPanel {workspace} onreveal={revealStep} />
            {:else if activeTab === 'Tables'}
              <TablePanel {workspace} />
            {:else if activeTab === 'Summary'}
              <FieldNotesPanel {workspace} view="summary" />
            {:else if activeTab === 'Literature'}
              <LiteraturePanel {workspace} onask={ask} />
            {:else if activeTab === 'Decisions'}
              <FieldNotesPanel {workspace} view="decisions" />
            {:else}
              <FieldNotesPanel {workspace} view="activity" />
            {/if}
          </div>
        </div>
      </section>
    </div>
    <aside id="chat-drawer" class="right-drawer" class:collapsed={!chatOpen} inert={!chatOpen} aria-hidden={!chatOpen}>
      <Splitter bind:value={chatWidth} min={260} max={chatMax} grow="left" label="Chat width" initial={CHAT_WIDTH} />
      <div class="drawer-inner">
        <ChatPanel {workspace} onclose={() => (chatOpen = false)} />
      </div>
    </aside>
  </main>
</div>
