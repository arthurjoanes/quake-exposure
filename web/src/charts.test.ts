import { describe, expect, it } from "vitest";
import type { Asset, AssetsResponse, CurrentPublication, DiffResponse, ResultStatus } from "./api";
import { renderCharts, summarizeAssets } from "./charts";

const asset = (patch: Partial<Asset> = {}): Asset => ({
  asset_id: "ativo-a",
  lon: -152,
  lat: 60,
  currency: "BRL",
  insured_value_minor: "10000",
  eligible: true,
  qualifying: false,
  mmi: 5,
  ...patch,
});

const fixture = (): { pub: CurrentPublication; assets: AssetsResponse; diff: DiffResponse } => ({
  pub: {
    publication_id: "publication-a",
    generation: 1,
    result: {
      calculation_id: "publication-a",
      input_vector: {
        stream_scope: "REPLAY",
        earthquake_uid: "evento-a",
        product_revision_id: "mapa-a",
        tenant_id: "tenant-a",
        mode: "SHAKEMAP_MMI",
        portfolio_epoch: 3,
      },
      status: "COMPLETE",
      eligible_count: 2,
      qualifying_count: 1,
      qualifying_value_minor: { BRL: "9007199254740993", USD: "0" },
      calculated_at: "2026-10-09T12:00:00.000Z",
      published_at: "2026-10-09T12:00:00.000Z",
      limitations: [],
    },
  },
  assets: {
    publication_id: "publication-a",
    assets: [
      asset({ qualifying: true, mmi: 6, insured_value_minor: "9007199254740993" }),
      asset({ asset_id: "ativo-b", mmi: 5.99, insured_value_minor: "7" }),
      asset({ asset_id: "ativo-c", currency: "USD", eligible: false, mmi: null }),
    ],
  },
  diff: { publication_id: "publication-a", previous_publication_id: "anterior", causes: [], entered: [], left: [] },
});

describe("summarizeAssets", () => {
  it("soma valores além de 2^53 sem arredondar e mantém moedas separadas", () => {
    const data = summarizeAssets([
      asset({ insured_value_minor: "9007199254740993", qualifying: true }),
      asset({ insured_value_minor: "7" }),
      asset({ currency: "USD", insured_value_minor: "31", qualifying: true }),
      asset({ currency: "USD", insured_value_minor: "999", eligible: false }),
    ]);
    expect(data.money).toEqual({
      BRL: { eligible: 9007199254741000n, qualifying: 9007199254740993n },
      USD: { eligible: 31n, qualifying: 31n },
    });
    expect([data.qualifying, data.eligibleOnly, data.other]).toEqual([2, 1, 1]);
  });

  it("usa intervalos MMI sem sobreposição e inclui o limiar 6 na faixa classificada", () => {
    const values = [0, 3.99, 4, 4.99, 5, 5.99, 6, 6.99, 7, 7.99, 8, 10];
    const data = summarizeAssets(values.map((mmi) => asset({ mmi, qualifying: mmi >= 6 })));
    expect(data.mmi.map((entry) => entry.count)).toEqual([2, 2, 2, 2, 2, 2]);
    expect(data.mmi.map((entry) => entry.qualifying)).toEqual([false, false, false, true, true, true]);
    expect(data.withoutMmi).toBe(0);
  });

  it("distingue MMI zero de ausente e exclui ativos não elegíveis da distribuição", () => {
    const data = summarizeAssets([
      asset({ mmi: 0 }), asset({ mmi: null }), asset({ mmi: 9, eligible: false }),
    ]);
    expect(data.mmi[0].count).toBe(1);
    expect(data.mmi[5].count).toBe(0);
    expect(data.withoutMmi).toBe(2);
  });

  it("não modifica os fatos nem sua ordem", () => {
    const assets = fixture().assets.assets;
    const before = JSON.stringify(assets);
    assets.forEach(Object.freeze);
    Object.freeze(assets);
    summarizeAssets(assets);
    expect(JSON.stringify(assets)).toBe(before);
  });

  it.each([
    { insured_value_minor: "1.5" },
    { insured_value_minor: "-1" },
    { insured_value_minor: "01" },
    { currency: "<svg onload=alert(1)>" },
    { eligible: false, qualifying: true },
  ])("recusa valores fora do contrato: %j", (patch) => {
    expect(() => summarizeAssets([asset(patch)])).toThrow(RangeError);
  });
});

describe("renderCharts", () => {
  it("apresenta soma monetária exata e uma escala independente por moeda", () => {
    const { pub, assets, diff } = fixture();
    const html = renderCharts(pub, assets, diff);
    expect(html).toContain("R$ 90.071.992.547.409,93");
    expect(html).toContain("R$ 90.071.992.547.410,00");
    expect(html).toContain("US$ 0,00");
    expect(html).toContain("sem valor elegível");
    expect(html).toContain("width:99.99%");
    expect(html).not.toContain("NaN");
    expect(html).not.toContain("Infinity");
    expect(html.match(/class="chart-money-row"/g)).toHaveLength(2);
    expect(html).toContain('role="img"');
    expect(html).toContain("Classificados fazem parte dos elegíveis");
  });

  it("marca participações positivas pequenas sem chamá-las de zero", () => {
    const { pub, assets, diff } = fixture();
    assets.assets[0].insured_value_minor = "1";
    assets.assets[1].insured_value_minor = "9007199254740993";
    pub.result.qualifying_value_minor!.BRL = "1";
    const html = renderCharts(pub, assets, diff);
    expect(html).toContain("menos de 0,01%");
    expect(html).toContain("R$ 0,01");
  });

  it("preserva um resultado completo vazio como zero válido", () => {
    const { pub, assets, diff } = fixture();
    assets.assets = [];
    pub.result.eligible_count = 0;
    pub.result.qualifying_count = 0;
    pub.result.qualifying_value_minor = { BRL: "0", USD: "0" };
    const html = renderCharts(pub, assets, diff);
    expect(html).toContain("de 0 ativos classificados");
    expect(html).toContain("R$ 0,00");
    expect(html).not.toContain("indisponíveis");
    expect(html).not.toContain("NaN");
  });

  it.each<ResultStatus>(["NO_MAP", "UNSUPPORTED_GRID", "RETRACTED", "REDACTED_BY_ERASURE"])("não transforma %s em exposição zero", (status) => {
    const { pub, assets, diff } = fixture();
    pub.result.status = status;
    pub.result.eligible_count = null;
    pub.result.qualifying_count = null;
    pub.result.qualifying_value_minor = null;
    const html = renderCharts(pub, assets, diff);
    expect(html).toContain("indisponíveis");
    expect(html).not.toContain("<svg");
    expect(html).not.toContain("R$");
    expect(html).not.toContain("0 ativos");
  });

  it("espera contagens nulas mesmo se o status informado for COMPLETE", () => {
    const { pub, assets, diff } = fixture();
    pub.result.eligible_count = null;
    expect(renderCharts(pub, assets, diff)).not.toContain("<svg");
  });

  it("não apresenta intensidade no modo de proximidade", () => {
    const { pub, assets, diff } = fixture();
    pub.result.input_vector.mode = "RADIUS_TRIAGE";
    const html = renderCharts(pub, assets, diff);
    expect(html).not.toContain("Intensidade nos ativos");
    expect(html).not.toContain("MMI ≥");
    expect(html.match(/class="chart-panel"/g)).toHaveLength(3);
  });

  it("compara entradas e saídas apenas quando existe publicação anterior", () => {
    const { pub, assets, diff } = fixture();
    diff.entered = [{ asset_id: "entrada", currency: "BRL", insured_value_minor: "1", mmi_before: null, mmi_after: 7 }];
    expect(renderCharts(pub, assets, diff)).toContain("1 ativos entraram; 0 ativos saíram");
    diff.previous_publication_id = null;
    const html = renderCharts(pub, assets, diff);
    expect(html).toContain("Sem publicação anterior para comparar");
    expect(html).not.toContain("1 ativos entraram");
  });

  it("escapa atributos e operadores MMI no SVG", () => {
    const { pub, assets, diff } = fixture();
    pub.publication_id = assets.publication_id = diff.publication_id = 'x" onmouseover="alert(1) <&\'';
    const html = renderCharts(pub, assets, diff);
    expect(html).toContain('data-publication="x&#34; onmouseover=&#34;alert(1) &#60;&#38;&#39;"');
    expect(html).toContain("4 a &#60; 5");
    expect(html).not.toContain('onmouseover="alert');
    expect(html).not.toContain(">4 a < 5<");
  });

  it("recusa ativos ou diff de outra publicação", () => {
    const { pub, assets, diff } = fixture();
    expect(() => renderCharts(pub, { ...assets, publication_id: "outra" }, diff)).toThrow("publicações diferentes");
    expect(() => renderCharts(pub, assets, { ...diff, publication_id: "outra" })).toThrow("publicações diferentes");
  });

  it("recusa séries que divergem das contagens e valores publicados", () => {
    const { pub, assets, diff } = fixture();
    pub.result.eligible_count = 3;
    expect(() => renderCharts(pub, assets, diff)).toThrow("contagens");
    pub.result.eligible_count = 2;
    pub.result.qualifying_value_minor!.BRL = "9007199254740992";
    expect(() => renderCharts(pub, assets, diff)).toThrow("valores");
  });

  it("não altera os objetos usados pelo mapa e pelas listas", () => {
    const data = fixture();
    const before = JSON.stringify(data);
    renderCharts(data.pub, data.assets, data.diff);
    expect(JSON.stringify(data)).toBe(before);
  });
});
