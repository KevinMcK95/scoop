// What each tool did, in words; the chat shows these instead of tool output.
const STEPS: Record<string, string> = {
  update_map: 'Updated the map',
  describe_field: 'Read the field',
  load_field: 'Loaded a field',
  region: 'Checked a region',
  match: 'Matched sources to footprints',
  summarize: 'Computed statistics',
  lookup: 'Looked up SIMBAD',
  sky_image: 'Looked at the sky',
  overlaps: 'Found overlapping images',
  find_lvdb: 'Searched the Local Volume Database',
  run_field: 'Started archive queries',
  run_cone: 'Started a cone search',
  ads_search: 'Searched ADS',
  ads_paper: 'Read a paper',
  ads_citations: 'Followed citations',
  web_search: 'Searched the web',
  web_fetch: 'Read a web page',
  run_python: 'Ran Python',
  run_kernel: 'Ran Python in your environment'
};

export function stepLabel(tool: string): string {
  return STEPS[tool] ?? tool.replace(/_/g, ' ').replace(/^./, (c) => c.toUpperCase());
}

/** Consecutive tool messages collapse into one activity row between chat messages. */
export function groupMessages<T extends { role: string }>(messages: T[]): ({ kind: 'message'; message: T } | { kind: 'activity'; steps: T[] })[] {
  const out: ({ kind: 'message'; message: T } | { kind: 'activity'; steps: T[] })[] = [];
  for (const message of messages) {
    const last = out.at(-1);
    if (message.role !== 'tool') out.push({ kind: 'message', message });
    else if (last?.kind === 'activity') last.steps.push(message);
    else out.push({ kind: 'activity', steps: [message] });
  }
  return out;
}

/** One line saying what travelled with a message, instead of the raw context. */
export function contextSummary(context: Record<string, unknown>): string {
  const parts: string[] = [];
  if (typeof context.field === 'string') parts.push(context.field);
  const selection = context.selection as Record<string, unknown> | null | undefined;
  if (selection) parts.push(`selected ${selection.table} ${selection.id}`);
  const regions = context.userRegions as unknown[] | undefined;
  if (regions?.length) parts.push(`${regions.length} drawn region${regions.length === 1 ? '' : 's'}`);
  const layers = context.layers as { visible?: boolean }[] | undefined;
  if (layers) parts.push(`${layers.filter((l) => l.visible).length} layers on`);
  if (context.selectedPublication) parts.push('1 paper');
  return parts.length ? `with ${parts.join(' · ')}` : '';
}

/** The collapsed line for a finished group, e.g. "5 tool calls · 1 failed". */
export function activitySummary(steps: { status?: string }[]): string {
  const failed = steps.filter((step) => step.status === 'error').length;
  return `${steps.length} tool call${steps.length === 1 ? '' : 's'}${failed ? ` · ${failed} failed` : ''}`;
}
