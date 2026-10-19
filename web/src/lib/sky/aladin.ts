// Minimal boundary for the untyped upstream package. Keep vendor details out of state.
export interface AladinViewer {
  getRaDec(): [number, number];
  getFov(): [number, number];
  gotoRaDec(ra: number, dec: number): void;
  setFoV(fov: number): void;
  addCatalog(catalog: AladinCatalog): void;
  addOverlay(overlay: AladinOverlay): void;
  on(event: 'positionChanged' | 'zoomChanged' | 'objectClicked', callback: (value: unknown) => void): void;
}
export interface AladinSource { select(): void; deselect(): void }
export interface AladinCatalog { addSources(sources: AladinSource[]): void; show(): void; hide(): void }
export interface AladinOverlay { addFootprints(shapes: unknown[]): void; removeAll(): void; show(): void; hide(): void }
export interface AladinAPI {
  init: Promise<unknown>;
  aladin(container: string, options: Record<string, unknown>): AladinViewer;
  catalog(options: Record<string, unknown>): AladinCatalog;
  source(ra: number, dec: number, data: Record<string, string>): AladinSource;
  graphicOverlay(options: Record<string, unknown>): AladinOverlay;
  circle(ra: number, dec: number, radius: number, options?: Record<string, unknown>): unknown;
}

export function fixtureIdFromClick(value: unknown): string | null {
  if (!value || typeof value !== 'object' || !('data' in value)) return null;
  const data = value.data;
  if (!data || typeof data !== 'object' || !('fixtureId' in data)) return null;
  return typeof data.fixtureId === 'string' ? data.fixtureId : null;
}
