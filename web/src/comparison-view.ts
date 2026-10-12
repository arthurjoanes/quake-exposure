import type { Asset, ExposureResult } from "./api";
import { assetMoved, classificationChange } from "./map-comparison";
import { formatMinor } from "./money";

export interface ComparisonPublication { publication_id: string; generation: number | null; result: ExposureResult }
export interface ComparisonResponse {
  publication_id: string;
  previous_publication_id: string | null;
  previous_available: boolean;
  unavailable_reason: "first_publication" | "privacy_epoch" | null;
  causes: string[];
  before: ComparisonPublication | null;
  after: ComparisonPublication;
  total_changes: number;
  offset: number;
  limit: number;
  changes: { asset_id: string; change: "added" | "removed" | "changed"; before: Asset | null; after: Asset | null }[];
}

const esc = (value: string) => value.replace(/[&<>"']/g, c => `&#${c.charCodeAt(0)};`);
const count = (value: number | null) => value === null ? "indisponível" : value.toLocaleString("pt-BR");
const CAUSE_LABELS: Record<string, string> = {
  "revisão do evento": "Revisão do terremoto", mapa: "Mapa de intensidade", "epoch da carteira": "Versão da carteira",
  "origem da carteira": "Cadastro na origem", "exclusão de titular": "Exclusão de titular", regra: "Critério de classificação",
};
const STATUS_LABELS: Record<ExposureResult["status"], string> = {
  COMPLETE: "Calculada", NO_MAP: "Sem mapa de intensidade", UNSUPPORTED_GRID: "Grid não suportado",
  RETRACTED: "Evento retirado", REDACTED_BY_ERASURE: "Invalidada após exclusão",
};
const MAP_ICON = '<svg class="ui-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="m9 5 6 3 6-3v14l-6 3-6-3-6 3V8l6-3Z"/><path d="M9 5v14M15 8v14"/></svg>';

function amount(result: ExposureResult, currency: "BRL" | "USD"): string | null {
  return result.status === "COMPLETE" && result.qualifying_value_minor !== null
    ? result.qualifying_value_minor[currency] ?? "0" : null;
}

function moneyDelta(before: string | null, after: string | null, currency: "BRL" | "USD"): string {
  if (before === null || after === null) return "indisponível";
  const delta = BigInt(after) - BigInt(before);
  const magnitude = delta < 0n ? -delta : delta;
  return `${delta > 0n ? "+" : delta < 0n ? "−" : ""}${formatMinor(magnitude.toString(), currency)}`;
}

function position(asset: Asset | null): string {
  if (!asset) return '<span class="muted">Ausente</span>';
  const state = asset.qualifying ? "qualifying" : asset.eligible ? "eligible" : "other";
  const label = asset.qualifying ? "Classificado" : asset.eligible ? "Elegível" : "Não elegível";
  return `<span class="asset-status ${state}">${label}</span><span class="comparison-coordinate">${asset.lon.toFixed(5)}, ${asset.lat.toFixed(5)}</span>`;
}

function changeLabel(change: ComparisonResponse["changes"][number]): string {
  if (change.change === "added") return "Adicionado";
  if (change.change === "removed") return "Removido";
  const classification = classificationChange(change);
  const labels = [];
  if (assetMoved(change)) labels.push("Posição");
  if (classification) labels.push(classification === "entered" ? "Entrou no critério" : "Saiu do critério");
  else if (change.before?.eligible !== change.after?.eligible) labels.push("Elegibilidade");
  if (change.before?.currency !== change.after?.currency || change.before?.insured_value_minor !== change.after?.insured_value_minor) labels.push("Valor cadastrado");
  if (change.before?.mmi !== change.after?.mmi) labels.push("Intensidade");
  return labels.join(" · ") || "Dados do ativo";
}

export function renderComparison(data: ComparisonResponse): string {
  if (!data.previous_available || !data.before) return `<div class="comparison-empty"><p>${data.unavailable_reason === "privacy_epoch" ? "Publicação anterior invalidada após uma exclusão." : "Primeira publicação: ainda não há comparação."}</p></div>`;
  const summary = (title: string, pub: ComparisonPublication) => {
    const r = pub.result;
    const complete = r.status === "COMPLETE";
    return `<article class="comparison-summary"><div class="comparison-summary-title"><h3>${title}</h3><span class="result-status" data-result-status="${esc(r.status)}">${esc(STATUS_LABELS[r.status] ?? r.status)}</span></div><dl class="comparison-values"><dt>Elegíveis</dt><dd>${count(complete ? r.eligible_count : null)}</dd><dt>Classificados</dt><dd>${count(complete ? r.qualifying_count : null)}</dd>
      ${(["BRL", "USD"] as const).map(currency => {
        const minor = amount(r, currency);
        return `<dt>${currency}</dt><dd>${minor === null ? "indisponível" : formatMinor(minor, currency)}</dd>`;
      }).join("")}</dl></article>`;
  };
  const before = data.before.result, after = data.after.result;
  const countDelta = before.status === "COMPLETE" && after.status === "COMPLETE" && before.qualifying_count !== null && after.qualifying_count !== null
    ? after.qualifying_count - before.qualifying_count : null;
  const end = Math.min(data.total_changes, data.offset + data.changes.length);
  return `<div class="comparison-overview"><p class="comparison-count">${count(data.total_changes)} ${data.total_changes === 1 ? "ativo alterado" : "ativos alterados"}</p><div class="comparison-causes" aria-label="Causas da mudança">${data.causes.map(cause => `<span class="cause-badge">${esc(CAUSE_LABELS[cause] ?? cause)}</span>`).join("") || '<span class="muted">Sem mudança de entradas</span>'}</div></div>
    <div class="comparison-summaries">${summary("Anterior", data.before)}${summary("Atual", data.after)}</div>
    <dl class="comparison-deltas" aria-label="Variação entre as publicações"><div><dt>Variação de classificados</dt><dd>${countDelta === null ? "indisponível" : `${countDelta > 0 ? "+" : countDelta < 0 ? "−" : ""}${count(Math.abs(countDelta))}`}</dd></div>${(["BRL", "USD"] as const).map(currency => `<div><dt>Variação em ${currency}</dt><dd>${moneyDelta(amount(before, currency), amount(after, currency), currency)}</dd></div>`).join("")}</dl>
    <div class="comparison-map-key"><details class="comparison-help"><summary>Como ler o mapa</summary><p class="muted">Contorno tracejado: posição anterior. Ponto preenchido: posição atual. A linha liga os deslocamentos.</p></details><button type="button" data-focus-comparison title="Ver mudanças no mapa" aria-label="Ver mudanças no mapa" ${data.changes.length ? "" : "disabled"}>${MAP_ICON}<span>Ver no mapa</span></button></div>
    <div class="scroll short comparison-table"><table><caption class="sr-only">Ativos alterados nesta página</caption><thead><tr><th scope="col">Ativo</th><th scope="col">Alteração</th><th scope="col">Anterior</th><th scope="col">Atual</th></tr></thead><tbody>
    ${data.changes.map(change => `<tr data-change="${esc(change.change)}"><th scope="row">${esc(change.asset_id)}</th><td><span class="change-kind ${esc(change.change)}">${changeLabel(change)}</span></td><td>${position(change.before)}</td><td>${position(change.after)}</td></tr>`).join("") || '<tr><td colspan="4">Nenhum ativo mudou entre essas publicações.</td></tr>'}</tbody></table></div>
    <nav class="comparison-pager" aria-label="Páginas da comparação"><button type="button" data-comparison-page="previous" ${data.offset === 0 ? "disabled" : ""}>Anteriores</button><span>${data.total_changes ? data.offset + 1 : 0}–${end} de ${count(data.total_changes)} alterações</span><button type="button" data-comparison-page="next" ${end >= data.total_changes ? "disabled" : ""}>Próximas</button></nav>
    <details class="comparison-details"><summary>Dados das publicações</summary><dl><dt>Versão anterior da carteira</dt><dd>${before.input_vector.portfolio_epoch.toLocaleString("pt-BR")}</dd><dt>Versão atual da carteira</dt><dd>${after.input_vector.portfolio_epoch.toLocaleString("pt-BR")}</dd><dt>ID anterior</dt><dd><code>${esc(data.before.publication_id)}</code></dd><dt>ID atual</dt><dd><code>${esc(data.after.publication_id)}</code></dd></dl></details>`;
}
