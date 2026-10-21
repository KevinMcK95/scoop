<script lang="ts">
  import type { Workspace } from '../workspace/state.svelte';
  import type { SettingName, SettingsView } from '../api/client';
  let { workspace }: { workspace: Workspace } = $props();
  // Endpoints and models offered by default; anything OpenAI-compatible works.
  const ENDPOINTS = [
    { label: 'OpenRouter', url: 'https://openrouter.ai/api/v1', models: ['openai/gpt-6-luna'] },
    { label: 'Flatiron inference', url: 'https://inference.flatironinstitute.org/v1',
      models: ['moonshotai/Kimi-K3', 'zai-org/GLM-5.2-FP8', 'mistralai/Mistral-Medium-3.5-128B'] }
  ];
  let view = $state<SettingsView | null>(null);
  let model = $state('');
  let baseUrl = $state('');
  let apiKey = $state('');
  let adsToken = $state('');
  let kernel = $state('');
  let kernelCwd = $state('');
  let status = $state('');
  let saving = $state(false);
  const endpoint = $derived(ENDPOINTS.find((e) => e.url === baseUrl));
  const from = (name: SettingName) =>
    view?.sources?.[name] === 'settings' ? 'set here' : view?.sources?.[name] === 'environment' ? 'from environment' : '';

  async function load() {
    try {
      view = await workspace.backend!.getSettings();
      model = view.model ?? '';
      baseUrl = view.base_url ?? '';
      kernel = view.kernel ?? 'off';
      kernelCwd = view.kernel_cwd ?? '';
    } catch (error) {
      status = `Settings unavailable: ${error instanceof Error ? error.message : error}`;
    }
  }
  $effect(() => { if (workspace.backend) void load(); });

  async function save(extra: Record<string, string> = {}) {
    // Send only what changed; secrets are sent once and never come back.
    const changes: Record<string, string> = { ...extra };
    if (model !== (view?.model ?? '')) changes.model = model.trim();
    if (baseUrl !== (view?.base_url ?? '')) changes.base_url = baseUrl.trim();
    if (apiKey) changes.api_key = apiKey.trim();
    if (adsToken) changes.ads_token = adsToken.trim();
    if (kernel !== (view?.kernel ?? 'off')) changes.kernel = kernel;
    if (kernelCwd !== (view?.kernel_cwd ?? '')) changes.kernel_cwd = kernelCwd.trim();
    if (!Object.keys(changes).length) { status = 'Nothing changed.'; return; }
    saving = true;
    try {
      view = await workspace.backend!.saveSettings(changes);
      apiKey = adsToken = '';
      kernel = view.kernel ?? 'off';
      kernelCwd = view.kernel_cwd ?? '';
      await workspace.refreshServices();
      status = view.problems?.length ? `Saved, but: ${view.problems.join('; ')}` : 'Saved ✓';
    } catch (error) {
      status = error instanceof Error ? error.message : String(error);
    } finally {
      saving = false;
    }
  }
</script>

<form class="settings" onsubmit={(e) => { e.preventDefault(); save(); }}>
  <p class="muted">Values saved here win; anything not set here comes from the server's environment (SCOOP_MODEL, SCOOP_MODEL_BASE_URL, SCOOP_MODEL_API_KEY, ADS_API_TOKEN). Saved on the server in ~/.config/scoop/settings.json, readable only by you; keys never come back to the browser.</p>

  <label for="set-endpoint">Model endpoint</label>
  <select id="set-endpoint" value={endpoint ? baseUrl : 'custom'} onchange={(e) => {
    const chosen = ENDPOINTS.find((x) => x.url === e.currentTarget.value);
    if (chosen) { baseUrl = chosen.url; if (!chosen.models.includes(model)) model = chosen.models[0]; }
  }}>
    {#each ENDPOINTS as e}<option value={e.url}>{e.label}</option>{/each}
    <option value="custom">Custom (OpenAI-compatible)…</option>
  </select>
  {#if !endpoint}<input aria-label="Endpoint URL" bind:value={baseUrl} placeholder="https://…/v1" />{/if}

  <label for="set-model">Model</label>
  <input id="set-model" bind:value={model} list="model-options" placeholder="e.g. openai/gpt-6-luna" />
  <datalist id="model-options">{#each endpoint?.models ?? [] as m}<option value={m}></option>{/each}</datalist>

  <label for="set-key">API key</label>
  <div class="secret">
    <input id="set-key" type="password" autocomplete="off" bind:value={apiKey}
      placeholder={view?.api_key_set ? `${view.api_key_hint ?? '…'} · ${from('api_key')} · type to replace` : 'not set'} />
    {#if view?.sources?.api_key === 'settings'}<button type="button" class="text-button icon" title="Remove the key saved here (falls back to the server's environment, if set)" aria-label="Remove saved API key" onclick={() => save({ api_key: '' })}>×</button>{/if}
  </div>

  <label for="set-ads">ADS API token</label>
  <div class="secret">
    <input id="set-ads" type="password" autocomplete="off" bind:value={adsToken}
      placeholder={view?.ads_token_set ? `${view.ads_token_hint ?? '…'} · ${from('ads_token')} · type to replace` : 'not set'} />
    {#if view?.sources?.ads_token === 'settings'}<button type="button" class="text-button icon" title="Remove the token saved here (falls back to the server's environment, if set)" aria-label="Remove saved ADS token" onclick={() => save({ ads_token: '' })}>×</button>{/if}
  </div>
  <p class="muted">Get one at <a href="https://ui.adsabs.harvard.edu/user/settings/token" target="_blank" rel="noreferrer">ui.adsabs.harvard.edu</a>.</p>

  <label for="set-kernel">Analysis kernel (run_kernel)</label>
  <select id="set-kernel" bind:value={kernel}>
    <option value="off">Off</option>
    {#each view?.kernels ?? [] as k}<option value={k.name}>{k.display_name} ({k.name})</option>{/each}
    {#if kernel !== 'off' && !(view?.kernels ?? []).some((k) => k.name === kernel)}<option value={kernel}>{kernel} (not installed)</option>{/if}
  </select>
  <label for="set-kernel-cwd">Kernel working directory</label>
  <input id="set-kernel-cwd" bind:value={kernelCwd} placeholder="~/.cache/scoop/kernel" />
  <p class="muted">{from('kernel') === 'from environment' ? 'From SCOOP_KERNEL. ' : ''}The agent's code runs in this Jupyter kernel with your packages and files: <strong>not sandboxed</strong>. Variables are kept between calls; changing the kernel or directory starts a fresh one. Add an environment with <code>python -m ipykernel install --user --name &lt;name&gt;</code> inside it.{view?.kernel && view.kernel_on === false ? ' The chosen kernel did not start; see the message below.' : ''}</p>

  <div class="settings-actions">
    <span class="muted" role="status">{status}</span>
    <button class="primary" type="submit" disabled={saving || !view}>Save</button>
  </div>
</form>

<div class="settings developer">
  <label class="check"><input type="checkbox" checked={workspace.debug} onchange={(e) => (workspace.debug = e.currentTarget.checked)} />
    Developer: show the Debug tab</label>
  <p class="muted">Each turn's model calls and tool calls, with what was sent, timings and tokens. Traces stay on this machine.</p>
</div>
