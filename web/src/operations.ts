import type { ApiStatus, ExposureResult, Mode, Scope } from "./api";

export interface OperationsResponse {
  generated_at: string;
  tenant_id: string;
  cdc: NonNullable<ApiStatus["cdc"]> & { slot_active?: boolean | null; checked_age_s?: number | null; last_error?: string | null };
  jobs: { pending: number; leased: number; failed: number; outdated: number };
  epochs: { portfolio: number; source: number; privacy: number };
  publications: {
    publication_id: string; earthquake_uid: string; stream_scope: Scope; mode: Mode;
    generation: number; status: ExposureResult["status"]; published_at: string;
    calculated_at: string; publication_delay_s: number | null;
  }[];
  last_publication_at: string | null;
  sources: ApiStatus["sources"];
}

const esc = (text: string) => text.replace(/[&<>"']/g, c => `&#${c.charCodeAt(0)};`);
const utc = (value: string | null) => value ? `${value.slice(0, 19).replace("T", " ")} UTC` : "sem registro";
const number = (value: number) => value.toLocaleString("pt-BR");

export function renderOperations(status: OperationsResponse, demo: boolean, pipelineExecuted = true): string {
  const cdc = status.cdc;
  const epoch = status.epochs;
  const jobs = status.jobs;
  const phases = [
    ["Carteira", `Versão ${number(epoch.portfolio)}`, `Origem ${number(epoch.source)}`],
    ["CDC", `${esc(cdc.connector_state)} / ${esc(cdc.task_state)}`, `${number(cdc.pending_transactions)} transações pendentes`],
    ["Cálculo", `${number(jobs.pending)} na fila · ${number(jobs.leased)} em execução`, `${number(jobs.failed)} com falha`],
    ["Publicação", `${number(jobs.outdated)} aguardando atualização`, utc(status.last_publication_at)],
  ];
  if (demo && !pipelineExecuted) {
    phases[1] = ["CDC", "Disponível no ambiente local", "Não executado neste exemplo"];
    phases[2] = ["Cálculo", "MMI no ponto de cada ativo", "Grid capturado do USGS"];
    phases[3] = ["Resultado", "Snapshot para consulta", utc(status.last_publication_at)];
  }
  const rows = status.publications.slice(0, 12).map(pub => `<tr><td><code title="${esc(pub.publication_id)}">${esc(pub.publication_id.slice(0, 12))}</code></td>
    <td>${esc(pub.stream_scope)}</td><td>${pub.mode === "SHAKEMAP_MMI" ? "MMI" : "Raio"}</td><td>${esc(pub.status)}</td>
    <td>${utc(pub.published_at)}</td><td>${pub.publication_delay_s === null ? "sem registro" : `${pub.publication_delay_s.toLocaleString("pt-BR", { maximumFractionDigits: 3 })} s`}</td></tr>`).join("");
  return `<div class="section-heading"><div><h2>Operação da carteira</h2><p class="muted">${demo ? pipelineExecuted ? "Estado registrado na captura da demonstração." : "Exemplo calculado a partir das capturas. O ambiente local executa o fluxo completo pelo CDC." : `Consulta de ${utc(status.generated_at)}.`}</p></div><span class="pipeline-state ${!demo && cdc.stale ? "delayed" : ""}">${demo ? "Captura REPLAY" : cdc.stale ? "CDC precisa de atenção" : "CDC em dia"}</span></div>
    <ol class="pipeline">${phases.map(([title, value, note]) => `<li><h3>${title}</h3><p>${value}</p><span class="muted">${note}</span></li>`).join("")}</ol>
    <details><summary>${demo && !pipelineExecuted ? "Resultados do exemplo" : "Publicações e estado do conector"}</summary><p>${demo && !pipelineExecuted ? "O snapshot não consulta bancos, slots ou tópicos Kafka." : `Slot: ${cdc.slot_active === true ? "ativo" : cdc.slot_active === false ? "inativo" : "sem registro"}. WAL retido: ${cdc.retained_wal_bytes === null ? "sem registro" : `${number(cdc.retained_wal_bytes)} bytes`}. Versão de exclusão: ${number(epoch.privacy)}.`}</p>
    ${cdc.last_error ? `<p class="operation-error">${esc(cdc.last_error)}</p>` : ""}
    <div class="scroll"><table><thead><tr><th>Publicação</th><th>Escopo</th><th>Critério</th><th>Situação</th><th>Publicada em</th><th>Intervalo cálculo → publicação</th></tr></thead><tbody>${rows || '<tr><td colspan="6">Nenhuma publicação registrada.</td></tr>'}</tbody></table></div>
    <p class="muted">O intervalo é a diferença entre os horários gravados do cálculo e da publicação. O tempo completo da origem até o painel não é medido.</p></details>`;
}
