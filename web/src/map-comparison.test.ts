import { describe, expect, it } from "vitest";
import type { Asset } from "./api";
import { assetMoved, buildMapComparison, classificationChange, comparisonCoordinates } from "./map-comparison";

const asset = (asset_id: string, changes: Partial<Asset> = {}): Asset => ({
  asset_id, lon: -150, lat: 61, eligible: true, qualifying: false, currency: "BRL",
  insured_value_minor: "90071992547409931", mmi: 5.5, ...changes,
});

describe("comparação geográfica entre publicações", () => {
  it("identifica entradas, saídas e mudanças por ativo preservando valores exatos", () => {
    const before = [asset("fica"), asset("mudou"), asset("saiu", { qualifying: true }), asset("removido", { qualifying: true })];
    const after = [asset("fica"), asset("mudou", { lon: -149, qualifying: true, mmi: 6.1 }),
      asset("saiu", { qualifying: false }), asset("novo", { qualifying: true, currency: "USD" })];
    const snapshot = JSON.stringify([before, after]);
    const changes = buildMapComparison(before, after);
    expect(changes.map((entry) => [entry.asset_id, entry.change, classificationChange(entry), assetMoved(entry)])).toEqual([
      ["mudou", "changed", "entered", true], ["novo", "added", "entered", false],
      ["removido", "removed", "left", false], ["saiu", "changed", "left", false],
    ]);
    expect(changes[0].after?.insured_value_minor).toBe("90071992547409931");
    expect(JSON.stringify([before, after])).toBe(snapshot);
  });

  it("não indica movimento entre cópias da mesma longitude", () => {
    const [change] = buildMapComparison([asset("a", { lon: -179 })], [asset("a", { lon: 181 })]);
    expect(assetMoved(change)).toBe(false);
    expect(classificationChange(change)).toBeNull();
    expect(assetMoved({ ...change, after: asset("a", { lon: 179 }) })).toBe(true);
  });

  it("liga marcadores da mesma cópia do mundo ao atravessar o antimeridiano", () => {
    const change = { asset_id: "a", change: "changed" as const, before: asset("a", { lon: 179 }), after: asset("a", { lon: -179 }) };
    expect(comparisonCoordinates(change, 0)).toEqual({ before: [179, 61], after: [181, 61] });
    expect(comparisonCoordinates(change, -180)).toEqual({ before: [-181, 61], after: [-179, 61] });
    expect(comparisonCoordinates({ ...change, before: null }, 0)).toEqual({ before: null, after: [-179, 61] });
  });

  it("mostra alterações de valor, moeda, elegibilidade e MMI sem inventar classificação", () => {
    for (const changes of [{ insured_value_minor: "90071992547409932" }, { currency: "USD" }, { eligible: false }, { mmi: null }]) {
      const [change] = buildMapComparison([asset("a")], [asset("a", changes)]);
      expect(change.change).toBe("changed");
      expect(assetMoved(change)).toBe(false);
      expect(classificationChange(change)).toBeNull();
    }
    expect(buildMapComparison([asset("a")], [asset("a")])).toEqual([]);
  });
});
