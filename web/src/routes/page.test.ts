import { describe, expect, it } from 'vitest';
import { render } from 'svelte/server';
import Page from './+page.svelte';

// The dev and production servers render the page before hydration; browser-only
// globals (window, localStorage) must not be touched during that render.
describe('server rendering', () => {
  it('renders the workspace without browser globals', () => {
    const { body } = render(Page);
    expect(body).toContain('id="workspace"');
    expect(body).toContain('role="separator"');
  });

  it('opens on the start page, over the mounted workspace', () => {
    const { body } = render(Page);
    expect(body).toContain('id="landing-title"');
    expect(body).toContain('Suggested questions');
    expect(body).toMatch(/<main[^>]*id="workspace"[^>]*inert/);
  });
});
