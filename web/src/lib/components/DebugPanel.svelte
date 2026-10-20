<script lang="ts">
  import type { Workspace } from '../workspace/state.svelte';
  import { bar, cacheShare, seconds, stepTitle, tokens, type Trace, type TraceStep } from '../chat/trace';
  let { workspace }: { workspace: Workspace } = $props();

  let traces = $state<Trace[]>([]);
  let error = $state('');
  let loading = $state(false);
  let openTurn = $state<string | null>(null);
  let openStep = $state<string | null>(null);

  async function refresh() {
    loading = true;
    try {
      traces = await workspace.loadTraces();
      error = '';
      openTurn ??= traces[0]?.request_id ?? null;
    } catch (e) {
      error = e instanceof Error ? e.message : String(e);
    } finally {
      loading = false;
    }
  }
  // Reload when the conversation changes and whenever a turn finishes.
  $effect(() => {
    void workspace.conversationId;
    if (!workspace.busy) void refresh();
  });

  const json = (value: unknown) => (typeof value === 'string' ? value : JSON.stringify(value, null, 2));
  const time = (iso: string) => new Date(iso).toLocaleTimeString();
  const stepKey = (trace: Trace, i: number) => `${trace.request_id}:${i}`;
  const failed = (step: TraceStep) => !!step.error || step.status === 'error';
  async function copy(trace: Trace) {
    try { await navigator.clipboard.writeText(JSON.stringify(trace, null, 2)); } catch { /* clipboard blocked */ }
  }
</script>

<section class="debug-panel" aria-label="Debug traces">
  <div class="debug-tools">
    <span class="muted">{traces.length} turn{traces.length === 1 ? '' : 's'} in this conversation · stored on this machine</span>
    <button class="text-button icon" title="Reload traces" aria-label="Reload traces" disabled={loading} onclick={refresh}>↻</button>
  </div>
  {#if error}<p class="muted">{error}</p>{/if}
  {#if !traces.length && !error}
    <p class="empty-state">{loading ? 'Loading…' : 'No traces yet. Ask SCOOP something; each turn appears here.'}</p>
  {/if}

  {#each traces as trace (trace.request_id)}
    {@const share = cacheShare(trace)}
    <div class="trace" class:failed={!!trace.error}>
      <button class="trace-head" aria-expanded={openTurn === trace.request_id}
        onclick={() => (openTurn = openTurn === trace.request_id ? null : trace.request_id)}>
        <span aria-hidden="true">{openTurn === trace.request_id ? '▾' : '▸'}</span>
        <span class="trace-question" title={trace.question}>{trace.question}</span>
        <span class="trace-stats">
          {time(trace.started_at)} · {seconds(trace.duration_ms)} · {trace.totals.model_calls} model · {trace.totals.tool_calls} tools{trace.totals.tool_failures ? ` (${trace.totals.tool_failures} ✗)` : ''}
          · {tokens(trace.totals.input_tokens)} in{share !== null ? ` (${Math.round(share * 100)}% cached)` : ''} / {tokens(trace.totals.output_tokens)} out
        </span>
      </button>
      {#if openTurn === trace.request_id}
        {#if trace.error}<p class="trace-error">✗ {trace.error}</p>{/if}
        <ol class="waterfall">
          {#each trace.steps as step, i (i)}
            {@const key = stepKey(trace, i)}
            {@const span = bar(step, trace.duration_ms)}
            <li class:failed={failed(step)} class={step.kind}>
              <button class="step-row" aria-expanded={openStep === key} onclick={() => (openStep = openStep === key ? null : key)}>
                <span class="step-name">{step.kind === 'model' ? '◆' : '▸'} {stepTitle(step)}</span>
                <span class="step-bar"><span style={`left:${span.left}%;width:${span.width}%`}></span></span>
                <span class="step-time">{seconds(step.duration_ms)}</span>
              </button>
              {#if openStep === key}
                <div class="step-body">
                  {#if step.kind === 'model'}
                    <p class="muted">{step.model ?? 'model'} · {tokens(step.usage?.input)} in ({tokens(step.usage?.cached)} cached) · {tokens(step.usage?.output)} out ·
                      sent {step.sent_count} messages{i > 0 ? `, ${step.sent?.length ?? 0} new since the last call` : ''}</p>
                    {#each step.sent ?? [] as message, m (m)}
                      <details class="trace-message">
                        <summary><span class="role">{message.role}</span> {message.content.slice(0, 120).replace(/\s+/g, ' ')}{message.content.length > 120 ? '…' : ''}</summary>
                        <pre>{message.content}</pre>
                        {#if message.tool_calls}<pre>{json(message.tool_calls)}</pre>{/if}
                      </details>
                    {/each}
                    {#if step.reply}
                      <h5>Reply</h5>
                      {#if step.reply.content}<pre>{step.reply.content}</pre>{/if}
                      {#each step.reply.tool_calls ?? [] as call, c (c)}<pre>{call.name}({json(call.args)})</pre>{/each}
                    {/if}
                    {#if step.error}<pre class="failed">{step.error}</pre>{/if}
                  {:else}
                    <h5>Arguments</h5>
                    <pre>{json(step.args)}</pre>
                    <h5>Result · {step.status ?? 'running'}{step.image ? ' · with image' : ''}</h5>
                    <pre class:failed={failed(step)}>{step.result ?? ''}</pre>
                  {/if}
                </div>
              {/if}
            </li>
          {/each}
        </ol>
        <div class="trace-foot">
          <span class="muted">request {trace.request_id}</span>
          <button class="text-button" title="Copy this trace as JSON (to share a bug)" onclick={() => copy(trace)}>⧉ copy JSON</button>
        </div>
      {/if}
    </div>
  {/each}
</section>
