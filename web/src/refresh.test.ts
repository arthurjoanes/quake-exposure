import { describe, expect, it, vi } from "vitest";
import { PrivacyRecalculation } from "./api";
import { LatestRefresh } from "./refresh";

function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (reason: unknown) => void;
  const promise = new Promise<T>((yes, no) => { resolve = yes; reject = no; });
  return { promise, resolve, reject };
}

describe("LatestRefresh", () => {
  it.each([new PrivacyRecalculation(), new TypeError("Failed to fetch")])(
    "uma falha antiga não apaga a seleção nova: %s", async (error) => {
      const refresh = new LatestRefresh();
      const old = deferred<void>();
      const failed = vi.fn();
      let shown = "a";
      const stale = refresh.run(() => old.promise, () => { shown = ""; failed(); });
      await refresh.run(async () => { shown = "b"; }, failed);
      old.reject(error);
      await stale;
      expect(shown).toBe("b");
      expect(failed).not.toHaveBeenCalled();
    },
  );

  it("voltar à mesma seleção não reativa a requisição anterior", async () => {
    const refresh = new LatestRefresh();
    const old = deferred<void>();
    const failed = vi.fn();
    let shown = "a";
    const stale = refresh.run(() => old.promise, () => { shown = ""; failed(); });
    await refresh.run(async () => { shown = "b"; }, failed);
    await refresh.run(async () => { shown = "a"; }, failed);
    old.reject(new PrivacyRecalculation());
    await stale;
    expect(shown).toBe("a");
    expect(failed).not.toHaveBeenCalled();
  });

  it("resposta antiga que chega depois também fica inválida", async () => {
    const refresh = new LatestRefresh();
    const old = deferred<string>();
    const failed = vi.fn();
    let revisions = "";
    const stale = refresh.run(async (isCurrent) => {
      const loaded = await old.promise;
      if (isCurrent()) revisions = loaded;
    }, failed);
    await refresh.run(async () => { revisions = "novo"; }, failed);
    old.resolve("antigo");
    await stale;
    expect(revisions).toBe("novo");
    expect(failed).not.toHaveBeenCalled();
  });

  it("409 do ciclo atual chega ao tratamento que limpa dados", async () => {
    const refresh = new LatestRefresh();
    const error = new PrivacyRecalculation();
    const failed = vi.fn();
    await refresh.run(async () => { throw error; }, failed);
    expect(failed).toHaveBeenCalledExactlyOnceWith(error);
  });
});
