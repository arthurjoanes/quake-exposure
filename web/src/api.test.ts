import { afterEach, describe, expect, it, vi } from "vitest";
import { get, PrivacyRecalculation, REQUEST_TIMEOUT_MS } from "./api";

afterEach(() => {
  vi.unstubAllGlobals();
  vi.useRealTimers();
});

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
      "/api/publications/current?tenant=a+%26+b&scope=REPLAY",
      { headers: { Accept: "application/json" }, signal: expect.any(AbortSignal) },
    );
  });

  it.each(["headers", "body"])("prazo cobre %s pendentes e permite a consulta seguinte", async (stage) => {
    vi.useFakeTimers();
    let requestSignal!: AbortSignal;
    const fetch = vi.fn(async (_url: string, options: RequestInit) => {
      requestSignal = options.signal as AbortSignal;
      if (stage === "headers") {
        return new Promise<Response>((_resolve, reject) => {
          requestSignal.addEventListener("abort", () => reject(requestSignal.reason), { once: true });
        });
      }
      return new Response(new ReadableStream({
        start(controller) {
          requestSignal.addEventListener("abort", () => controller.error(requestSignal.reason), { once: true });
        },
      }));
    });
    vi.stubGlobal("fetch", fetch);
    const result = get("/api/events").catch((error: unknown) => error);
    await vi.advanceTimersByTimeAsync(REQUEST_TIMEOUT_MS - 1);
    expect(requestSignal.aborted).toBe(false);
    await vi.advanceTimersByTimeAsync(1);
    expect(await result).toMatchObject({ name: "TimeoutError" });
    expect(requestSignal.aborted).toBe(true);
    expect(vi.getTimerCount()).toBe(0);
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(Response.json([])));
    await expect(get("/api/events")).resolves.toEqual([]);
    expect(vi.getTimerCount()).toBe(0);
  });

  it("mudança da seleção aborta a consulta antes do prazo", async () => {
    vi.useFakeTimers();
    const selection = new AbortController();
    let requestSignal!: AbortSignal;
    vi.stubGlobal("fetch", vi.fn((_url: string, options: RequestInit) => {
      requestSignal = options.signal as AbortSignal;
      return new Promise<Response>((_resolve, reject) => {
        requestSignal.addEventListener("abort", () => reject(requestSignal.reason), { once: true });
      });
    }));
    const result = get("/api/events", {}, selection.signal).catch((error: unknown) => error);
    selection.abort();
    expect(await result).toMatchObject({ name: "AbortError" });
    expect(requestSignal.aborted).toBe(true);
    expect(vi.getTimerCount()).toBe(0);
    await expect(get("/api/events", {}, selection.signal)).rejects.toMatchObject({ name: "AbortError" });
  });
});
