import { describe, expect, it } from 'vitest';
import { bibcodeOf, renderMarkdown } from './markdown';

describe('assistant markdown', () => {
  it('renders the formatting an answer uses', () => {
    const html = renderMarkdown('**13** Gaia-CRF3 QSOs:\n\n- `1433159736894983168` (20.5 yr)\n- see [ADS](https://ui.adsabs.harvard.edu/abs/2020ApJ...1A)\n\n| QSO | yr |\n|---|---|\n| a | 1 |');
    expect(html).toContain('<strong>13</strong>');
    expect(html).toContain('<li><code>1433159736894983168</code> (20.5 yr)</li>');
    expect(html).toContain('<a href="https://ui.adsabs.harvard.edu/abs/2020ApJ...1A" target="_blank" rel="noreferrer noopener">ADS</a>');
    expect(html).toContain('<table>');
  });

  it('links bare bibcodes to ADS, but not in code or inside longer tokens', () => {
    const html = renderMarkdown('Distance from 2024AJ....167..247B; spectra in 2026arXiv260210202G.');
    expect(html).toContain('<a href="https://ui.adsabs.harvard.edu/abs/2024AJ....167..247B/abstract" target="_blank" rel="noreferrer noopener" class="bibcode">2024AJ....167..247B</a>');
    expect(html).toContain('abs/2026arXiv260210202G/abstract');
    expect(renderMarkdown('`2024AJ....167..247B`')).not.toContain('<a ');
    expect(renderMarkdown('x2024AJ....167..247B')).not.toContain('<a ');
    expect(renderMarkdown('In 2024 the field had 1509 observations.')).not.toContain('<a ');
    expect(renderMarkdown('See [Muñoz](https://ui.adsabs.harvard.edu/abs/2018ApJ...860...66M) 2018ApJ...860...66M'))
      .toBe('<p>See <a href="https://ui.adsabs.harvard.edu/abs/2018ApJ...860...66M" target="_blank" rel="noreferrer noopener">Muñoz</a> <a href="https://ui.adsabs.harvard.edu/abs/2018ApJ...860...66M/abstract" target="_blank" rel="noreferrer noopener" class="bibcode">2018ApJ...860...66M</a></p>\n');
    expect(renderMarkdown('A & B < C')).toBe('<p>A &amp; B &lt; C</p>\n');
  });

  it('reads the bibcode out of an LVDB reference', () => {
    expect(bibcodeOf('Munoz2018ApJ...860...66M')).toBe('2018ApJ...860...66M');
    expect(bibcodeOf('Geha2026arXiv260210202G')).toBe('2026arXiv260210202G');
    expect(bibcodeOf('private communication')).toBeNull();
  });

  it('renders LaTeX in the usual delimiters, and leaves prices alone', () => {
    for (const source of ['$\\sigma_v = 9.1$', '\\(\\sigma_v = 9.1\\)', '$$\\sigma_v = 9.1$$', '\\[\\sigma_v = 9.1\\]']) {
      const html = renderMarkdown(source);
      expect(html, source).toContain('class="katex"');
      expect(html, source).toContain('σ');
    }
    expect(renderMarkdown('$$\\frac{a}{b}$$')).toContain('katex-display');
    expect(renderMarkdown('It costs $5 and $10 today.')).not.toContain('katex');
    expect(renderMarkdown('`$x$` in code')).not.toContain('katex');
    // Untrusted input: no links or HTML out of TeX, errors shown as source.
    const href = renderMarkdown('$\\href{javascript:alert(1)}{x}$');
    expect(href).not.toContain('<a ');
    expect(href).not.toContain('href=');
    expect(renderMarkdown('$\\frac{$')).not.toContain('<script');
  });

  it('renders loose lists (items separated by blank lines) once, not as escaped HTML', () => {
    const html = renderMarkdown('- **The spur** is unusual.\n\n- See 2020ApJ...892L..37B');
    expect(html).toContain('<strong>The spur</strong>');
    expect(html).not.toContain('&lt;');
    expect(html.match(/class="bibcode"/g)).toHaveLength(1);
    // Raw HTML from the model is still shown as text, loose list or not.
    expect(renderMarkdown('- <b>x</b>\n\n- y')).toContain('&lt;b&gt;');
  });

  it('never passes raw HTML or unsafe links through', () => {
    for (const attack of ['<script>alert(1)</script>', '<img src=x onerror=alert(1)>', 'hi <b onclick="x()">b</b>']) {
      const html = renderMarkdown(attack);
      expect(html).not.toMatch(/<(script|img|b)[\s>]/);
      expect(html).toContain('&lt;');
    }
    expect(renderMarkdown('[x](javascript:alert(1))')).toBe('<p>x</p>\n');
    expect(renderMarkdown('[x](data:text/html,hi)')).not.toContain('href');
    expect(renderMarkdown('![sky](https://example.org/a.png)')).not.toContain('<img');
    expect(renderMarkdown('<a href="https://x.org">x</a>')).not.toContain('<a ');
  });
});
