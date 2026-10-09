import type { Asset, QuakeEvent } from "./api";
import { near } from "./basemap";

export interface AssetGroup {
  key: string;
  lon: number;
  lat: number;
  assets: Asset[];
  qualifying: number;
  eligible: number;
}

/** Agrupa em células fixas no mundo; arrastar o mapa não muda os grupos. */
export function groupAssets(assets: readonly Asset[], zoom: number, anchor: number): AssetGroup[] {
  const groups = new Map<string, AssetGroup>();
  const scale = 256 * 2 ** Math.floor(zoom);
  for (const asset of assets) {
    const lon = near(asset.lon, anchor);
    const lat = Math.max(-85.051129, Math.min(85.051129, asset.lat));
    const sine = Math.sin(lat * Math.PI / 180);
    const x = ((lon + 180) / 360) * scale;
    const y = (0.5 - Math.log((1 + sine) / (1 - sine)) / (4 * Math.PI)) * scale;
    const key = zoom >= 9 ? `asset:${asset.asset_id}` : `cell:${Math.floor(x / 40)}:${Math.floor(y / 40)}`;
    let group = groups.get(key);
    if (!group) {
      group = { key, lon: 0, lat: 0, assets: [], qualifying: 0, eligible: 0 };
      groups.set(key, group);
    }
    group.assets.push(asset);
    group.lon += lon;
    group.lat += asset.lat;
    group.qualifying += Number(asset.qualifying);
    group.eligible += Number(asset.eligible);
  }
  for (const group of groups.values()) {
    group.lon /= group.assets.length;
    group.lat /= group.assets.length;
  }
  // Pontos classificados por cima dos demais, sem depender da ordem da API.
  return [...groups.values()].sort((a, b) => Number(a.qualifying > 0) - Number(b.qualifying > 0)
    || Number(a.eligible > 0) - Number(b.eligible > 0) || a.key.localeCompare(b.key));
}

/** Campos que mudam a geometria, o estilo ou o texto de um marcador. */
export function eventSignature(event: QuakeEvent): string {
  return JSON.stringify([event.lon, event.lat, event.mag, event.lifecycle,
    event.event_time, event.preferred_id]);
}

/** Cache por publicação imutável e nível de zoom; pan não reagrupa milhares de ativos. */
export class AssetGroupCache {
  private publicationId: string | null = null;
  private anchor = 0;
  private assets: readonly Asset[] = [];
  private levels = new Map<number, AssetGroup[]>();

  set(assets: readonly Asset[], publicationId: string, anchor: number): boolean {
    if (this.publicationId === publicationId && this.anchor === anchor) return false;
    this.publicationId = publicationId;
    this.anchor = anchor;
    this.assets = assets;
    this.levels.clear();
    return true;
  }

  get(zoom: number): AssetGroup[] {
    const level = Math.min(9, Math.floor(zoom));
    let groups = this.levels.get(level);
    if (!groups) {
      groups = groupAssets(this.assets, level, this.anchor);
      this.levels.set(level, groups);
    }
    return groups;
  }

  clear(): void {
    this.publicationId = null;
    this.assets = [];
    this.levels.clear();
  }
}
