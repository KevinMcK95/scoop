// Minimal boundary for the untyped upstream package. Keep vendor details out of state.
import { separation } from '../field/filter';
import type { RowRef } from '../field/types';

export type AladinLayer = AladinCatalog | AladinOverlay;
export interface AladinViewer {
  getRaDec(): [number, number];
  getFov(): [number, number];
  gotoRaDec(ra: number, dec: number): void;
  setFoV(fov: number): void;
  setBaseImageLayer(id: string): void;
  addCatalog(catalog: AladinCatalog): void;
  addOverlay(overlay: AladinOverlay): void;
  removeOverlay(layer: AladinLayer): void;
  on(event: 'positionChanged' | 'zoomChanged' | 'objectClicked' | 'footprintClicked' | 'click', callback: (value: unknown) => void): void;
  pix2world?(x: number, y: number): [number, number] | undefined;
}
export type AladinSource = object;
export interface AladinCatalog { addSources(sources: AladinSource[]): void; show(): void; hide(): void }
export interface AladinOverlay { add(shape: unknown): void; show(): void; hide(): void }
export interface AladinAPI {
  init: Promise<unknown>;
  aladin(container: string, options: Record<string, unknown>): AladinViewer;
  catalog(options: Record<string, unknown>): AladinCatalog;
  source(ra: number, dec: number, data: Record<string, unknown>): AladinSource;
  graphicOverlay(options: Record<string, unknown>): AladinOverlay;
  circle(ra: number, dec: number, radius: number, options?: Record<string, unknown>): unknown;
  polygon(points: [number, number][], options?: Record<string, unknown>): unknown;
  footprintsFromSTCS(stcs: string, options?: Record<string, unknown>): Record<string, unknown>[];
}

/**
 * A circle drawn by dragging from its center (x0, y0) to its edge (x1, y1), in
 * map pixels; null if the drag was too short or left the sky.
 */
export function circleFromDrag(pix2world: (x: number, y: number) => [number, number] | undefined | null,
  x0: number, y0: number, x1: number, y1: number, minPixels = 4): { ra: number; dec: number; radiusArcmin: number } | null {
  if (Math.hypot(x1 - x0, y1 - y0) < minPixels) return null;
  const center = pix2world(x0, y0);
  const edge = pix2world(x1, y1);
  if (!center || !edge || ![...center, ...edge].every(Number.isFinite)) return null;
  const ra = ((center[0] % 360) + 360) % 360;
  return { ra, dec: center[1], radiusArcmin: separation(ra, center[1], edge[0], edge[1]) * 60 };
}

/**
 * A box drawn by dragging between opposite corners, as a sky polygon (each
 * screen corner converted, so it follows the projection). Null if too small.
 */
export function boxFromDrag(pix2world: (x: number, y: number) => [number, number] | undefined | null,
  x0: number, y0: number, x1: number, y1: number, minPixels = 4) {
  if (Math.abs(x1 - x0) < minPixels || Math.abs(y1 - y0) < minPixels) return null;
  const corners = [[x0, y0], [x1, y0], [x1, y1], [x0, y1]].map(([x, y]) => pix2world(x, y));
  if (corners.some((c) => !c || !c.every(Number.isFinite))) return null;
  const points = (corners as [number, number][]).map(([ra, dec]) => [((ra % 360) + 360) % 360, dec] as [number, number]);
  const center = pix2world((x0 + x1) / 2, (y0 + y1) / 2);
  if (!center) return null;
  return { points, ra: ((center[0] % 360) + 360) % 360, dec: center[1],
    widthArcmin: separation(...points[0], ...points[1]) * 60, heightArcmin: separation(...points[1], ...points[2]) * 60 };
}

/**
 * Zoom toward the cursor: after changing the field of view, move the center by
 * how far the sky point under the cursor drifted, so it stays put.
 */
export function zoomAnchor(center: [number, number], before: [number, number], after: [number, number]): [number, number] {
  const dRa = ((before[0] - after[0] + 540) % 360) - 180;
  const ra = (((center[0] + dRa) % 360) + 360) % 360;
  const dec = Math.max(-90, Math.min(90, center[1] + (before[1] - after[1])));
  return [ra, dec];
}

/** Field-of-view factor for one wheel event: smooth for trackpads, a step for mouse wheels. */
export function wheelFactor(deltaY: number, deltaMode: number): number {
  const pixels = deltaMode === 1 ? deltaY * 16 : deltaMode === 2 ? deltaY * 400 : deltaY;
  return Math.exp(Math.max(-200, Math.min(200, pixels)) * 0.0015);
}

/** A polygon from clicked vertices (map pixels) as a sky polygon; null if < 3 or off the sky. */
export function polygonFromClicks(pix2world: (x: number, y: number) => [number, number] | undefined | null,
  vertices: { x: number; y: number }[]) {
  if (vertices.length < 3 || vertices.length > 64) return null;
  const points = vertices.map(({ x, y }) => pix2world(x, y));
  if (points.some((p) => !p || !p.every(Number.isFinite))) return null;
  const cx = vertices.reduce((s, v) => s + v.x, 0) / vertices.length;
  const cy = vertices.reduce((s, v) => s + v.y, 0) / vertices.length;
  const center = pix2world(cx, cy);
  if (!center) return null;
  const wrap = (ra: number) => ((ra % 360) + 360) % 360;
  return { points: (points as [number, number][]).map(([ra, dec]) => [wrap(ra), dec] as [number, number]),
    ra: wrap(center[0]), dec: center[1] };
}

/** The row a clicked source or footprint was drawn from (set by the renderer). */
export function rowFromClick(value: unknown): RowRef | null {
  if (!value || typeof value !== 'object') return null;
  const holder = 'data' in value && value.data && typeof value.data === 'object' ? value.data : value;
  const ref = (holder as { _ref?: unknown })._ref;
  if (!ref || typeof ref !== 'object') return null;
  const { table, id } = ref as Record<string, unknown>;
  return typeof table === 'string' && typeof id === 'string' ? { table, id } : null;
}
