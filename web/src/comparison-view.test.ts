import { expect, it } from "vitest";
import type { Asset, ExposureResult } from "./api";
import { renderComparison, type ComparisonResponse } from "./comparison-view";

const result: ExposureResult = {
  calculation_id: "p", input_vector: { stream_scope: "REPLAY", earthquake_uid: "e", product_revision_id: "grid", tenant_id: "t", mode: "SHAKEMAP_MMI", portfolio_epoch: 1 },
  status: "COMPLETE", eligible_count: 1, qualifying_count: 1,
  qualifying_value_minor: { BRL: "9007199254740993" } as ExposureResult["qualifying_value_minor"],
  calculated_at: "2026-10-11T00:00:00Z", published_at: "2026-10-11T00:00:01Z", limitations: [],
};
const response: ComparisonResponse = {
  publication_id: "p", previous_publication_id: "old", previous_available: true, unavailable_reason: null,
  causes: ["<script>bad</script>"], before: { publication_id: "old", generation: null, result },
  after: { publication_id: "p", generation: 2, result }, total_changes: 0, limit: 100, offset: 0, changes: [],
};

it("preserves large amounts and displays an absent currency as zero", () => {
  const html = renderComparison(response);
  expect(html).toContain("R$ 90.071.992.547.409,93");
  expect(html).toContain("US$ 0,00");
  expect(html).not.toContain("<script>bad</script>");
});

it("withholds historical summaries when privacy invalidates the previous publication", () => {
  const secret: Asset = { asset_id: "private-old", lon: 12.34567, lat: 76.54321, currency: "BRL", insured_value_minor: "123", eligible: true, qualifying: true, mmi: 6.8 };
  const html = renderComparison({ ...response, previous_available: false, unavailable_reason: "privacy_epoch", changes: [{ asset_id: secret.asset_id, change: "removed", before: secret, after: null }] });
  expect(html).toContain("invalidada após uma exclusão");
  expect(html).not.toContain("90.071.992");
  expect(html).not.toContain("Anterior: lon");
  expect(html).not.toContain("private-old");
  expect(html).not.toContain("12.34567");
});

it("formats signed monetary differences exactly and keeps currencies separate", () => {
  const html = renderComparison({ ...response,
    before: { ...response.before!, result: { ...result, qualifying_value_minor: { BRL: "9007199254740993", USD: "55" } } },
    after: { ...response.after, result: { ...result, qualifying_value_minor: { BRL: "1", USD: "105" } } },
  });
  expect(html).toContain("−R$ 90.071.992.547.409,92");
  expect(html).toContain("+US$ 0,50");
  expect(html).toContain("Variação em BRL");
  expect(html).toContain("Variação em USD");
});

it("does not manufacture zero amounts or deltas for an unavailable result", () => {
  const missing: ExposureResult = { ...result, status: "NO_MAP", eligible_count: null, qualifying_count: null, qualifying_value_minor: null };
  const html = renderComparison({ ...response, before: { ...response.before!, result: missing }, after: { ...response.after, result: missing } });
  expect(html).toContain("Sem mapa de intensidade");
  expect(html).toContain('Variação em BRL</dt><dd>indisponível');
  expect(html).not.toContain("R$ 0,00");
  expect(html).not.toContain("US$ 0,00");
});

it("explains causes and changes of position and classification without exposing markup", () => {
  const before: Asset = { asset_id: '<img src=x onerror="bad">', lon: 120.12345, lat: -8.56789, currency: "BRL", insured_value_minor: "100", eligible: true, qualifying: false, mmi: 5.9 };
  const after: Asset = { ...before, lon: 120.22345, qualifying: true, mmi: 6.1 };
  const html = renderComparison({ ...response, causes: ["epoch da carteira", "revisão do evento"], total_changes: 1, changes: [{ asset_id: before.asset_id, change: "changed", before, after }] });
  expect(html).toContain("Versão da carteira");
  expect(html).toContain("Revisão do terremoto");
  expect(html).toContain("Posição · Entrou no critério · Intensidade");
  expect(html).toContain('class="asset-status qualifying">Classificado');
  expect(html).toContain('class="asset-status eligible">Elegível');
  expect(html).toContain('aria-label="Ver mudanças no mapa"');
  expect(html).not.toContain('aria-label="Ver mudanças no mapa" disabled');
  expect(html).not.toContain('<img src=x');
});

it("marks added and removed assets as absent in the corresponding publication", () => {
  const asset: Asset = { asset_id: "a", lon: 0, lat: 0, currency: "BRL", insured_value_minor: "100", eligible: false, qualifying: false, mmi: null };
  const html = renderComparison({ ...response, total_changes: 2, changes: [
    { asset_id: "a", change: "added", before: null, after: asset },
    { asset_id: "b", change: "removed", before: { ...asset, asset_id: "b" }, after: null },
  ] });
  expect(html).toContain("Adicionado");
  expect(html).toContain("Removido");
  expect(html.match(/>Ausente</g)).toHaveLength(2);
  expect(html).toContain("Não elegível");
});

it("keeps first-publication and empty-comparison controls clear", () => {
  expect(renderComparison({ ...response, before: null, previous_available: false, unavailable_reason: "first_publication" })).toContain("Primeira publicação");
  const html = renderComparison(response);
  expect(html).toContain("Nenhum ativo mudou");
  expect(html).toContain('aria-label="Ver mudanças no mapa" disabled');
  expect(html).toContain('data-comparison-page="previous" disabled');
  expect(html).toContain('data-comparison-page="next" disabled');
});
