import { describe, expect, it } from "vitest";
import { formatMinor } from "./money";

describe("formatMinor", () => {
  it.each([
    ["0", "BRL", "R$ 0,00"],
    ["1", "BRL", "R$ 0,01"],
    ["10", "USD", "US$ 0,10"],
    ["100", "BRL", "R$ 1,00"],
    ["99999", "BRL", "R$ 999,99"],
    ["100000", "BRL", "R$ 1.000,00"],
    ["123450", "USD", "US$ 1.234,50"],
    ["123456789012345678", "BRL", "R$ 1.234.567.890.123.456,78"],
    // 2^53 + 1: como Number viraria ...992
    ["9007199254740993", "USD", "US$ 90.071.992.547.409,93"],
    // 2^64 - 1
    ["18446744073709551615", "BRL", "R$ 184.467.440.737.095.516,15"],
  ])("%s %s", (minor, currency, expected) => {
    expect(formatMinor(minor, currency)).toBe(expected);
  });

  it.each(["", "007", "00", "-1", "1.5", "1e3", " 1", "1 ", "+1", "１"])("recusa %j", (minor) => {
    expect(() => formatMinor(minor, "BRL")).toThrow(RangeError);
  });

  it("recusa moeda fora de BRL e USD", () => {
    expect(() => formatMinor("100", "EUR")).toThrow(RangeError);
    expect(() => formatMinor("100", "toString")).toThrow(RangeError);
  });
});
