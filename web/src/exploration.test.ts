import { describe, expect, it } from "vitest";
import type { Asset } from "./api";
import { areaTotals, renderArea } from "./exploration";

const asset = (currency: string, value: string, qualifying: boolean): Asset => ({
  asset_id: "a", lon: 0, lat: 0, currency, insured_value_minor: value,
  eligible: qualifying, qualifying, mmi: qualifying ? 6 : null,
});

describe("selected area amounts", () => {
  it("keeps each currency exact beyond the Number limit", () => {
    const totals = areaTotals([asset("BRL", "9007199254740993", true), asset("BRL", "9", false), asset("USD", "17", true)]);
    expect(totals.insured).toEqual({ BRL: "9007199254741002", USD: "17" });
    expect(totals.classified).toEqual({ BRL: "9007199254740993", USD: "17" });
    expect(totals).toMatchObject({ count: 3, eligible: 2, qualifying: 2 });
  });
  it("renders a genuine empty selection and explains the global chart scope", () => {
    expect(areaTotals([]).count).toBe(0);
    expect(renderArea([])).toContain("publicação inteira");
    expect(renderArea([])).toContain("R$ 0,00");
  });
  it("rejects unsupported currency and malformed money", () => {
    expect(() => areaTotals([asset("EUR", "1", true)])).toThrow();
    expect(() => areaTotals([asset("BRL", "1.5", true)])).toThrow();
  });
});
