import type { Publication, SkySource, SkyViewport } from './contracts';

// Invented positions and publications for interaction design, not catalog evidence.
export const sources: readonly SkySource[] = [
  { id: 'fx-src-a7', label: 'Demo source A', ra: 10.6847, dec: 41.2690, kind: 'Illustrative target' },
  { id: 'fx-src-k2', label: 'Demo source B', ra: 10.9100, dec: 41.3900, kind: 'Illustrative neighbor' },
  { id: 'fx-src-p9', label: 'Demo source C', ra: 10.4300, dec: 41.1300, kind: 'Illustrative neighbor' }
];

export const publications: readonly Publication[] = [
  { id: 'fx-pub-r4', title: 'Mapping a nearby galaxy: an illustrative observing strategy', authors: 'Example collaboration', year: 'Fixture 01', summary: 'A fictional methods note for exploring how a selected paper can travel alongside a sky target. Not a real publication or a scientific claim.' },
  { id: 'fx-pub-t8', title: 'Resolved populations across a wide field', authors: 'Demo research group', year: 'Fixture 02', summary: 'An invented literature card for testing independent paper selection. No ADS query, citation matching, or source association has been performed.' },
  { id: 'fx-pub-v3', title: 'From image context to follow-up questions', authors: 'Workspace design team', year: 'Fixture 03', summary: 'A fictional workflow example, included to demonstrate context capture rather than evidence retrieval.' }
];

export const initialViewport: SkyViewport = {
  ra: 10.6847, dec: 41.269, fov: 1.2, frame: 'ICRS',
  survey: 'P/DSS2/color', status: 'initial'
};
