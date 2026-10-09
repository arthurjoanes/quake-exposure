import { describe, expect, it } from "vitest";
import type { Asset, QuakeEvent } from "./api";
import { AssetGroupCache, eventSignature, groupAssets } from "./map-data";

const asset = (asset_id: string, lon: number, lat: number, qualifying = false): Asset => ({
  asset_id, lon, lat, qualifying, eligible: true, mmi: qualifying ? 6.5 : 4,
  currency: "BRL", insured_value_minor: "120000",
});

describe("agrupamento do mapa", () => {
  it("conserva os ativos, elegíveis e classificados de uma publicação", () => {
    const assets = Array.from({ length: 5000 }, (_, index) => asset(String(index), -150 + index / 10000, 61, index % 3 === 0));
    const groups = groupAssets(assets, 5, -150);
    expect(groups.length).toBeLessThan(10);
    expect(groups.reduce((sum, group) => sum + group.assets.length, 0)).toBe(5000);
    expect(groups.reduce((sum, group) => sum + group.qualifying, 0)).toBe(1667);
    expect(groups.reduce((sum, group) => sum + group.eligible, 0)).toBe(5000);
    expect(new Set(groups.flatMap((group) => group.assets.map((entry) => entry.asset_id))).size).toBe(5000);
  });

  it("mantém vizinhos do antimeridiano perto de 180, sem média no outro lado do mundo", () => {
    const groups = groupAssets([asset("a", 179.95, -17), asset("b", -179.95, -17)], 3, 180);
    expect(groups.flatMap((group) => group.assets)).toHaveLength(2);
    for (const group of groups) expect(Math.abs(group.lon - 180)).toBeLessThan(0.1);
  });

  it("abre todos os pontos em zoom de detalhe, mesmo se ocupam a mesma coordenada", () => {
    const groups = groupAssets([asset("a", 10, 20), asset("b", 10, 20, true)], 9, 10);
    expect(groups.map((group) => group.assets.length)).toEqual([1, 1]);
    expect(groups.at(-1)?.qualifying).toBe(1);
  });

  it("projeta os polos sem infinito e não altera as coordenadas fornecidas", () => {
    const assets = [asset("n", 0, 90), asset("s", 0, -90)];
    const groups = groupAssets(assets, 2, 0);
    expect(groups).toHaveLength(2);
    expect(groups.every((group) => !group.key.includes("Infinity") && !group.key.includes("NaN"))).toBe(true);
    expect(assets.map((entry) => entry.lat)).toEqual([90, -90]);
  });

  it("reutiliza agrupamento no pan, polling repetido e zoom acima de 9; invalida com nova publicação", () => {
    const cache = new AssetGroupCache();
    const assets = [asset("a", -150, 61)];
    expect(cache.set(assets, "publication-a", -150)).toBe(true);
    const level5 = cache.get(5);
    expect(cache.set([...assets], "publication-a", -150)).toBe(false);
    expect(cache.get(5)).toBe(level5);
    expect(cache.get(9)).toBe(cache.get(18));
    cache.set([asset("b", -150, 61)], "publication-b", -150);
    expect(cache.get(5)).not.toBe(level5);
    expect(cache.get(5)[0].assets[0].asset_id).toBe("b");
    cache.clear();
    expect(cache.get(5)).toEqual([]);
  });
});

describe("atualização de eventos", () => {
  const event: QuakeEvent = {
    earthquake_uid: "quake", preferred_id: "us123", stream_scope: "REPLAY", lifecycle: "ACTIVE",
    event_time: "2026-10-01T00:00:00Z", lon: -151, lat: 61, depth_km: 10, mag: 6.3,
    event_type: "earthquake", source_updated_at: "2026-10-02T00:00:00Z", product_revision_id: null, revisions: 1,
  };

  it("mantém geometria no polling igual e atualiza posição, magnitude, retirada e rótulo", () => {
    const signature = eventSignature(event);
    expect(eventSignature({ ...event, revisions: 2, source_updated_at: "2026-10-03T00:00:00Z" })).toBe(signature);
    for (const change of [{ lon: -150 }, { mag: 6.8 }, { lifecycle: "RETRACTED" as const }, { preferred_id: "ak123" }]) {
      expect(eventSignature({ ...event, ...change })).not.toBe(signature);
    }
  });
});
