export interface SkySource {
  id: string;
  label: string;
  ra: number;
  dec: number;
  kind: string;
}

export interface Publication {
  id: string;
  title: string;
  authors: string;
  year: string;
  summary: string;
  origin?: 'fixture' | 'ads';
  url?: string | null;
}

// Source and publication are intentionally independent, not a single union.
export interface WorkspaceSelection {
  sourceId: string | null;
  publicationId: string | null;
}

export interface SkyViewport {
  ra: number;
  dec: number;
  fov: number;
  frame: 'ICRS';
  survey: string;
  status: 'initial' | 'live' | 'unavailable';
}

// Future transport boundary: structured intent only, never executable JS.
export type LayerId = 'fixture-sources' | 'annotations';
export type ViewCommand =
  | { type: 'center'; ra: number; dec: number; fov: number }
  | { type: 'layer.visibility'; layerId: LayerId; visible: boolean }
  | { type: 'sources.highlight'; sourceIds: string[] }
  | { type: 'annotation.circle'; ra: number; dec: number; radius: number; label: string }
  | { type: 'annotations.clear' };

export interface ViewRequest {
  sequence: number;
  command: ViewCommand;
  // Set when the request came from an agent tool call; acknowledged back to it.
  commandId?: string;
  status: 'pending' | 'applied' | 'failed';
  error?: string;
}

export interface MapLayer {
  id: LayerId;
  label: string;
  origin: string;
  visible: boolean;
  count: number;
}

export interface CircleAnnotation {
  id: number;
  ra: number;
  dec: number;
  label: string;
  radius: number;
}

export interface MapContext {
  layers: MapLayer[];
  highlightedSourceIds: string[];
  annotations: CircleAnnotation[];
}

export interface WorkspaceSnapshot {
  selection: WorkspaceSelection;
  viewport: SkyViewport;
  map?: MapContext;
}

export interface ChatMessage {
  id: number;
  role: 'user' | 'demo' | 'assistant' | 'tool' | 'error';
  text: string;
  snapshot?: WorkspaceSnapshot;
  status?: 'running' | 'success' | 'error';
}

export function captureSnapshot(
  selection: WorkspaceSelection,
  viewport: SkyViewport,
  map?: MapContext
): WorkspaceSnapshot {
  return {
    selection: { ...selection }, viewport: { ...viewport },
    ...(map ? { map: { layers: map.layers.map((layer) => ({ ...layer })),
      highlightedSourceIds: [...map.highlightedSourceIds],
      annotations: map.annotations.map((annotation) => ({ ...annotation })) } } : {})
  };
}

export function isViewCommand(value: unknown): value is ViewCommand {
  if (!value || typeof value !== 'object') return false;
  const c = value as Record<string, unknown>;
  const position = typeof c.ra === 'number' && Number.isFinite(c.ra) && c.ra >= 0 && c.ra < 360 &&
    typeof c.dec === 'number' && Number.isFinite(c.dec) && c.dec >= -90 && c.dec <= 90;
  switch (c.type) {
    case 'center': return position && typeof c.fov === 'number' && Number.isFinite(c.fov) && c.fov > 0 && c.fov <= 180;
    case 'layer.visibility': return (c.layerId === 'fixture-sources' || c.layerId === 'annotations') && typeof c.visible === 'boolean';
    case 'sources.highlight': return Array.isArray(c.sourceIds) && c.sourceIds.length <= 100 && c.sourceIds.every((id) => typeof id === 'string' && id.length > 0);
    case 'annotation.circle': return position && typeof c.radius === 'number' && Number.isFinite(c.radius) && c.radius > 0 && c.radius <= 180 && typeof c.label === 'string' && c.label.trim().length > 0 && c.label.length <= 200;
    case 'annotations.clear': return true;
    default: return false;
  }
}

export function demoReply(snapshot: WorkspaceSnapshot): string {
  const { sourceId, publicationId } = snapshot.selection;
  return `Local demo only — captured source: ${sourceId ?? 'none'}; publication: ${publicationId ?? 'none'}. ` +
    `Sky context: RA ${snapshot.viewport.ra.toFixed(4)}°, Dec ${snapshot.viewport.dec.toFixed(4)}°, ` +
    `FoV ${snapshot.viewport.fov.toFixed(2)}° (${snapshot.viewport.status}). ` +
    'No model, archive search, or scientific interpretation was run.';
}
