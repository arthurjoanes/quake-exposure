import { describe, expect, it } from "vitest";
import type { Asset } from "./api";
import { filterAssets, orderAssets, paginateAssets, type AssetFilters } from "./asset-list";

const asset = (id: string, changes: Partial<Asset> = {}): Asset => ({
  asset_id: id,
  lon: 0,
  lat: 0,
  currency: "BRL",
  insured_value_minor: "123456789012345678901",
  eligible: true,
  qualifying: false,
  mmi: null,
  ...changes,
});
const filters = (changes: Partial<AssetFilters> = {}): AssetFilters => ({
  search: "", status: "all", currency: "all", ...changes,
});

describe("lista de ativos", () => {
  it("ordena por situação e ID, sem alterar os ativos recebidos", () => {
    const original = [asset("z"), asset("b", { qualifying: true }), asset("a", { eligible: false }),
      asset("a", { qualifying: true }), asset("c")];
    expect(orderAssets(original).map((a) => [a.asset_id, a.qualifying, a.eligible])).toEqual([
      ["a", true, true], ["b", true, true], ["c", false, true], ["z", false, true], ["a", false, false],
    ]);
    expect(original.map((a) => a.asset_id)).toEqual(["z", "b", "a", "a", "c"]);
  });

  it("busca ID literal ignorando caixa e espaços nas bordas", () => {
    const original = [asset("ALFA.[1]"), asset("alfaX1"), asset("beta")];
    expect(filterAssets(original, filters({ search: " alfa.[1] " })).map((a) => a.asset_id)).toEqual(["ALFA.[1]"]);
    expect(filterAssets(original, filters({ search: "   " }))).toHaveLength(3);
  });

  it("combina moeda, situação e busca; elegíveis inclui classificados", () => {
    const original = [asset("a1", { qualifying: true }), asset("a2"), asset("a3", { eligible: false }),
      asset("a4", { qualifying: true, currency: "USD" }), asset("b1", { qualifying: true })];
    expect(filterAssets(original, filters({ status: "eligible" }))).toHaveLength(4);
    expect(filterAssets(original, filters({ status: "ineligible" })).map((a) => a.asset_id)).toEqual(["a3"]);
    expect(filterAssets(original, filters({ search: "a", currency: "BRL", status: "qualifying" }))
      .map((a) => a.asset_id)).toEqual(["a1"]);
  });

  it("mantém todos os 20 mil ativos acessíveis em páginas de até 50 linhas", () => {
    const original = Array.from({ length: 20_003 }, (_, index) => asset(`ativo-${index}`));
    const visited: Asset[] = [];
    for (let page = 0; page < 401; page++) {
      const result = paginateAssets(original, page, 50);
      expect(result.assets.length).toBeLessThanOrEqual(50);
      visited.push(...result.assets);
    }
    expect(visited.map((a) => a.asset_id)).toEqual(original.map((a) => a.asset_id));
    expect(visited[20_002].insured_value_minor).toBe("123456789012345678901");
    expect(paginateAssets(original, 400, 50)).toMatchObject({ first: 20_001, last: 20_003, pages: 401 });
  });

  it("limita a página às bordas e oferece 100 linhas por página", () => {
    const original = Array.from({ length: 101 }, (_, index) => asset(String(index)));
    expect(paginateAssets(original, -1, 100)).toMatchObject({ page: 0, first: 1, last: 100, pages: 2 });
    expect(paginateAssets(original, 200, 100)).toMatchObject({ page: 1, first: 101, last: 101, pages: 2 });
    expect(paginateAssets(original, Number.NaN, 100).page).toBe(0);
    expect(paginateAssets(original, 0.5, 100).page).toBe(0);
  });

  it("retorna um estado vazio consistente após filtro sem correspondência", () => {
    const filtered = filterAssets([asset("a")], filters({ search: "inexistente" }));
    expect(paginateAssets(filtered, 99, 50)).toEqual({ assets: [], page: 0, pages: 1, total: 0, first: 0, last: 0 });
  });
});
