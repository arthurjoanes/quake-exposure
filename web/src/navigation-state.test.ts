import { describe, expect, it } from "vitest";
import type { AssetListState } from "./asset-list";
import { readComparison, readListState, writeComparison, writeListState } from "./navigation-state";

const defaultState: AssetListState = {
  filters: { search: "", status: "all", currency: "all" }, page: 0, pageSize: 50,
};

describe("estado de navegação", () => {
  it("recupera busca, situação, moeda, página e comparação de um link compartilhado", () => {
    const params = new URLSearchParams("q=+OPE-%5B005%5D+&situation=eligible&currency=USD&page=3&pageSize=100&comparison=1");
    expect(readListState(params)).toEqual({
      filters: { search: " OPE-[005] ", status: "eligible", currency: "USD" }, page: 2, pageSize: 100,
    });
    expect(readComparison(params)).toBe(true);
  });

  it("grava e recupera a seleção mantendo o texto literal da busca", () => {
    const state: AssetListState = {
      filters: { search: " ope-005 & + / ç ", status: "qualifying", currency: "BRL" }, page: 7, pageSize: 100,
    };
    const params = new URLSearchParams();
    writeListState(params, state);
    writeComparison(params, true);
    const reopened = new URLSearchParams(params.toString());
    expect(readListState(reopened)).toEqual(state);
    expect(reopened.get("page")).toBe("8");
    expect(readComparison(reopened)).toBe(true);
    expect(state.page).toBe(7);
  });

  it("usa os padrões quando as opções e a página são inválidas", () => {
    const params = new URLSearchParams("situation=unknown&currency=EUR&page=2.5&pageSize=200&comparison=true");
    expect(readListState(params)).toEqual(defaultState);
    expect(readComparison(params)).toBe(false);
    for (const page of ["0", "-1", "NaN", "Infinity", "1e2", " 2 ", "02", "9007199254740992"]) {
      expect(readListState(new URLSearchParams({ page })).page).toBe(0);
    }
    for (const comparison of ["", "0", "false", "01", "2"]) {
      expect(readComparison(new URLSearchParams({ comparison }))).toBe(false);
    }
    expect(readListState(new URLSearchParams())).toEqual(defaultState);
  });

  it("conserva a seleção principal e os parâmetros desconhecidos ao editar a lista", () => {
    const params = new URLSearchParams("scope=REPLAY&tenant=operadora-b&event=uid&mode=SHAKEMAP_MMI&source=a&source=b&comparison=1&page=4");
    writeListState(params, {
      filters: { search: "ope-", status: "ineligible", currency: "USD" }, page: 1, pageSize: 50,
    });
    expect(params.get("scope")).toBe("REPLAY");
    expect(params.get("tenant")).toBe("operadora-b");
    expect(params.get("event")).toBe("uid");
    expect(params.get("mode")).toBe("SHAKEMAP_MMI");
    expect(params.getAll("source")).toEqual(["a", "b"]);
    expect(params.get("comparison")).toBe("1");
    expect(params.get("page")).toBe("2");
    expect(params.has("pageSize")).toBe(false);
    writeComparison(params, false);
    expect(params.has("comparison")).toBe(false);
    expect(params.get("q")).toBe("ope-");
    expect(params.get("situation")).toBe("ineligible");
    expect(params.get("currency")).toBe("USD");
  });

  it("remove opções padrão de um link existente sem apagar outros parâmetros", () => {
    const params = new URLSearchParams("q=old&situation=eligible&currency=BRL&page=8&pageSize=100&comparison=1&tenant=seguradora-a");
    writeListState(params, { ...defaultState, filters: { ...defaultState.filters, search: "   " } });
    expect(params.toString()).toBe("comparison=1&tenant=seguradora-a");
    expect(readListState(params)).toEqual(defaultState);
    writeComparison(params, false);
    expect(params.toString()).toBe("tenant=seguradora-a");
  });
});
