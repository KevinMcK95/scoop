<script lang="ts">
  import type { Workspace } from '../workspace/state.svelte';
  import { activitySummary, groupMessages, stepLabel } from '../chat/activity';
  import { renderMarkdown } from '../chat/markdown';
  import SettingsPanel from './SettingsPanel.svelte';
  import { follow } from '../chat/follow';
  let { workspace, onclose }: { workspace: Workspace; onclose?: () => void } = $props();
  const row = $derived(workspace.selectedRow);
  const paper = $derived(workspace.papers.find((p) => p.id === workspace.publicationId));
  const live = $derived(workspace.services.chat);
  // Tool-call groups: open while any step runs; once done, collapsed unless the user opens them.
  let opened = $state<Record<string, boolean>>({});
  // Figures shown at full width, by step id.
  let wide = $state<Record<number, boolean>>({});
  let tab = $state<'chat' | 'history' | 'settings'>('chat');
  const when = (t: number) => {
    const minutes = Math.round((Date.now() - t) / 60000);
    return minutes < 1 ? 'just now' : minutes < 60 ? `${minutes} min ago` : minutes < 1440 ? `${Math.round(minutes / 60)} h ago`
      : new Date(t).toLocaleDateString();
  };
  // Messages after the last one you sent belong to the running turn.
  const turnStart = $derived(workspace.messages.findLast((m) => m.role === 'user')?.id ?? 0);


  const labels = { user: 'YOU', assistant: 'SCOOP', tool: 'TOOL', error: 'ERROR', note: 'NOTE' } as const;
  const visible = $derived(workspace.layers.filter((layer) => layer.visible).length);
  const status = (message: { role: string; status?: string }) => !message.status ? ''
    : message.role === 'assistant' ? (message.status === 'running' ? ' ···' : ' ⚠')
    : ` · ${message.status}`;
</script>

<section class="panel chat-panel" aria-labelledby="chat-heading">
  <header class="panel-heading chat-tabs">
    {#if onclose}<button class="text-button close-x" aria-label="Close chat" title="Close chat" onclick={onclose}>×</button>{/if}
    <div class="tabs" role="tablist" aria-label="Conversation">
      <button role="tab" aria-selected={tab === 'chat'} onclick={() => (tab = 'chat')}>Chat</button>
      <button role="tab" aria-selected={tab === 'history'} onclick={() => (tab = 'history')}>History{workspace.conversations.length ? ` (${workspace.conversations.length})` : ''}</button>
      <button role="tab" class="gear" aria-selected={tab === 'settings'} title="Settings" aria-label="Settings" onclick={() => (tab = 'settings')}>⚙</button>
    </div>
    <button class="text-button icon" title="New conversation" aria-label="New conversation" disabled={workspace.busy}
      onclick={() => { workspace.newConversation(); tab = 'chat'; }}>＋</button>
    <span class="dot" class:on={live && !workspace.busy} class:busy={live && workspace.busy}
    role="status" title={live ? (workspace.busy ? 'Working…' : 'Agent connected') : 'No agent'}
    aria-label={live ? (workspace.busy ? 'Working' : 'Agent connected') : 'No agent'}></span></header>
  {#if tab === 'settings'}
    <div class="history" role="tabpanel" aria-label="Settings"><SettingsPanel {workspace} /></div>
  {:else if tab === 'history'}
    <div class="history" role="tabpanel" aria-label="Conversation history">
      {#each workspace.conversations as conversation (conversation.id)}
        <div class="history-row" class:current={conversation.id === workspace.conversationId}>
          <button class="history-open" disabled={workspace.busy} onclick={() => { workspace.openConversation(conversation.id); tab = 'chat'; }}>
            <strong>{conversation.title}</strong>
            <small>{conversation.field ?? 'no field'} · {when(conversation.updatedAt)} · {conversation.messages.filter((m) => m.role === 'user').length} questions</small>
          </button>
          <button class="text-button icon remove" title="Delete conversation" aria-label={`Delete ${conversation.title}`}
            onclick={() => workspace.deleteConversation(conversation.id)}>×</button>
        </div>
      {:else}
        <p class="empty-state">No saved conversations yet. They are kept in this browser.</p>
      {/each}
      <p class="muted">Saved in this browser only. SCOOP remembers a reopened conversation until its server restarts.</p>
    </div>
  {:else}
  <div class="chat-scroll" use:follow={`${workspace.conversationId}:${turnStart}`}>
    <div class="intro-card">
      <h3>Ask about this patch of sky.</h3>
      <p>SCOOP sees the map as you do: the field, visible layers, what you selected. It answers on the map too, turning layers on or off, highlighting and labelling what it talks about. Undo reverts any change.</p>
      {#if !live}<p class="muted">No agent connected. Start <code>python -m scoop.server</code> with SCOOP_MODEL set.</p>{/if}
    </div>
    <div class="messages" role="log" aria-label="Conversation messages" aria-live="polite" aria-relevant="additions">
      {#each groupMessages(workspace.messages) as entry (entry.kind === 'message' ? entry.message.id : `a${entry.steps[0].id}`)}
        {#if entry.kind === 'activity'}
          {@const key = `a${entry.steps[0].id}`}
          {@const running = entry.steps.some((step) => step.status === 'running')}
          {@const current = workspace.busy && entry.steps[0].id > turnStart}
          {@const open = running || current || !!opened[key]}
          <div class="activity" aria-label="What SCOOP did">
            {#if !running && !current}
              <button class="activity-toggle" aria-expanded={open} onclick={() => (opened = { ...opened, [key]: !opened[key] })}>
                <span aria-hidden="true">{open ? '▾' : '▸'}</span> {activitySummary(entry.steps)}
              </button>
            {/if}
            {#if open}
              <ol class="steps">
                {#each entry.steps as step (step.id)}
                  <li id={`step-${step.id}`} class="step" class:running={step.status === 'running'} class:failed={step.status === 'error'}
                    title={step.status === 'error' ? 'This step did not succeed; SCOOP was told and can retry.' : ''}>
                    <span aria-hidden="true">{step.status === 'running' ? '◌' : step.status === 'error' ? '✗' : '✓'}</span> {stepLabel(step.text)}{step.status === 'running' ? '…' : ''}
                    {#if step.detail?.code}
                      <details class="step-detail">
                        <summary>code and output</summary>
                        <pre class="code">{step.detail.code}</pre>
                        {#if step.detail.output}<pre class="output">{step.detail.output}</pre>{/if}
                        {#if step.detail.error}<pre class="output failed">{step.detail.error}</pre>{/if}
                      </details>
                    {/if}
                  </li>
                {/each}
              </ol>
            {/if}
            <!-- Figures and sky images stay in view when the steps collapse. -->
            {#each entry.steps.filter((step) => step.detail?.image) as step (step.id)}
              <button class="step-image" class:wide={!!wide[step.id]} title={wide[step.id] ? 'Show smaller' : 'Show larger'}
                onclick={() => (wide = { ...wide, [step.id]: !wide[step.id] })}>
                <img src={step.detail!.image} alt={`Image from ${stepLabel(step.text)}`} />
              </button>
            {/each}
          </div>
        {:else}
          {@const message = entry.message}
          <article class:user-message={message.role === 'user'} class:assistant-message={message.role === 'assistant'}
            class:error-message={message.role === 'error'} class:note-message={message.role === 'note'} class="message">
            {#if message.role === 'assistant'}
              <span class="speaker" aria-label="SCOOP"><span aria-hidden="true">◉</span> SCOOP{status(message)}</span>
            {:else if message.role === 'user'}
              <span class="speaker">YOU</span>
            {:else}
              <span class="eyebrow">{labels[message.role]}</span>
            {/if}
            {#if message.role === 'assistant'}
              <div class="markdown">{@html renderMarkdown(message.text)}</div>
            {:else}
              <p>{message.text}</p>
            {/if}
            {#if message.scene}
              <button class="text-button scene-button" title="Restore the map as it was for this answer (layers, highlights, notes, view)"
                aria-label="Restore the map view for this answer" onclick={() => workspace.showScene(message)}>🗺 view</button>
            {/if}
            {#if message.contextNote}
              <p class="context-note">{message.contextNote}</p>
            {/if}
          </article>
        {/if}
      {/each}
      {#if workspace.busy}
        <!-- Right under the latest message, for the whole turn. -->
        <div class="scooping" role="status" aria-label="SCOOP is working"><span aria-hidden="true">◉</span> Scooping<span class="dots" aria-hidden="true"><span>.</span><span>.</span><span>.</span></span></div>
      {/if}
    </div>
  </div>
  <form class="composer" onsubmit={(event) => { event.preventDefault(); workspace.send(); }}>
    <div class="context-strip" aria-label="Context for next message">
      <span>◎ {row ? `${row.table} ${row.id}` : 'Nothing selected'}</span>
      <span>▤ {paper ? paper.id : 'No paper'}</span>
      <span class="muted">{workspace.field?.target.name ?? 'No field'} · {visible} layers on</span>
    </div>
    <label class="sr-only" for="chat-draft">Message</label>
    <textarea id="chat-draft" bind:value={workspace.draft} rows="3" placeholder="e.g. Which trusted QSOs fall inside JWST footprints?" maxlength="4000"
      onkeydown={(event) => {
      // Enter sends; Shift+Enter is a new line; never mid-composition (IME).
      if (event.key === 'Enter' && !event.shiftKey && !event.isComposing) { event.preventDefault(); workspace.send(); }
    }}></textarea>
    <div class="composer-actions"><span class="muted" title="Enter sends · Shift+Enter for a new line">↵ send · ⇧↵ new line</span>{#if workspace.busy}
        <button class="primary stop" type="button" aria-label="Stop" title="Stop SCOOP" onclick={() => workspace.stop()}>■</button>
      {:else}
        <button class="primary" type="submit" aria-label="Send" title="Send (↵)" disabled={!workspace.draft.trim()}>↗</button>
      {/if}</div>
  </form>
  {/if}
</section>
