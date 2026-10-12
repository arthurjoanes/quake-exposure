import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { Asset } from "./api";
import { demoRegion } from "./demo-api";

const asset = (asset_id: string, lon: number, currency = "BRL", qualifying = true): Asset => ({
  asset_id, lon, lat: 0, currency, insured_value_minor: "9007199254740993",
  eligible: true, qualifying, mmi: qualifying ? 6.1 : 5.1,
});

describe("região do snapshot REPLAY", () => {
  it("preserva cada moeda e os inteiros acima do limite seguro de Number", () => {
    const region = demoRegion("pub", [asset("a", 0), asset("b", 0), asset("c", 0, "USD", false)], {
      west: "-1", east: "1", south: "-1", north: "1", zoom: "0", limit: "1",
    }) as { total_count: number; qualifying_count: number; insured_value_minor: Record<string, string>; qualifying_value_minor: Record<string, string>; clusters: { count: number }[] };
    expect(region.total_count).toBe(3);
    expect(region.qualifying_count).toBe(2);
    expect(region.insured_value_minor).toEqual({ BRL: "18014398509481986", USD: "9007199254740993" });
    expect(region.qualifying_value_minor).toEqual({ BRL: "18014398509481986", USD: "0" });
    expect(region.clusters).toEqual([expect.objectContaining({ count: 3 })]);
  });

  it("seleciona ambos os lados do antimeridiano sem incluir o meridiano zero", () => {
    const region = demoRegion("pub", [asset("east", 179), asset("west", -179), asset("outside", 0)], {
      west: "178", east: "-178", south: "-1", north: "1", zoom: "0", limit: "1",
    }) as { total_count: number; clusters: { lon: number; count: number }[] };
    expect(region.total_count).toBe(2);
    expect(region.clusters[0].count).toBe(2);
    expect(Math.abs(region.clusters[0].lon)).toBe(180);
  });

  it.each([[-180, -160, -180], [160, 180, -180], [-180, 180, -180]])("inclui ±180 como a mesma borda na caixa %s..%s", (west, east, center) => {
    const region = demoRegion("pub", [asset("negative", -180), asset("positive", 180)], {
      west: String(west), east: String(east), south: "-1", north: "1", zoom: "0", limit: "1",
    }) as { total_count: number; clusters: { count: number; lon: number }[] };
    expect(region.total_count).toBe(2);
    expect(region.clusters).toEqual([expect.objectContaining({ count: 2, lon: center })]);
  });

  it("devolve todos os pontos no detalhe e respeita o limite de grupos", () => {
    const assets = Array.from({ length: 200 }, (_, i) => asset(String(i), i / 200));
    const params = { west: "0", east: "1", south: "-1", north: "1", zoom: "10" };
    const groups = demoRegion("pub", assets, { ...params, limit: "3" }) as { representation: string; clusters: { count: number }[] };
    expect(groups.representation).toBe("clusters");
    expect(groups.clusters.length).toBeLessThanOrEqual(3);
    expect(groups.clusters.reduce((sum, group) => sum + group.count, 0)).toBe(200);
    const points = demoRegion("pub", assets, { ...params, limit: "200" }) as { representation: string; assets: Asset[] };
    expect(points.representation).toBe("points");
    expect(points.assets).toHaveLength(200);
  });

  it.each<Record<string, string>>([{ west: "Infinity" }, { north: "91" }, { zoom: "-1" }, { limit: "0" }, { south: "5", north: "-5" }])("recusa parâmetros inválidos %j", (params) => {
    expect(() => demoRegion("pub", [], params)).toThrow("Região inválida");
  });
});

describe("API estática", () => {
  beforeEach(() => vi.resetModules());
  afterEach(() => vi.unstubAllGlobals());

  const id = "a".repeat(64);
  const stored = {
    current: { publication_id: id, generation: 2, result: { input_vector: { tenant_id: "t", earthquake_uid: "quake", mode: "SHAKEMAP_MMI" } } },
    assets: [asset("a", 0)],
    diff: { publication_id: id, causes: ["epoch da carteira"] },
    comparison: { publication_id: id, total_changes: 3, changes: ["a", "b", "c"] },
  };
  const index = {
    metadata: { scope: "REPLAY" }, tenants: [{ tenant_id: "t", name: "Fictícia" }],
    events: [{ preferred_id: "us1" }], revisions: { quake: [] },
    current: [stored.current], publications: { [id]: { tenant: "t", file: `${id}.json` } },
    status: {}, operations: { t: {} },
  };

  function mockSnapshot(): ReturnType<typeof vi.fn> {
    const fetch = vi.fn(async (path: string) => new Response(JSON.stringify(path.endsWith("snapshot.json") ? index : stored)));
    vi.stubGlobal("fetch", fetch);
    return fetch;
  }

  it("não retorna LIVE nem ativos de outro tenant e só busca arquivos de snapshot", async () => {
    const fetch = mockSnapshot();
    const { demoGet } = await import("./demo-api");
    await expect(demoGet("/api/events", { scope: "LIVE" })).resolves.toBeNull();
    await expect(demoGet(`/api/publications/${id}/assets`, { tenant: "other" })).resolves.toBeNull();
    const current = await demoGet("/api/publications/current", { tenant: "t", earthquake_uid: "quake", mode: "SHAKEMAP_MMI", scope: "REPLAY" });
    expect(current).toEqual(stored.current);
    await expect(demoGet(`/api/publications/${id}/assets`, { tenant: "t" })).resolves.toEqual({ publication_id: id, assets: stored.assets });
    expect(fetch.mock.calls.every(([path]) => String(path).includes("demo/") && !String(path).includes("/api/"))).toBe(true);
  });

  it("pagina mudanças sem alterar total e devolve cópias isoladas do cache", async () => {
    const fetch = mockSnapshot();
    const { demoGet } = await import("./demo-api");
    const path = `/api/publications/${id}/comparison`;
    await expect(demoGet(path, { tenant: "t", limit: "1", offset: "1" })).resolves.toEqual({ ...stored.comparison, offset: 1, limit: 1, changes: ["b"] });
    const response = await demoGet<{ changes: string[] }>(path, { tenant: "t" });
    response!.changes.length = 0;
    const again = await demoGet<{ changes: string[] }>(path, { tenant: "t" });
    expect(again!.changes).toEqual(["a", "b", "c"]);
    expect(fetch).toHaveBeenCalledTimes(2);
  });

  it("uma falha no índice pode ser repetida sem congelar o erro no cache", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValueOnce(new Response(null, { status: 503 })).mockResolvedValueOnce(new Response(JSON.stringify(index))));
    const { demoGet } = await import("./demo-api");
    await expect(demoGet("/api/tenants")).rejects.toThrow("HTTP 503");
    await expect(demoGet("/api/tenants")).resolves.toEqual(index.tenants);
  });
});
