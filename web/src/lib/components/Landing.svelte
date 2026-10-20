<script lang="ts">
  import type { Workspace } from '../workspace/state.svelte';
  import { EXAMPLES } from '../landing/examples';
  let { workspace, onask, onexplore, onopen }: {
    workspace: Workspace;
    onask: (question: string) => void; // ask the first question: the workspace takes over
    onexplore: () => void; // straight to the sky map
    onopen: (conversationId: string) => void; // continue a saved conversation
  } = $props();
  let question = $state('');
  const recent = $derived(workspace.conversations.slice(0, 3));
  const live = $derived(workspace.services.chat);
  function submit() {
    const text = question.trim();
    if (text) onask(text);
  }
</script>

<section class="landing" aria-labelledby="landing-title">
  <div class="landing-inner">
    <h2 id="landing-title" class="landing-title"><span aria-hidden="true">◉</span> SCOOP</h2>
    <p class="landing-tagline">Ask about any patch of sky. SCOOP gathers archive data and literature, shows it on a sky map, and explains what it finds.</p>

    <form class="landing-ask" onsubmit={(e) => { e.preventDefault(); submit(); }}>
      <label class="sr-only" for="landing-question">Your question</label>
      <!-- svelte-ignore a11y_autofocus -->
      <textarea id="landing-question" bind:value={question} rows="2" maxlength="4000" autofocus
        placeholder="e.g. Which quasars behind M31 have HST imaging?"
        onkeydown={(e) => { if (e.key === 'Enter' && !e.shiftKey && !e.isComposing) { e.preventDefault(); submit(); } }}></textarea>
      <button class="primary" type="submit" aria-label="Ask" title="Ask (↵)" disabled={!question.trim()}>↗</button>
    </form>
    {#if !live}<p class="muted landing-note">No agent connected yet: start <code>python -m scoop.server</code> with a model set, or add one in ⚙ Settings.</p>{/if}

    <div class="landing-examples" aria-label="Suggested questions">
      {#each EXAMPLES as example (example.label)}
        <button class="landing-example" title={example.prompt} onclick={() => onask(example.prompt)}>
          <span aria-hidden="true">{example.icon}</span> {example.label}
        </button>
      {/each}
    </div>

    <div class="landing-foot">
      {#if recent.length}
        <div class="landing-recent">
          <span class="eyebrow">CONTINUE</span>
          {#each recent as conversation (conversation.id)}
            <button class="text-button" onclick={() => onopen(conversation.id)} title={conversation.field ?? ''}>{conversation.title}</button>
          {/each}
        </div>
      {/if}
      <button class="text-button landing-explore" onclick={onexplore}>Open the sky map →</button>
    </div>
  </div>
</section>
