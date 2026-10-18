import "leaflet/dist/leaflet.css";
import "./style.css";
import {
  type ApiStatus,
  type Asset,
  type AssetsResponse,
  type CurrentPublication,
  type DiffItem,
  type DiffResponse,
  get,
  type Mode,
  PrivacyRecalculation,
  type QuakeEvent,
  type Revision,
  type ResultStatus,
  type Scope,
  type Tenant,
} from "./api";
import { AssetList } from "./asset-list";
import { renderCharts } from "./charts";
import { filterEventPeriod, normalizeDay, recentEventPeriod } from "./event-period";
import { QuakeMap } from "./map-view";
import { formatMinor } from "./money";
import { PublicationSync } from "./publication";
import { LatestRefresh } from "./refresh";
import { renderArea } from "./exploration";
import { renderComparison, type ComparisonResponse } from "./comparison-view";
import { renderOperations, type OperationsResponse } from "./operations";
import { filterAssetsInPolygon, type AreaPolygon } from "./spatial";
import type { RegionResponse } from "./map-region";
import type { DemoMetadata } from "./demo-api";
import { readListState, writeListState, readComparison, writeComparison } from "./navigation-state";

const REFRESH_MS = 10_000;

const STATUS_TEXT: Record<ResultStatus, string> = {
  COMPLETE: "completo",
  NO_MAP: "sem ShakeMap",
  UNSUPPORTED_GRID: "grid não suportado",
  RETRACTED: "evento retirado",
  REDACTED_BY_ERASURE: "removido por exclusão de titular",
};

const MODE_NOTE: Record<Mode, string> = {
  RADIUS_TRIAGE: "Ativos até 100 km do epicentro. É proximidade, não intensidade.",
  SHAKEMAP_MMI:
    "MMI do ShakeMap no ponto do ativo; classificado com MMI ≥ 6,0. É regra de triagem, não modelo de dano.",
};

const SCOPE_NOTE: Record<Scope, string> = {
  LIVE: "",
  REPLAY: "REPLAY: capturas reais reprocessadas, com o horário original.",
  FAULT: "FAULT: dados com mutação de teste.",
};

const el = <T extends HTMLElement = HTMLElement>(id: string): T => {
  const e = document.getElementById(id);
  if (!e) throw new Error(`#${id} não existe`);
  return e as T;
};

const scopeSel = el<HTMLSelectElement>("scope");
const tenantSel = el<HTMLSelectElement>("tenant");
const eventSel = el<HTMLSelectElement>("event");
const eventFrom = el<HTMLInputElement>("event-from");
const eventTo = el<HTMLInputElement>("event-to");
const modeSel = el<HTMLSelectElement>("mode");
const clock = el("clock");
const scopeNote = el("scope-note");
const staleBox = el("stale");
const eventPanel = el("event-panel");
const summaryBox = el("summary");
const diffBox = el("diff");
const assetsBox = el("assets");
const chartsBox = el("charts");
const focusButton = el<HTMLButtonElement>("focus-event");
const fitButton = el<HTMLButtonElement>("fit-assets");
const drawAreaButton = el<HTMLButtonElement>("draw-area");
const finishAreaButton = el<HTMLButtonElement>("finish-area");
const undoAreaButton = el<HTMLButtonElement>("undo-area");
const cancelAreaButton = el<HTMLButtonElement>("cancel-area");
const clearAreaButton = el<HTMLButtonElement>("clear-area");
const compareButton = el<HTMLButtonElement>("compare-map");
const areaSummary = el("area-summary");
const comparisonBox = el("comparison");
const operationsBox = el("operations");
const feedbackBox = el("app-feedback");
const filtersPanel = el<HTMLDetailsElement>("analysis-filters");
const retryAnalysis = el<HTMLButtonElement>("retry-analysis");
const retryRegion = el<HTMLButtonElement>("retry-region");
const DEMO = import.meta.env.VITE_DEMO === "1";
let demoSnapshotAt = "";
let demoPipelineExecuted = true;
const initialParams = new URLSearchParams(location.search);
eventFrom.value = normalizeDay(initialParams.get("from") ?? "");
eventTo.value = normalizeDay(initialParams.get("to") ?? "");
if (["LIVE", "REPLAY", "FAULT"].includes(initialParams.get("scope") ?? "")) scopeSel.value = initialParams.get("scope")!;
if (["RADIUS_TRIAGE", "SHAKEMAP_MMI"].includes(initialParams.get("mode") ?? "")) modeSel.value = initialParams.get("mode")!;

const state = {
  events: [] as QuakeEvent[],
  revisions: [] as Revision[],
  uid: initialParams.get("event") ?? "",
  shown: null as CurrentPublication | null, // publicação cujos ativos, resumo e diff estão na tela
  assets: [] as Asset[],
  area: null as AreaPolygon | null,
  comparison: null as ComparisonResponse | null,
};
const sync = new PublicationSync();
const refreshes = new LatestRefresh();
let eventSignature = "";
let initialSelection = true;
let regionGeneration = 0;
let regionTimer = 0;
let comparisonGeneration = 0;
let comparisonOffset = 0;
let areaDrawing = false;
let restoreInitialList = true;
let restoreInitialComparison = readComparison(initialParams);
let operationsGeneration = 0;

// ---------- mapa ----------

const map = new QuakeMap("map", {
  onSelect: selectEvent,
  onBaseStatus: (message) => { el("map-base-status").textContent = message.startsWith("OpenStreetMap") ? "" : message; },
  onAreaChange: (polygon) => { state.area = polygon; applyArea(); },
  onAreaStatus: ({ drawing, vertices, message }) => {
    areaDrawing = drawing;
    document.querySelector<HTMLElement>(".area-controls")?.setAttribute("data-drawing", String(drawing));
    finishAreaButton.hidden = !drawing;
    finishAreaButton.disabled = vertices < 3;
    undoAreaButton.hidden = !drawing;
    undoAreaButton.disabled = vertices === 0;
    cancelAreaButton.hidden = !drawing;
    clearAreaButton.hidden = drawing || !state.area;
    drawAreaButton.disabled = drawing || state.shown?.result.status !== "COMPLETE";
    el("area-status").textContent = message;
    el("area-status").hidden = !drawing;
  },
  onViewportChange: () => scheduleRegion(),
  onDensityChange: ({ assets, groups, clustered }) => {
    fitButton.disabled = visibleAssets().length === 0;
    el("map-density").textContent = assets === 0
      ? state.shown ? !state.area && state.assets.length ? "Carregando ativos…" : "Nenhum ativo neste enquadramento." : "Escolha um terremoto"
      : clustered
        ? `${assets.toLocaleString("pt-BR")} ativos · ${groups.toLocaleString("pt-BR")} pontos e grupos`
        : `${assets.toLocaleString("pt-BR")} ativos neste enquadramento`;
    el("map-density").title = "Ativos no enquadramento atual. Amplie para separar os grupos.";
  },
});

function visibleAssets(): Asset[] {
  return state.area ? filterAssetsInPolygon(state.assets, state.area) : state.assets;
}
const assetList = new AssetList(assetsBox, (asset) => {
  map.focusAsset(asset);
  el("map-wrap").scrollIntoView({ behavior: matchMedia("(prefers-reduced-motion: reduce)").matches ? "instant" : "smooth", block: "start" });
}, () => rememberSelection());

function applyArea(): void {
  if (!state.shown) return;
  const assets = visibleAssets();
  assetList.setContext(state.area ? "Área selecionada" : "Carteira inteira");
  assetList.setAssets(assets);
  setActionLabel(fitButton, state.area ? "Área" : "Carteira", state.area ? "Ver área no mapa" : "Ver carteira no mapa");
  fitButton.disabled = assets.length === 0;
  setActionLabel(drawAreaButton, state.area ? "Nova área" : "Área", state.area ? "Redesenhar área" : "Selecionar área");
  clearAreaButton.hidden = areaDrawing || !state.area;
  areaSummary.hidden = !state.area;
  if (state.area) {
    retryRegion.hidden = true;
    areaSummary.innerHTML = `${renderArea(assets)}<a href="#assets">Ver ativos desta área</a>`;
    const event = state.events.find(item => item.earthquake_uid === state.uid);
    map.setAssets(assets, `${state.shown.publication_id}|${JSON.stringify(state.area)}`, state.shown.result.input_vector.mode, event);
  } else {
    areaSummary.innerHTML = "";
    scheduleRegion();
  }
}

function scheduleRegion(): void {
  regionGeneration++;
  clearTimeout(regionTimer);
  if (!state.shown || state.area) return;
  regionTimer = window.setTimeout(() => { void loadRegion(); }, 90);
}

async function loadRegion(): Promise<void> {
  const publication = state.shown;
  if (!publication || state.area) return;
  const tenant = tenantSel.value;
  const generation = ++regionGeneration;
  const viewport = map.getViewport();
  try {
    const region = await get<RegionResponse>(`/api/publications/${encodeURIComponent(publication.publication_id)}/region`, {
      tenant, ...Object.fromEntries(Object.entries(viewport).map(([key, value]) => [key, String(value)])), limit: "1200",
    });
    if (generation !== regionGeneration || tenant !== tenantSel.value || state.area || state.shown?.publication_id !== publication.publication_id) return;
    if (!region || region.publication_id !== publication.publication_id) throw new Error("Região indisponível.");
    map.setRegion(region, publication.publication_id, publication.result.input_vector.mode, state.events.find(item => item.earthquake_uid === state.uid));
    retryRegion.hidden = true;
  } catch (error) {
    if (generation !== regionGeneration || tenant !== tenantSel.value || state.shown?.publication_id !== publication.publication_id) return;
    if (error instanceof PrivacyRecalculation) { sync.reset(); paint(null, null, error.message); }
    else if (!state.area) {
      el("map-density").textContent = "Não foi possível consultar os ativos deste enquadramento.";
      retryRegion.hidden = false;
    }
  }
}

async function loadComparison(offset = 0, reveal = true): Promise<void> {
  const publication = state.shown;
  if (!publication) return;
  const tenant = tenantSel.value;
  const generation = ++comparisonGeneration;
  comparisonOffset = offset;
  compareButton.setAttribute("aria-pressed", "true");
  comparisonBox.hidden = false;
  comparisonBox.setAttribute("aria-busy", "true");
  const heading = `<div class="section-heading"><div><h2>Comparação</h2><span class="section-scope">Carteira inteira</span></div><button class="icon-button" type="button" data-close-comparison title="Fechar comparação" aria-label="Fechar comparação"><svg class="ui-icon" viewBox="0 0 24 24" aria-hidden="true"><path d="m6 6 12 12M6 18 18 6"/></svg></button></div>`;
  comparisonBox.innerHTML = `${heading}<p class="muted" role="status">Consultando as mudanças…</p>`;
  if (reveal) revealPanel(comparisonBox);
  rememberSelection();
  try {
    const data = await get<ComparisonResponse>(`/api/publications/${encodeURIComponent(publication.publication_id)}/comparison`, { tenant, offset: String(offset), limit: "100" });
    if (generation !== comparisonGeneration || tenant !== tenantSel.value || state.shown?.publication_id !== publication.publication_id) return;
    if (!data || data.publication_id !== publication.publication_id) throw new Error("Comparação indisponível.");
    state.comparison = data;
    comparisonBox.innerHTML = `${heading}${renderComparison(data)}`;
    map.setComparison(data.previous_available && data.before ? data.changes : []);
    if (reveal) comparisonBox.focus({ preventScroll: true });
    el("analysis-announcement").textContent = data.previous_available ? `${data.total_changes.toLocaleString("pt-BR")} ativos alterados entre as publicações.` : "Publicação anterior indisponível para comparação.";
  } catch (error) {
    if (generation !== comparisonGeneration || state.shown?.publication_id !== publication.publication_id) return;
    if (error instanceof PrivacyRecalculation) { sync.reset(); paint(null, null, error.message); }
    else {
      state.comparison = null;
      map.clearComparison();
      comparisonBox.innerHTML = `${heading}<p role="status">Não foi possível consultar as mudanças entre publicações.</p><button type="button" data-retry-comparison>Tentar novamente</button>`;
    }
  } finally {
    if (generation === comparisonGeneration) {
      comparisonBox.setAttribute("aria-busy", "false");
      compareButton.disabled = !state.shown;
    }
  }
}

function closeComparison(returnFocus = false): void {
  comparisonGeneration++;
  state.comparison = null;
  comparisonBox.hidden = true;
  comparisonBox.innerHTML = "";
  comparisonBox.setAttribute("aria-busy", "false");
  compareButton.setAttribute("aria-pressed", "false");
  map.clearComparison();
  if (returnFocus) {
    compareButton.focus({ preventScroll: true });
    revealPanel(el("map-wrap"), false);
  }
}

function revealPanel(panel: HTMLElement, focus = true): void {
  panel.scrollIntoView({ behavior: matchMedia("(prefers-reduced-motion: reduce)").matches ? "instant" : "smooth", block: "start" });
  if (focus) panel.focus({ preventScroll: true });
}

function setActionLabel(button: HTMLButtonElement, label: string, description: string): void {
  button.querySelector("[data-label]")!.textContent = label;
  button.setAttribute("aria-label", description);
  button.title = description;
}

// ---------- formatação ----------

const esc = (s: string): string => s.replace(/[&<>"']/g, (c) => `&#${c.charCodeAt(0)};`);
const num = (x: number | null, digits: number): string =>
  x === null ? "–" : x.toFixed(digits).replace(".", ",");
const utc = (iso: string | null): string =>
  iso === null ? "–" : `${iso.slice(0, 19).replace("T", " ")} UTC`;
const shortId = (id: string): string => `<code title="${esc(id)}">${esc(id.slice(0, 12))}</code>`;
const count = (n: number | null): string => (n === null ? "–" : n.toLocaleString("pt-BR"));
const mmiChange = (i: DiffItem): string =>
  i.mmi_before === null && i.mmi_after === null ? "" : `MMI ${num(i.mmi_before, 2)} → ${num(i.mmi_after, 2)}`;
const eventLabel = (e: QuakeEvent): string =>
  `${utc(e.event_time)} · M ${num(e.mag, 1)} · ${e.preferred_id}` +
  (e.lifecycle === "RETRACTED" ? " · retirado" : "");

const selection = () => ({
  scope: scopeSel.value as Scope,
  tenant: tenantSel.value,
  uid: state.uid,
  mode: modeSel.value as Mode,
});
const selectionKey = (): string => [...Object.values(selection()), eventFrom.value, eventTo.value].join("|");
function rememberSelection(): void {
  const s = selection();
  const params = new URLSearchParams(location.search);
  params.set("scope", s.scope);
  params.set("tenant", s.tenant);
  params.set("mode", s.mode);
  if (s.uid) params.set("event", s.uid);
  else params.delete("event");
  for (const [key, value] of [["from", eventFrom.value], ["to", eventTo.value]]) {
    if (value) params.set(key, value);
    else params.delete(key);
  }
  writeListState(params, assetList.getState());
  writeComparison(params, !comparisonBox.hidden);
  history.replaceState(null, "", `${location.pathname}?${params}${location.hash}`);
  el("filter-selection").textContent = `${tenantSel.selectedOptions[0]?.textContent ?? "Escolher carteira"} · ${modeSel.value === "SHAKEMAP_MMI" ? "MMI ≥ 6,0" : "100 km"}`;
}

// ---------- eventos ----------

function visibleEvents(): QuakeEvent[] {
  if (eventFrom.value && eventTo.value && eventFrom.value > eventTo.value) return [];
  return filterEventPeriod(state.events, eventFrom.value, eventTo.value);
}

function renderEventOptions(): void {
  const events = visibleEvents();
  const opts: [string, string][] = [
    ["", events.length ? "escolha um evento" : "nenhum evento neste período"],
    ...events.map((e): [string, string] => [e.earthquake_uid, eventLabel(e)]),
  ];
  const signature = opts.map((o) => o.join("\t")).join("\n");
  if (signature !== eventSignature) {
    eventSignature = signature;
    eventSel.replaceChildren(...opts.map(([value, text]) => new Option(text, value)));
  }
  eventSel.value = state.uid;
  const invalid = Boolean(eventFrom.value && eventTo.value && eventFrom.value > eventTo.value);
  eventTo.setAttribute("aria-invalid", String(invalid));
  el("event-period-note").textContent = invalid
    ? "A data inicial deve vir antes da final. Ajuste o período."
    : `${events.length} de ${state.events.length} eventos no catálogo. Datas em UTC; o período filtra eventos, sem somar exposições entre terremotos.`;
  const latest = recentEventPeriod(state.events, 1).to;
  el("event-period-reference").textContent = latest
    ? `Atalhos de 7 e 30 dias terminam em ${latest.split("-").reverse().join("/")} (UTC), data do evento mais recente do catálogo.`
    : "Sem eventos para definir a referência dos atalhos.";
}

function drawEvents(): void {
  map.setEvents(visibleEvents(), state.uid);
  focusButton.disabled = !visibleEvents().some((event) => event.earthquake_uid === state.uid);
}

function renderEventPanel(): void {
  const e = state.events.find((x) => x.earthquake_uid === state.uid);
  if (!e) {
    eventPanel.innerHTML = `<h2>Evento</h2><p class="muted">Escolha um evento na lista ou no mapa.</p>`;
    return;
  }
  const result = state.shown?.result;
  const rows = state.revisions
    .map(
      (r) =>
        `<tr><td>${esc(utc(r.source_updated_at))}</td><td>${esc(r.provider_event_id)}</td>` +
        `<td>${num(r.mag, 1)}</td><td>${esc(r.status ?? "–")}</td></tr>`,
    )
    .join("");
  eventPanel.innerHTML = `
    <h2>Terremoto</h2><p class="event-link"><a href="https://earthquake.usgs.gov/earthquakes/eventpage/${encodeURIComponent(e.preferred_id)}" target="_blank" rel="noopener noreferrer" title="Consultar o evento no USGS">${esc(e.preferred_id)} ↗</a><span>M ${num(e.mag, 1)} · ${e.depth_km === null ? "–" : `${num(e.depth_km, 1)} km`}</span></p>
    <details class="event-details"><summary>Revisões e detalhes ${scopeBadge(e.stream_scope)}</summary>
    <dl>
      <dt>ID USGS</dt><dd>${esc(e.preferred_id)}</dd>
      <dt>Horário</dt><dd>${esc(utc(e.event_time))}</dd>
      <dt>Magnitude</dt><dd>${num(e.mag, 1)}</dd>
      <dt>Profundidade</dt><dd>${e.depth_km === null ? "–" : `${num(e.depth_km, 1)} km`}</dd>
      <dt>Situação</dt><dd>${e.lifecycle === "RETRACTED" ? "Retirado pelo USGS" : "Ativo"}</dd>
      <dt>Mapa escolhido</dt><dd>${e.product_revision_id ? `<code>${esc(e.product_revision_id)}</code>` : "sem ShakeMap"}</dd>
      <dt>Resultado</dt><dd>${result ? `${result.status} (${STATUS_TEXT[result.status]})` : "sem publicação"}</dd>
    </dl>
    <h3>Revisões (${state.revisions.length})</h3>
    <div class="scroll short"><table>
      <thead><tr><th>Atualizada em</th><th>ID</th><th>M</th><th>Status</th></tr></thead>
      <tbody>${rows}</tbody>
    </table></div></details>`;
}

const scopeBadge = (s: Scope): string => (s === "LIVE" ? "" : `<span class="badge ${s}">${s}</span>`);

// ---------- publicação ----------

interface View {
  summary: string;
  diff: string;
  charts: string;
  assets: Asset[];
}

/** Monta tudo antes de tocar na tela: se algum valor for inválido, nada muda. */
function build(pub: CurrentPublication, assets: AssetsResponse, diff: DiffResponse): View {
  const r = pub.result;
  const event = state.events.find((e) => e.earthquake_uid === r.input_vector.earthquake_uid);
  const totals = r.qualifying_value_minor;
  const summary = `
    <div class="summary-heading"><h2>Carteira inteira</h2><span class="result-status" data-result-status="${r.status}">${STATUS_TEXT[r.status]}</span></div>
    <p class="summary-event"><strong>${esc(event?.preferred_id ?? "Terremoto")}</strong>${esc(event ? utc(event.event_time) : "")}</p>
    <dl class="summary-counts"><div class="classified"><dt>Classificados</dt><dd>${count(r.qualifying_count)}</dd></div><div><dt>Elegíveis</dt><dd>${count(r.eligible_count)}</dd></div></dl>
    <dl class="currency-values"><div><dt>Classificados · BRL</dt><dd>${totals ? formatMinor(totals.BRL ?? "0", "BRL") : "–"}</dd></div><div><dt>Classificados · USD</dt><dd>${totals ? formatMinor(totals.USD ?? "0", "USD") : "–"}</dd></div></dl>
    <span class="criterion-tag">${r.input_vector.mode === "SHAKEMAP_MMI" ? "MMI ≥ 6,0" : "Raio de 100 km"}</span>
    <details class="publication-details"><summary>Critério e publicação</summary><p>${MODE_NOTE[r.input_vector.mode]}</p><dl>
      <dt>Status</dt><dd>${r.status}</dd>
      <dt>Mapa usado</dt><dd>${r.input_vector.product_revision_id === "NONE" ? "nenhum (raio)" : `<code>${esc(r.input_vector.product_revision_id)}</code>`}</dd>
      <dt>Geração</dt><dd>${pub.generation}</dd>
      <dt>Calculada em</dt><dd>${esc(utc(r.calculated_at))}</dd>
      <dt>Publicada em</dt><dd>${esc(utc(r.published_at))}</dd>
      <dt>Publicação</dt><dd>${shortId(pub.publication_id)}</dd>
    </dl><p class="muted">Limitações: ${r.limitations.map((l) => `<code>${esc(l)}</code>`).join(" ") || "–"}</p></details>`;

  const diffList = (title: string, items: DiffItem[]): string => {
    const shown = items.slice(0, 100);
    const rows = shown
      .map(
        (i) =>
          `<tr><td>${esc(i.asset_id)}</td><td class="money">${formatMinor(i.insured_value_minor, i.currency)}</td>` +
          `<td>${mmiChange(i)}</td></tr>`,
      )
      .join("");
    const more = items.length > shown.length ? `<p class="muted">e mais ${items.length - shown.length}</p>` : "";
    return `<h3>${title} (${items.length})</h3>${rows ? `<div class="scroll short"><table><tbody>${rows}</tbody></table></div>` : ""}${more}`;
  };
  const diffHtml = `
    <h2>Entradas e saídas</h2>
    <details class="diff-details"><summary>Motivo da mudança</summary><dl>
      <dt>Anterior</dt><dd>${diff.previous_publication_id ? shortId(diff.previous_publication_id) : "nenhuma"}</dd>
      <dt>Mudou por</dt><dd>${esc(diff.causes.map(cause => cause === "epoch da carteira" ? "versão da carteira" : cause).join(", ") || "nenhuma alteração")}</dd>
    </dl></details>
    ${diffList("Entraram", diff.entered)}
    ${diffList("Saíram", diff.left)}<button type="button" data-open-comparison>Ver comparação completa</button>`;

  return { summary, diff: diffHtml, assets: assets.assets, charts: renderCharts(pub, assets, diff) };
}

function paint(pub: CurrentPublication | null, view: View | null, message = ""): void {
  if (state.shown?.publication_id !== pub?.publication_id) closeComparison();
  state.shown = pub;
  if (view) {
    const event = state.events.find((event) => event.earthquake_uid === state.uid);
    state.assets = view.assets;
    if (pub) map.setAssets([], pub.publication_id, pub.result.input_vector.mode, event);
    summaryBox.innerHTML = view.summary;
    diffBox.innerHTML = view.diff;
    chartsBox.innerHTML = view.charts;
    drawAreaButton.disabled = areaDrawing || pub?.result.status !== "COMPLETE";
    compareButton.disabled = false;
    applyArea();
    if (restoreInitialList) {
      restoreInitialList = false;
      assetList.restoreState(readListState(initialParams));
    }
    rememberSelection();
    el("analysis-announcement").textContent = `Carteira carregada. ${count(pub?.result.qualifying_count ?? null)} ativos classificados.`;
    if (restoreInitialComparison) {
      restoreInitialComparison = false;
      void loadComparison(0, false);
    }
  } else {
    regionGeneration++;
    clearTimeout(regionTimer);
    closeComparison();
    state.assets = [];
    state.area = null;
    map.clearAreaSelection();
    map.clearAssets();
    assetList.clear();
    summaryBox.innerHTML = `<h2>Carteira inteira</h2><p class="muted">${esc(message)}</p>`;
    diffBox.innerHTML = "";
    chartsBox.innerHTML = `<div class="chart-empty"><p>${esc(message)} Os gráficos acompanham a publicação selecionada.</p></div>`;
    drawAreaButton.disabled = true;
    compareButton.disabled = true;
    areaSummary.hidden = true;
    areaSummary.innerHTML = "";
    clearAreaButton.hidden = true;
    retryRegion.hidden = true;
    setActionLabel(fitButton, "Carteira", "Ver carteira no mapa");
    setActionLabel(drawAreaButton, "Área", "Selecionar área");
  }
  renderEventPanel();
}

async function refreshPublication(key: string, isCurrent: () => boolean, signal: AbortSignal): Promise<void> {
  const { scope, tenant, uid, mode } = selection();
  if (!uid || !tenant) {
    sync.reset();
    paint(null, null, uid ? "Nenhum tenant." : "Escolha um evento.");
    return;
  }
  const pub = await get<CurrentPublication>("/api/publications/current", {
    tenant,
    earthquake_uid: uid,
    mode,
    scope,
  }, signal);
  if (!isCurrent() || key !== selectionKey()) return;
  if (!pub) {
    sync.seen(null);
    paint(null, null, "Sem publicação para este evento, tenant e modo.");
    return;
  }
  if (!sync.seen(pub.publication_id)) return;
  const id = encodeURIComponent(pub.publication_id);
  const [assets, diff] = await Promise.all([
    get<AssetsResponse>(`/api/publications/${id}/assets`, { tenant }, signal),
    get<DiffResponse>(`/api/publications/${id}/diff`, { tenant }, signal),
  ]);
  if (!isCurrent() || key !== selectionKey() || !assets || !diff) return;
  const view = build(pub, assets, diff);
  if (sync.accept({ pub, assets, diff })) paint(pub, view);
}

// ---------- estado das fontes ----------

function renderStale(s: ApiStatus): void {
  const age = (x: number | null): string =>
    x === null ? "nunca" : x < 7200 ? `há ${Math.round(x / 60)} min` : `há ${Math.round(x / 3600)} h`;
  const lines: string[] = [];
  const sources: [string, keyof ApiStatus["sources"]][] = [
    ["Feed do USGS", "feed"],
    ["Incremental FDSN", "incremental"],
  ];
  for (const [label, key] of sources) {
    const src = s.sources[key];
    if (src.stale) {
      lines.push(
        `${label} desatualizado: último ok ${age(src.last_ok_age_s)}` +
          (src.last_error ? `; último erro: ${src.last_error}` : ""),
      );
    }
  }
  if (s.jobs_failed > 0) lines.push(`${s.jobs_failed} cálculos parados após 5 tentativas.`);
  if (s.publications_outdated > 0) lines.push(`${s.publications_outdated} publicações aguardando recálculo.`);
  if (s.cdc?.stale) {
    lines.push(`Carteira desatualizada: CDC ${s.cdc.connector_state}; ${s.cdc.pending_transactions} transações pendentes.`);
  }
  staleBox.hidden = lines.length === 0;
  staleBox.innerHTML = lines.map((l) => `<div>${esc(l)}</div>`).join("");
}

// ---------- ciclo ----------

function feedback(message = "", error = false): void {
  feedbackBox.hidden = !message;
  feedbackBox.dataset.state = error ? "error" : "loading";
  el("feedback-message").textContent = message;
  retryAnalysis.hidden = !error;
  document.querySelector(".analysis-view")?.setAttribute("aria-busy", String(Boolean(message) && !error));
}

async function refreshOperations(isCurrent: () => boolean, signal?: AbortSignal): Promise<void> {
  const tenant = tenantSel.value;
  if (!tenant) return;
  const generation = ++operationsGeneration;
  operationsBox.setAttribute("aria-busy", "true");
  try {
    const operations = await get<OperationsResponse>("/api/operations", { tenant }, signal);
    if (!isCurrent() || generation !== operationsGeneration || tenant !== tenantSel.value) return;
    operationsBox.innerHTML = operations ? renderOperations(operations, DEMO, demoPipelineExecuted) : "<h2>Operação da carteira</h2><p>Sem informações de processamento para esta carteira.</p>";
  } catch {
    if (!isCurrent() || generation !== operationsGeneration || tenant !== tenantSel.value) return;
    operationsBox.innerHTML = '<h2>Operação da carteira</h2><p role="status">Não foi possível consultar o processamento.</p><button type="button" data-retry-operations>Tentar novamente</button>';
  } finally {
    if (isCurrent() && generation === operationsGeneration) operationsBox.setAttribute("aria-busy", "false");
  }
}

async function refreshSources(isCurrent: () => boolean, signal: AbortSignal): Promise<void> {
  if (DEMO) return;
  try {
    const status = await get<ApiStatus>("/api/status", {}, signal);
    if (isCurrent() && status) renderStale(status);
  } catch {
    if (!isCurrent()) return;
    staleBox.hidden = false;
    staleBox.textContent = "O estado das fontes não pôde ser consultado. A publicação mantém seu horário original.";
  }
}

async function refresh(): Promise<void> {
  await refreshes.run(async (isCurrent, signal) => {
    if (!state.shown) feedback("Carregando a análise…");
    if (!tenantSel.options.length) {
      const tenants = (await get<Tenant[]>("/api/tenants", {}, signal)) ?? [];
      if (!isCurrent()) return;
      tenantSel.replaceChildren(...tenants.map((t) => new Option(t.name, t.tenant_id)));
      const preferred = initialParams.get("tenant");
      if (preferred && tenants.some((tenant) => tenant.tenant_id === preferred)) tenantSel.value = preferred;
    }
    let key = selectionKey();
    const { scope } = selection();
    void refreshSources(isCurrent, signal);
    void refreshOperations(isCurrent, signal);
    const events = await get<QuakeEvent[]>("/api/events", { scope }, signal);
    if (!isCurrent() || key !== selectionKey()) return;
    state.events = events ?? [];
    if (initialSelection) {
      initialSelection = false;
      const initialEvent = visibleEvents().find((event) => event.earthquake_uid === state.uid)
        ?? (scope === "REPLAY" ? visibleEvents().find((event) => event.preferred_id === "us7000tiur") : undefined)
        ?? visibleEvents()[0];
      if (initialEvent) {
        state.uid = initialEvent.earthquake_uid;
        key = selectionKey();
        map.focusEvent(initialEvent, false);
      }
      rememberSelection();
    }
    if (state.uid && !visibleEvents().some((e) => e.earthquake_uid === state.uid)) {
      state.uid = ""; // saiu da lista (outro escopo ou fundido em outro evento)
      changed();
      return;
    }
    renderEventOptions();
    drawEvents();
    if (state.uid) {
      const revisions =
        (await get<Revision[]>(`/api/events/${encodeURIComponent(state.uid)}/revisions`, { scope }, signal)) ?? [];
      if (!isCurrent() || key !== selectionKey()) return;
      state.revisions = revisions;
    } else {
      state.revisions = [];
    }
    renderEventPanel();
    await refreshPublication(key, isCurrent, signal);
    if (!isCurrent() || key !== selectionKey()) return;
    clock.textContent = DEMO ? `Snapshot · ${demoSnapshotAt ? utc(demoSnapshotAt) : "REPLAY"}` : `Atualizado às ${new Date().toLocaleTimeString("pt-BR")}`;
    clock.classList.remove("error");
    feedback();
    rememberSelection();
  }, (err) => {
    if (err instanceof PrivacyRecalculation) {
      sync.reset();
      paint(null, null, err.message);
      feedback(err.message, true);
    } else {
      if (!state.shown) paint(null, null, "Não foi possível carregar a análise.");
      feedback("Não foi possível atualizar a análise. Tente novamente.", true);
    }
    clock.textContent = "Falha na atualização";
    clock.classList.add("error");
  });
}

let timer = 0;
let tickGeneration = 0;
async function tick(): Promise<void> {
  const generation = ++tickGeneration;
  clearTimeout(timer);
  await refresh();
  if (!DEMO && generation === tickGeneration) timer = window.setTimeout(tick, REFRESH_MS);
}

function changed(): void {
  restoreInitialList = false;
  restoreInitialComparison = false;
  state.revisions = [];
  operationsGeneration++;
  operationsBox.innerHTML = '<h2>Operação da carteira</h2><p class="muted">Consultando o processamento…</p>';
  operationsBox.setAttribute("aria-busy", "true");
  staleBox.hidden = true;
  sync.reset();
  paint(null, null, "Carregando a publicação…");
  rememberSelection();
  void tick();
}

function selectEvent(uid: string): void {
  state.uid = uid;
  eventSel.value = uid;
  const e = state.events.find((x) => x.earthquake_uid === uid);
  if (e) map.focusEvent(e);
  drawEvents();
  changed();
}

scopeSel.addEventListener("change", () => {
  scopeNote.textContent = SCOPE_NOTE[scopeSel.value as Scope];
  scopeNote.className = `context-note ${scopeSel.value}`;
  scopeNote.hidden = scopeSel.value === "LIVE";
  changed();
});
tenantSel.addEventListener("change", changed);
modeSel.addEventListener("change", changed);
eventSel.addEventListener("change", () => selectEvent(eventSel.value));
function changePeriod(): void {
  const events = visibleEvents();
  renderEventOptions();
  selectEvent(events.some(event => event.earthquake_uid === state.uid) ? state.uid : events[0]?.earthquake_uid ?? "");
}
eventFrom.addEventListener("change", changePeriod);
eventTo.addEventListener("change", changePeriod);
document.querySelectorAll<HTMLButtonElement>("[data-event-days]").forEach(button => button.addEventListener("click", () => {
  const days = Number(button.dataset.eventDays);
  const period = days ? recentEventPeriod(state.events, days) : { from: "", to: "" };
  eventFrom.value = period.from;
  eventTo.value = period.to;
  changePeriod();
}));
focusButton.addEventListener("click", () => {
  const event = state.events.find((event) => event.earthquake_uid === state.uid);
  if (event) map.focusEvent(event);
});
fitButton.addEventListener("click", () => map.fitAssets(visibleAssets()));
retryAnalysis.addEventListener("click", () => { void start().catch(startupError); });
retryRegion.addEventListener("click", () => { retryRegion.hidden = true; void loadRegion(); });
drawAreaButton.addEventListener("click", () => map.startAreaSelection());
finishAreaButton.addEventListener("click", () => { map.finishAreaSelection(); });
undoAreaButton.addEventListener("click", () => map.undoAreaVertex());
cancelAreaButton.addEventListener("click", () => map.cancelAreaSelection());
clearAreaButton.addEventListener("click", () => map.clearAreaSelection());
compareButton.addEventListener("click", () => {
  if (compareButton.getAttribute("aria-pressed") === "true") { closeComparison(); rememberSelection(); }
  else void loadComparison();
});
comparisonBox.addEventListener("click", event => {
  const target = event.target instanceof Element ? event.target.closest<HTMLButtonElement>("button") : null;
  if (!target || target.disabled) return;
  if (target.hasAttribute("data-close-comparison")) { closeComparison(true); rememberSelection(); }
  else if (target.hasAttribute("data-retry-comparison")) void loadComparison(comparisonOffset);
  else if (target.hasAttribute("data-focus-comparison")) { map.fitComparison(); revealPanel(el("map-wrap"), false); el("map").focus({ preventScroll: true }); }
  else if (state.comparison && target.dataset.comparisonPage) {
    const offset = state.comparison.offset + (target.dataset.comparisonPage === "next" ? state.comparison.limit : -state.comparison.limit);
    void loadComparison(Math.max(0, offset));
  }
});
comparisonBox.addEventListener("keydown", event => {
  if (event.key === "Escape") { closeComparison(true); rememberSelection(); }
});
diffBox.addEventListener("click", event => {
  if (event.target instanceof Element && event.target.closest("[data-open-comparison]")) void loadComparison();
});
operationsBox.addEventListener("click", event => {
  if (event.target instanceof Element && event.target.closest("[data-retry-operations]")) void refreshOperations(() => true);
});
const mobileLayout = matchMedia("(max-width: 760px)");
const mapActions = document.querySelector<HTMLElement>(".map-actions")!;
function responsiveLayout(): void {
  filtersPanel.open = !mobileLayout.matches;
  if (mobileLayout.matches) el("map-wrap").insertBefore(mapActions, document.querySelector(".map-help"));
  else document.querySelector(".map-heading")!.append(mapActions);
}
responsiveLayout();
mobileLayout.addEventListener("change", responsiveLayout);
if (location.hash === "#operations") el<HTMLDetailsElement>("operations-disclosure").open = true;
document.querySelectorAll<HTMLAnchorElement>(".section-nav a").forEach(link => link.addEventListener("click", () => {
  if (link.getAttribute("href") === "#operations") el<HTMLDetailsElement>("operations-disclosure").open = true;
  document.querySelectorAll(".section-nav a").forEach(item => item.removeAttribute("aria-current"));
  link.setAttribute("aria-current", "location");
}));

scopeNote.textContent = SCOPE_NOTE[scopeSel.value as Scope];
scopeNote.className = `context-note ${scopeSel.value}`;
scopeNote.hidden = scopeSel.value === "LIVE";
paint(null, null, "Escolha um evento.");
async function start(): Promise<void> {
  feedback("Carregando a análise…");
  if (DEMO) {
    const meta = await get<DemoMetadata & { pipeline_executed?: boolean }>("/api/demo");
    demoSnapshotAt = meta?.snapshot_at ?? "";
    demoPipelineExecuted = meta?.pipeline_executed !== false;
    if (meta?.default_selection) {
      if (!initialParams.has("tenant")) initialParams.set("tenant", meta.default_selection.tenant);
      if (!initialParams.has("event")) state.uid = meta.default_selection.event;
      if (!initialParams.has("mode")) modeSel.value = meta.default_selection.mode;
    }
    scopeSel.value = "REPLAY";
    scopeSel.disabled = true;
    document.querySelector<HTMLElement>(".scope-filter")!.hidden = true;
    if (meta?.available_modes?.length) {
      for (const option of Array.from(modeSel.options)) if (!meta.available_modes.includes(option.value as Mode)) option.remove();
      document.querySelector<HTMLElement>(".mode-filter")!.hidden = modeSel.options.length === 1;
    }
    document.querySelector<HTMLElement>(".filters")!.dataset.compact = String(modeSel.options.length === 1);
    el("dataset-label").textContent = "REPLAY · Carteira fictícia";
    document.querySelector(".dataset-details p")!.textContent = "Capturas do USGS reprocessadas com seu horário original. Carteira fictícia; snapshot sem coleta ao vivo.";
    scopeNote.hidden = true;
  }
  await tick();
}
function startupError(error: unknown): void {
  clock.textContent = "Falha ao abrir a análise";
  clock.classList.add("error");
  feedback(error instanceof PrivacyRecalculation ? error.message : "Não foi possível abrir a análise. Tente novamente.", true);
}
void start().catch(startupError);
