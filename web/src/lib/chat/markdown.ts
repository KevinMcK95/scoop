// Markdown for assistant answers. Model output is untrusted: raw HTML is shown
// as text, only http(s) links are clickable, and remote images become links.
import { Marked, type TokenizerAndRendererExtension } from 'marked';
import katex from 'katex';

const escape = (text: string) =>
  text.replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[c]!);
const safeUrl = (href: string) => /^https?:\/\//i.test(href.trim());

// An ADS bibcode: year, journal (5), volume/qualifier/page (9; digits for arXiv), initial — 19 characters.
const BIBCODE = /^(\d{4}[A-Za-z&.]{5}[\w.]{9}[A-Z])(?![\w.])/;
export const adsUrl = (bibcode: string) => `https://ui.adsabs.harvard.edu/abs/${encodeURIComponent(bibcode)}/abstract`;

/** The bibcode in an LVDB-style reference ("Munoz2018ApJ...860...66M"), or null. */
export function bibcodeOf(reference: string): string | null {
  const tail = reference.slice(-19);
  return BIBCODE.test(tail) ? tail : null;
}

// Within plain text: not part of a longer word, and a trailing "." is punctuation.
const BIBCODES = /(?<![\w.&])(\d{4}[A-Za-z&.]{5}[\w.]{9}[A-Z])(?!\w)/g;
let inLink = 0;

function linkBibcodes(text: string): string {
  if (inLink) return escape(text);
  let out = '';
  let last = 0;
  for (const match of text.matchAll(BIBCODES)) {
    out += escape(text.slice(last, match.index)) +
      `<a href="${escape(adsUrl(match[1]))}" target="_blank" rel="noreferrer noopener" class="bibcode">${escape(match[1])}</a>`;
    last = match.index! + match[0].length;
  }
  return out + escape(text.slice(last));
}

// Maths: $$…$$ and \[…\] display, $…$ and \(…\) inline. A $ must hug its content
// and the closing $ must not precede a digit, so "$5 and $10" stays text. KaTeX
// runs without trust (no \href or HTML) and shows bad input as source.
function tex(source: string, displayMode: boolean): string {
  return katex.renderToString(source, { displayMode, throwOnError: false, trust: false, output: 'htmlAndMathml' });
}
const mathBlock: TokenizerAndRendererExtension = {
  name: 'mathBlock',
  level: 'block',
  start: (src) => src.match(/\$\$|\\\[/)?.index,
  tokenizer(src) {
    const match = /^(?:\$\$([\s\S]+?)\$\$|\\\[([\s\S]+?)\\\])(?:\n|$)/.exec(src);
    if (match) return { type: 'mathBlock', raw: match[0], text: (match[1] ?? match[2]).trim() };
  },
  renderer: (token) => `<div class="math">${tex(token.text as string, true)}</div>\n`
};
const mathInline: TokenizerAndRendererExtension = {
  name: 'mathInline',
  level: 'inline',
  start: (src) => src.match(/\$|\\\(/)?.index,
  tokenizer(src) {
    const match = /^\$\$([^$]+?)\$\$|^\$(?!\s)([^$\n]+?)(?<!\s)\$(?!\d)|^\\\(([\s\S]+?)\\\)/.exec(src);
    if (match) return { type: 'mathInline', raw: match[0], text: (match[1] ?? match[2] ?? match[3]).trim(), display: !!match[1] };
  },
  renderer: (token) => tex(token.text as string, !!token.display)
};

const markdown = new Marked({
  extensions: [mathBlock, mathInline],
  gfm: true,
  breaks: true,
  renderer: {
    // Plain text runs only: code spans and blocks have their own renderers.
    text(token) {
      if ('tokens' in token && token.tokens) return this.parser.parseInline(token.tokens);
      // Loose list items: marked has already rendered this text through these renderers
      // and flags it escaped; escaping it again showed the HTML as text.
      if ('escaped' in token && token.escaped) return token.text;
      return linkBibcodes(token.text);
    },
    html({ text }) {
      return escape(text);
    },
    link({ href, title, tokens }) {
      inLink++;
      const label = this.parser.parseInline(tokens);
      inLink--;
      if (!safeUrl(href)) return label;
      return `<a href="${escape(href)}" target="_blank" rel="noreferrer noopener"${title ? ` title="${escape(title)}"` : ''}>${label}</a>`;
    },
    image({ href, text }) {
      const label = escape(text || 'image');
      return safeUrl(href) ? `<a href="${escape(href)}" target="_blank" rel="noreferrer noopener">${label}</a>` : label;
    }
  }
});

export function renderMarkdown(source: string): string {
  return markdown.parse(source, { async: false }) as string;
}
