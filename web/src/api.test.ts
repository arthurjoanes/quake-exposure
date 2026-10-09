import { afterEach, describe, expect, it, vi } from "vitest";
import { get, PrivacyRecalculation } from "./api";

afterEach(() => vi.unstubAllGlobals());

describe("GET da API", () => {
  it.each([
    "/api/publications/current",
    "/api/publications/old/assets",
    "/api/publications/old/diff",
  ])("409 em %s sinaliza recálculo por exclusão", async (path) => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(
      JSON.stringify({ detail: "recalculando após exclusão" }), { status: 409 },
    )));
    await expect(get(path)).rejects.toBeInstanceOf(PrivacyRecalculation);
    await expect(get(path)).rejects.toThrow("Recalculando após exclusão.");
  });

  it("404 significa publicação ausente, sem sinalizar exclusão", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(null, { status: 404 })));
    await expect(get("/api/publications/current")).resolves.toBeNull();
  });

  it("erro HTTP comum preserva o status e não vira exclusão", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(null, { status: 503 })));
    const error = await get("/api/status").catch((caught: unknown) => caught);
    expect(error).toBeInstanceOf(Error);
    expect(error).not.toBeInstanceOf(PrivacyRecalculation);
    expect((error as Error).message).toBe("/api/status: HTTP 503");
  });

  it("erro de rede propaga o mesmo erro, sem apagar por falsa exclusão", async () => {
    const error = new TypeError("Failed to fetch");
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(error));
    await expect(get("/api/publications/current")).rejects.toBe(error);
    expect(error).not.toBeInstanceOf(PrivacyRecalculation);
  });

  it("GET bem sucedido codifica filtros e conserva dinheiro como string", async () => {
    const result = { qualifying_value_minor: { BRL: "9007199254740993", USD: "0" } };
    const fetch = vi.fn().mockResolvedValue(new Response(JSON.stringify(result), { status: 200 }));
    vi.stubGlobal("fetch", fetch);
    await expect(get("/api/publications/current", { tenant: "a & b", scope: "REPLAY" }))
      .resolves.toEqual(result);
    expect(fetch).toHaveBeenCalledWith(
      "/api/publications/current?tenant=a+%26+b&scope=REPLAY", { headers: { Accept: "application/json" } },
    );
  });
});
