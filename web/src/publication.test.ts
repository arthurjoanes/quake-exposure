import { describe, expect, it } from "vitest";
import { type Bundle, PublicationSync } from "./publication";

const bundle = (id: string, assets = id, diff = id): Bundle => ({
  pub: { publication_id: id },
  assets: { publication_id: assets },
  diff: { publication_id: diff },
});

describe("PublicationSync", () => {
  it("busca ativos só quando o publication_id muda", () => {
    const s = new PublicationSync();
    expect(s.seen("a")).toBe(true);
    expect(s.accept(bundle("a"))).toBe(true);
    expect(s.seen("a")).toBe(false);
    expect(s.seen("b")).toBe(true);
  });

  it("descarta ativos de uma publicação que deixou de ser a atual", () => {
    const s = new PublicationSync();
    s.seen("a");
    s.seen("b");
    expect(s.accept(bundle("a"))).toBe(false);
    expect(s.accept(bundle("b"))).toBe(true);
  });

  it("descarta resposta atrasada da anterior que chega depois da atual", () => {
    const s = new PublicationSync();
    s.seen("a");
    s.seen("b");
    expect(s.accept(bundle("b"))).toBe(true);
    expect(s.accept(bundle("a"))).toBe(false);
  });

  it("recusa pacote com ativos ou diff de outra publicação", () => {
    const s = new PublicationSync();
    s.seen("a");
    expect(s.accept(bundle("a", "b"))).toBe(false);
    expect(s.accept(bundle("a", "a", "b"))).toBe(false);
    expect(s.accept(bundle("a"))).toBe(true);
  });

  it("não reaplica a publicação que já está na tela", () => {
    const s = new PublicationSync();
    s.seen("a");
    expect(s.accept(bundle("a"))).toBe(true);
    expect(s.accept(bundle("a"))).toBe(false);
  });

  it("busca de novo se a carga anterior falhou", () => {
    const s = new PublicationSync();
    expect(s.seen("a")).toBe(true);
    expect(s.seen("a")).toBe(true);
  });

  it("sem publicação limpa; se ela voltar, é carregada de novo", () => {
    const s = new PublicationSync();
    s.seen("a");
    s.accept(bundle("a"));
    expect(s.seen(null)).toBe(false);
    expect(s.accept(bundle("a"))).toBe(false);
    expect(s.seen("a")).toBe(true);
  });

  it("reset descarta o que estava em voo", () => {
    const s = new PublicationSync();
    s.seen("a");
    s.reset();
    expect(s.accept(bundle("a"))).toBe(false);
  });
});
