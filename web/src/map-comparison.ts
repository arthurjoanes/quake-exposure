import type { Asset } from "./api";
import { near } from "./basemap";
import type { LonLat } from "./spatial";

export interface MapComparisonChange {
  asset_id: string;
  change: "added" | "removed" | "changed";
  before: Asset | null;
  after: Asset | null;
}

export function assetMoved(change: MapComparisonChange): boolean {
  return change.before !== null && change.after !== null
    && (near(change.before.lon, change.after.lon) !== change.after.lon || change.before.lat !== change.after.lat);
}

export function classificationChange(change: MapComparisonChange): "entered" | "left" | null {
  const before = change.before?.qualifying ?? false, after = change.after?.qualifying ?? false;
  return before === after ? null : after ? "entered" : "left";
}

/** Os dois marcadores usam a mesma cópia do mundo que a linha de deslocamento. */
export function comparisonCoordinates(change: MapComparisonChange, center: number): { before: LonLat | null; after: LonLat | null } {
  const anchor = near(change.before?.lon ?? change.after?.lon ?? center, center);
  return {
    before: change.before ? [near(change.before.lon, anchor), change.before.lat] : null,
    after: change.after ? [near(change.after.lon, anchor), change.after.lat] : null,
  };
}

/** Comparação por identidade; valores monetários continuam em strings inteiras. */
export function buildMapComparison(before: readonly Asset[], after: readonly Asset[]): MapComparisonChange[] {
  const previous = new Map(before.map((asset) => [asset.asset_id, asset]));
  const changes: MapComparisonChange[] = [];
  for (const asset of after) {
    const older = previous.get(asset.asset_id);
    previous.delete(asset.asset_id);
    if (!older) changes.push({ asset_id: asset.asset_id, change: "added", before: null, after: asset });
    else if (older.lon !== asset.lon || older.lat !== asset.lat || older.eligible !== asset.eligible
      || older.qualifying !== asset.qualifying || older.mmi !== asset.mmi || older.currency !== asset.currency
      || older.insured_value_minor !== asset.insured_value_minor) {
      changes.push({ asset_id: asset.asset_id, change: "changed", before: older, after: asset });
    }
  }
  for (const asset of previous.values()) changes.push({ asset_id: asset.asset_id, change: "removed", before: asset, after: null });
  return changes.sort((a, b) => a.asset_id.localeCompare(b.asset_id));
}
