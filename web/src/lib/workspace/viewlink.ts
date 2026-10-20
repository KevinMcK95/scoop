// A view as a URL fragment ("#view=…"): never sent to a server, so links carry no
// data beyond what is on screen. Compact JSON in base64url, versioned.
import type { Scene } from './contracts';

const VERSION = 1;

const toBase64Url = (text: string) => {
  const bytes = new TextEncoder().encode(text);
  let binary = '';
  for (const byte of bytes) binary += String.fromCharCode(byte);
  return btoa(binary).replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '');
};
const fromBase64Url = (text: string) => {
  const binary = atob(text.replace(/-/g, '+').replace(/_/g, '/'));
  return new TextDecoder().decode(Uint8Array.from(binary, (c) => c.charCodeAt(0)));
};

export function encodeView(scene: Scene): string {
  // Layer counts and other derived values are recomputed on load.
  const map = { ...scene.map, layers: scene.map.layers.map(({ count: _, ...layer }) => layer) };
  return `view=${toBase64Url(JSON.stringify({ v: VERSION, ...scene, map }))}`;
}

/** The scene in a "#view=…" fragment, or null if absent or not a valid view. */
export function decodeView(hash: string): Scene | null {
  const match = /(?:^#?|&)view=([\w-]+)/.exec(hash);
  if (!match) return null;
  try {
    const data = JSON.parse(fromBase64Url(match[1]));
    if (data?.v !== VERSION || !data.map || !Array.isArray(data.map.layers) || !data.viewport) return null;
    const { v: _, ...scene } = data;
    return scene as Scene;
  } catch {
    return null;
  }
}
