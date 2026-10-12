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
const utc = (value: string | null) => {
  if (!value) return "sem registro";
  const parsed = new Date(value);
  if (!Number.isFinite(parsed.getTime())) return "sem registro";
  return `${parsed.toLocaleString("pt-BR", { timeZone: "UTC", dateStyle: "short", timeStyle: "short" })} UTC`;
};
const number = (value: number) => value.toLocaleString("pt-BR");
const RESULT_LABELS: Record<ExposureResult["status"], string> = {
  COMPLETE: "Calculada", NO_MAP: "Sem mapa", UNSUPPORTED_GRID: "Grid não suportado",
  RETRACTED: "Evento retirado", REDACTED_BY_ERASURE: "Invalidada após exclusão",
};

interface Phase { title: string; value: string; note: string; state: "ready" | "pending" | "attention" | "snapshot" }

export function renderOperations(status: OperationsResponse, demo: boolean, pipelineExecuted = true): string {
  const cdc = status.cdc;
  const epoch = status.epochs;
  const jobs = status.jobs;
  const snapshot = demo && !pipelineExecuted;
  const connectorFailed = cdc.connector_state === "FAILED" || cdc.task_state === "FAILED";
  const cdcReady = !cdc.stale && cdc.connector_state === "RUNNING" && cdc.task_state === "RUNNING";
  const attention = !snapshot && (cdc.stale || connectorFailed || jobs.failed > 0 || cdc.last_error !== null && cdc.last_error !== undefined);
  const pending = !snapshot && (cdc.pending_transactions > 0 || jobs.pending > 0 || jobs.leased > 0 || jobs.outdated > 0);
  const phases: Phase[] = [
    { title: "Carteira", value: `Versão ${number(epoch.portfolio)}`, note: "", state: "ready" },
    { title: "Atualizações", value: connectorFailed ? "Falha no conector" : cdcReady ? "CDC conectado" : cdc.stale ? "Sem leitura recente do CDC" : "CDC aguardando conexão", note: cdc.pending_transactions ? `${number(cdc.pending_transactions)} transações pendentes` : "Sem pendências", state: connectorFailed || cdc.stale ? "attention" : cdcReady ? "ready" : "pending" },
    { title: "Cálculo", value: jobs.failed ? `${number(jobs.failed)} ${jobs.failed === 1 ? "cálculo com falha" : "cálculos com falha"}` : jobs.pending || jobs.leased ? `${number(jobs.pending)} na fila · ${number(jobs.leased)} em execução` : "Fila de cálculo vazia", note: jobs.failed ? `${number(jobs.pending)} na fila · ${number(jobs.leased)} em execução` : "", state: jobs.failed ? "attention" : jobs.pending || jobs.leased ? "pending" : "ready" },
    { title: "Publicação", value: jobs.outdated ? `${number(jobs.outdated)} ${jobs.outdated === 1 ? "resultado por atualizar" : "resultados por atualizar"}` : status.last_publication_at ? "Resultados publicados" : "Ainda sem publicação", note: utc(status.last_publication_at), state: jobs.outdated || !status.last_publication_at ? "pending" : "ready" },
  ];
  if (snapshot) {
    phases[0] = { title: "Carteira", value: `Versão ${number(epoch.portfolio)}`, note: "Carteira fictícia", state: "snapshot" };
    phases[1] = { title: "Atualizações", value: "CDC no ambiente local", note: "Não executado neste exemplo", state: "snapshot" };
    phases[2] = { title: "Cálculo", value: "MMI por ativo", note: "Grid capturado do USGS", state: "snapshot" };
    phases[3] = { title: "Consulta", value: "Snapshot REPLAY", note: utc(status.last_publication_at), state: "snapshot" };
  }
  const rows = status.publications.slice(0, 12).map(pub => `<tr><td><code title="${esc(pub.publication_id)}">${esc(pub.publication_id.slice(0, 12))}</code></td>
    <td>${esc(pub.stream_scope)}</td><td>${pub.mode === "SHAKEMAP_MMI" ? "MMI" : "Raio"}</td><td><span class="result-status" data-result-status="${esc(pub.status)}">${esc(RESULT_LABELS[pub.status] ?? pub.status)}</span></td>
    <td>${utc(pub.published_at)}</td><td>${pub.publication_delay_s === null ? "não medido" : `${pub.publication_delay_s.toLocaleString("pt-BR", { maximumFractionDigits: 3 })} s`}</td></tr>`).join("");
  const stateLabel = snapshot ? "Consulta REPLAY" : demo ? "Estado capturado" : attention ? "Precisa de atenção" : pending || !cdcReady ? "Em processamento" : "Fluxo em dia";
  return `<div class="section-heading" data-pipeline-executed="${pipelineExecuted}"><div><h2>Processamento</h2>${snapshot ? "" : `<p class="muted">${demo ? "Estado registrado na captura." : utc(status.generated_at)}</p>`}</div><span class="pipeline-state ${snapshot ? "snapshot" : attention ? "delayed" : pending || !cdcReady ? "pending" : ""}">${stateLabel}</span></div>
    <ol class="pipeline">${phases.map((phase, index) => `<li data-stage="${index + 1}" data-state="${phase.state}"><span class="pipeline-step-number" aria-hidden="true">${String(index + 1).padStart(2, "0")}</span><h3>${phase.title}</h3><p class="pipeline-step-state">${phase.value}</p>${phase.note ? `<span class="muted">${phase.note}</span>` : ""}</li>`).join("")}</ol>
    <details class="operations-details"><summary>${snapshot ? "Publicações" : "Dados técnicos"}</summary>${snapshot ? '<p class="muted">CDC não executado nesta demonstração; disponível na stack local.</p>' : `<dl class="operations-metadata"><dt>Origem da carteira</dt><dd>Versão ${number(epoch.source)}</dd><dt>Conector / tarefa</dt><dd>${esc(cdc.connector_state)} / ${esc(cdc.task_state)}</dd><dt>Slot</dt><dd>${cdc.slot_active === true ? "ativo" : cdc.slot_active === false ? "inativo" : "sem registro"}</dd><dt>WAL retido</dt><dd>${cdc.retained_wal_bytes === null ? "sem registro" : `${number(cdc.retained_wal_bytes)} bytes`}</dd><dt>Versão de exclusão</dt><dd>${number(epoch.privacy)}</dd></dl>`}
    ${!snapshot && cdc.last_error ? `<p class="operation-error">${esc(cdc.last_error)}</p>` : ""}
    <div class="scroll"><table><caption class="sr-only">Publicações recentes</caption><thead><tr><th scope="col">Publicação</th><th scope="col">Escopo</th><th scope="col">Critério</th><th scope="col">Situação</th><th scope="col">Publicada em</th><th scope="col">Cálculo → publicação</th></tr></thead><tbody>${rows || '<tr><td colspan="6">Nenhuma publicação registrada.</td></tr>'}</tbody></table></div>
    <p class="muted">O intervalo usa os horários gravados do cálculo e da publicação. O tempo da origem até o painel não é medido.</p></details>`;
}
