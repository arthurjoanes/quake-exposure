import type { Asset, ExposureResult } from "./api";
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

export function renderComparison(data: ComparisonResponse): string {
  if (!data.previous_available || !data.before) return `<p class="muted">${data.unavailable_reason === "privacy_epoch" ? "A publicação anterior foi invalidada após uma exclusão. Suas posições não estão disponíveis." : "Esta é a primeira publicação disponível para comparação."}</p>`;
  const summary = (title: string, pub: ComparisonPublication) => {
    const r = pub.result;
    return `<div><h3>${title}</h3><p>Carteira na versão ${r.input_vector.portfolio_epoch.toLocaleString("pt-BR")}</p><dl><dt>Classificados</dt><dd>${count(r.qualifying_count)}</dd>
      ${(["BRL", "USD"] as const).map(currency => `<dt>${currency}</dt><dd>${r.qualifying_value_minor ? formatMinor(r.qualifying_value_minor[currency] ?? "0", currency) : "indisponível"}</dd>`).join("")}</dl></div>`;
  };
  const position = (asset: Asset | null) => asset ? `${asset.lon.toFixed(5)}, ${asset.lat.toFixed(5)} · ${asset.qualifying ? "classificado" : asset.eligible ? "elegível" : "não elegível"}` : "ausente";
  const end = Math.min(data.total_changes, data.offset + data.changes.length);
  return `<p>Mudou por: ${esc(data.causes.join(", ") || "sem mudança no vetor")}. ${count(data.total_changes)} ${data.total_changes === 1 ? "ativo com alteração" : "ativos com alterações"}.</p>
    <div class="comparison-summaries">${summary("Anterior", data.before)}${summary("Atual", data.after)}</div>
    <p class="muted">Contorno tracejado: posição anterior. Ponto preenchido: posição atual. As linhas ligam as mudanças de posição.</p>
    <div class="scroll short"><table><thead><tr><th>Ativo</th><th>Anterior: lon, lat</th><th>Atual: lon, lat</th></tr></thead><tbody>
    ${data.changes.map(change => `<tr><td>${esc(change.asset_id)}</td><td>${position(change.before)}</td><td>${position(change.after)}</td></tr>`).join("") || '<tr><td colspan="3">Nenhum ativo mudou entre essas publicações.</td></tr>'}</tbody></table></div>
    <nav class="comparison-pager" aria-label="Páginas da comparação"><button type="button" data-comparison-page="previous" ${data.offset === 0 ? "disabled" : ""}>Anteriores</button><span>${data.total_changes ? data.offset + 1 : 0}–${end} de ${count(data.total_changes)} alterações</span><button type="button" data-comparison-page="next" ${end >= data.total_changes ? "disabled" : ""}>Próximas</button></nav>`;
}
