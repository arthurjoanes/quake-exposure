import { expect, it } from "vitest";
import { renderOperations, type OperationsResponse } from "./operations";

const status: OperationsResponse = {
  generated_at: "2026-10-12T13:42:36.876Z", tenant_id: "t",
  cdc: { connector_state: "RUNNING", task_state: "RUNNING", pending_transactions: 0, retained_wal_bytes: null, stale: false, slot_active: true, last_error: null },
  jobs: { pending: 0, leased: 0, failed: 0, outdated: 0 }, epochs: { portfolio: 3, source: 2, privacy: 1 },
  publications: [{ publication_id: "a".repeat(64), earthquake_uid: "event", stream_scope: "REPLAY", mode: "SHAKEMAP_MMI", generation: 3, status: "COMPLETE", published_at: "2026-10-12T13:42:36.876Z", calculated_at: "2026-10-12T13:42:36.876Z", publication_delay_s: null }],
  last_publication_at: "2026-10-12T13:42:36.876Z",
  sources: { feed: { last_ok_age_s: 0, stale: false, last_error: null }, incremental: { last_ok_age_s: 0, stale: false, last_error: null } },
};

it("describes a connected, empty pipeline with technical IDs inside details", () => {
  const html = renderOperations(status, false);
  expect(html).toContain("Fluxo em dia");
  expect(html).toContain("CDC conectado");
  expect(html).toContain("Fila de cálculo vazia");
  expect(html).toContain("Resultados publicados");
  const detailsIndex = html.indexOf('<details class="operations-details">');
  expect(html.indexOf("aaaaaaaaaaaa")).toBeGreaterThan(detailsIndex);
  expect(html.indexOf("RUNNING / RUNNING")).toBeGreaterThan(detailsIndex);
  expect(html).toContain("12/10/2026, 13:42 UTC");
  expect(html).toContain("não medido");
  expect(html).not.toContain(">0 s<");
});

it("presents fixture snapshots without claiming CDC was executed", () => {
  const html = renderOperations({ ...status, cdc: { ...status.cdc, stale: true, connector_state: "NOT_CONNECTED", task_state: "NOT_CONNECTED" } }, true, false);
  expect(html).toContain('data-pipeline-executed="false"');
  expect(html).toContain('class="pipeline-state snapshot">Consulta REPLAY');
  expect(html).toContain("Não executado neste exemplo");
  expect(html).toContain("Carteira fictícia");
  expect(html).toContain("Grid capturado do USGS");
  expect(html).not.toContain("Fluxo em dia");
  expect(html).not.toContain("CDC conectado");
  expect(html).not.toContain("Precisa de atenção");
  expect(html).not.toContain("NOT_CONNECTED");
});

it("distinguishes captured pipeline states from a current connected pipeline", () => {
  const html = renderOperations(status, true);
  expect(html).toContain("Estado capturado");
  expect(html).toContain("Estado registrado na captura");
  expect(html).not.toContain("Fluxo em dia");
  expect(html).not.toContain("Não executado neste exemplo");
});

it("surfaces failures even when CDC is connected and escapes technical errors", () => {
  const html = renderOperations({ ...status, jobs: { ...status.jobs, failed: 1 }, cdc: { ...status.cdc, last_error: '<script>alert("bad")</script>' } }, false);
  expect(html).toContain("Precisa de atenção");
  expect(html).toContain("1 cálculo com falha");
  expect(html).toContain('data-state="attention"');
  expect(html).not.toContain('<script>alert');
  expect(html).toContain("&#60;script&#62;");
});

it("displays pending work and stale CDC as distinct states", () => {
  const pending = renderOperations({ ...status, jobs: { pending: 4, leased: 2, failed: 0, outdated: 1 } }, false);
  expect(pending).toContain("Em processamento");
  expect(pending).toContain("4 na fila · 2 em execução");
  expect(pending).toContain("1 resultado por atualizar");
  const stale = renderOperations({ ...status, cdc: { ...status.cdc, stale: true } }, false);
  expect(stale).toContain("Sem leitura recente do CDC");
  expect(stale).toContain("Precisa de atenção");
});

it("does not insert arbitrary date strings as HTML", () => {
  const html = renderOperations({ ...status, generated_at: '<img src=x>', last_publication_at: null, publications: [] }, false);
  expect(html).not.toContain('<img');
  expect(html).toContain("Ainda sem publicação");
  expect(html).toContain("Nenhuma publicação registrada");
});
