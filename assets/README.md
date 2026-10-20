# SCOOP logo

The mark is a **cone search** — an apex at the observer opening onto one patch of
sky — which is also, read the other way, a scoop. The disc holds what SCOOP hands
back: everything in that patch.

| File | Use |
| --- | --- |
| `scoop-mark.svg` | Primary mark. Use at 48px and above. |
| `scoop-mark-small.svg` | Simplified mark (no waffle texture, one star) for favicons and anything under ~48px. |
| `scoop-logo.svg` | Horizontal lockup: mark + wordmark + expansion. |
| `scoop-mark-512.png`, `scoop-favicon-32.png`, `scoop-logo-1200.png` | Raster exports, transparent background. |

## Colors

Taken from the web workspace theme in `web/src/app.css`.

| Role | Hex |
| --- | --- |
| Accent (ring, stars, observer) | `#79d9cb` |
| Cone | `#57c3bb` |
| Sky (disc fill) | `#0e1a28` |
| Wordmark | `#dce4ef` |
| Expansion text | `#9cacc0` |

Works on the app background (`#080f18`) and on white. The wordmark color is the
only thing to change on light backgrounds — swap `#dce4ef` for `#16222f`.

## Notes

- The wordmark uses live `<text>` with a system-font stack rather than outlines,
  so it renders with whatever sans the viewer has. Convert to paths before using
  it anywhere the exact letterforms matter.
- Clear space: one cone-width on all sides.
- Regenerate the PNGs with, e.g.
  `magick -density 2400 -background none scoop-mark.svg -resize 512x512 scoop-mark-512.png`.
