import type { Camera, MapAction, MapState, Origin } from '../field/types';

export interface Publication {
  id: string;
  title: string;
  authors: string;
  year: string;
  summary: string;
  url: string | null;
}

export interface SkyViewport extends Camera {
  status: 'initial' | 'live' | 'unavailable';
}

// What the map looked like when an answer was given; "Show on map" restores it.
export interface Scene {
  field?: string | null;
  viewport: Camera;
  survey: string;
  map: MapState;
}

export interface ChatMessage {
  id: number;
  role: 'user' | 'assistant' | 'tool' | 'error' | 'note';
  text: string;
  status?: 'running' | 'success' | 'error';
  scene?: Scene;
  // One line about what travelled with a message ("with draco_1 · 1 drawn region …").
  contextNote?: string;
}

// A batch of map actions, applied all-or-nothing once the renderer is ready.
export interface ActionRequest {
  sequence: number;
  actions: MapAction[];
  origin: Origin;
  commandId?: string;
  status: 'pending' | 'applied' | 'failed';
  error?: string;
}

export const DEFAULT_SURVEY = 'CDS/P/DSS2/color';
// Draco dSph, the smoke tests' default field, until a field is loaded.
export const initialViewport: SkyViewport = { ra: 260.0517, dec: 57.9153, fov: 1.5, status: 'initial' };
