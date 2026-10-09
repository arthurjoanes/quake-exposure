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
import { QuakeMap } from "./map-view";
import { formatMinor } from "./money";
import { PublicationSync } from "./publication";
import { LatestRefresh } from "./refresh";

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
const initialParams = new URLSearchParams(location.search);
if (["LIVE", "REPLAY", "FAULT"].includes(initialParams.get("scope") ?? "")) scopeSel.value = initialParams.get("scope")!;
if (["RADIUS_TRIAGE", "SHAKEMAP_MMI"].includes(initialParams.get("mode") ?? "")) modeSel.value = initialParams.get("mode")!;

const state = {
  events: [] as QuakeEvent[],
  revisions: [] as Revision[],
  uid: initialParams.get("event") ?? "",
  shown: null as CurrentPublication | null, // publicação cujos ativos, resumo e diff estão na tela
};
const sync = new PublicationSync();
const refreshes = new LatestRefresh();
let eventSignature = "";
let initialSelection = true;

// ---------- mapa ----------

const map = new QuakeMap("map", {
  onSelect: selectEvent,
  onBaseStatus: (message) => { el("map-base-status").textContent = message; },
  onDensityChange: ({ assets, groups, clustered }) => {
    fitButton.disabled = assets === 0;
    el("map-density").textContent = assets === 0
      ? "Selecione um terremoto para analisar a carteira."
      : clustered
        ? `${assets.toLocaleString("pt-BR")} ativos em ${groups.toLocaleString("pt-BR")} pontos e grupos. Amplie para separar.`
        : `${assets.toLocaleString("pt-BR")} ativos. Selecione um ponto para ver os detalhes.`;
  },
});
const assetList = new AssetList(assetsBox, (asset) => {
  map.focusAsset(asset);
  el("map-wrap").scrollIntoView({ behavior: matchMedia("(prefers-reduced-motion: reduce)").matches ? "instant" : "smooth", block: "start" });
});

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
const selectionKey = (): string => Object.values(selection()).join("|");
function rememberSelection(): void {
  const s = selection();
  const params = new URLSearchParams({ scope: s.scope, tenant: s.tenant, mode: s.mode });
  if (s.uid) params.set("event", s.uid);
  history.replaceState(null, "", `${location.pathname}?${params}`);
}

// ---------- eventos ----------

function renderEventOptions(): void {
  const opts: [string, string][] = [
    ["", state.events.length ? "escolha um evento" : "nenhum evento neste escopo"],
    ...state.events.map((e): [string, string] => [e.earthquake_uid, eventLabel(e)]),
  ];
  const signature = opts.map((o) => o.join("\t")).join("\n");
  if (signature !== eventSignature) {
    eventSignature = signature;
    eventSel.replaceChildren(...opts.map(([value, text]) => new Option(text, value)));
  }
  eventSel.value = state.uid;
}

function drawEvents(): void {
  map.setEvents(state.events, state.uid);
  focusButton.disabled = !state.events.some((event) => event.earthquake_uid === state.uid);
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
    <h2>Evento ${scopeBadge(e.stream_scope)}</h2>
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
    </table></div>`;
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
    <div class="summary-heading"><h2>Exposição ${scopeBadge(r.input_vector.stream_scope)}</h2><span class="result-status">${STATUS_TEXT[r.status]}</span></div>
    <p class="summary-event"><strong>${esc(event?.preferred_id ?? "Terremoto")}</strong>${esc(event ? utc(event.event_time) : "")}</p>
    <dl class="summary-counts"><div class="classified"><dt>Classificados</dt><dd>${count(r.qualifying_count)}</dd></div><div><dt>Elegíveis</dt><dd>${count(r.eligible_count)}</dd></div></dl>
    <dl class="currency-values"><div><dt>Valor classificado em BRL</dt><dd>${totals ? formatMinor(totals.BRL, "BRL") : "–"}</dd></div><div><dt>Valor classificado em USD</dt><dd>${totals ? formatMinor(totals.USD, "USD") : "–"}</dd></div></dl>
    <p class="muted">${MODE_NOTE[r.input_vector.mode]}</p>
    <details class="publication-details"><summary>Detalhes da publicação</summary><dl>
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
    <h2>Antes e depois</h2>
    <dl>
      <dt>Anterior</dt><dd>${diff.previous_publication_id ? shortId(diff.previous_publication_id) : "nenhuma"}</dd>
      <dt>Mudou por</dt><dd>${esc(diff.causes.join(", ") || "nada no vetor de entrada")}</dd>
    </dl>
    ${diffList("Entraram", diff.entered)}
    ${diffList("Saíram", diff.left)}`;

  return { summary, diff: diffHtml, assets: assets.assets, charts: renderCharts(pub, assets, diff) };
}

function paint(pub: CurrentPublication | null, view: View | null, message = ""): void {
  state.shown = pub;
  if (view) {
    const event = state.events.find((event) => event.earthquake_uid === state.uid);
    if (pub) map.setAssets(view.assets, pub.publication_id, pub.result.input_vector.mode, event);
    summaryBox.innerHTML = view.summary;
    diffBox.innerHTML = view.diff;
    chartsBox.innerHTML = view.charts;
    assetList.setAssets(view.assets);
  } else {
    map.clearAssets();
    assetList.clear();
    summaryBox.innerHTML = `<h2>Exposição</h2><p class="muted">${esc(message)}</p><p class="muted">${MODE_NOTE[modeSel.value as Mode]}</p>`;
    diffBox.innerHTML = "";
    chartsBox.innerHTML = `<div class="chart-empty"><p>${esc(message)} Os gráficos acompanham a publicação selecionada.</p></div>`;
  }
  renderEventPanel();
}

async function refreshPublication(key: string, isCurrent: () => boolean): Promise<void> {
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
  });
  if (!isCurrent() || key !== selectionKey()) return;
  if (!pub) {
    sync.seen(null);
    paint(null, null, "Sem publicação para este evento, tenant e modo.");
    return;
  }
  if (!sync.seen(pub.publication_id)) return;
  const id = encodeURIComponent(pub.publication_id);
  const [assets, diff] = await Promise.all([
    get<AssetsResponse>(`/api/publications/${id}/assets`, { tenant }),
    get<DiffResponse>(`/api/publications/${id}/diff`, { tenant }),
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

async function refresh(): Promise<void> {
  await refreshes.run(async (isCurrent) => {
    if (!tenantSel.options.length) {
      const tenants = (await get<Tenant[]>("/api/tenants")) ?? [];
      if (!isCurrent()) return;
      tenantSel.replaceChildren(...tenants.map((t) => new Option(`${t.name} (${t.tenant_id})`, t.tenant_id)));
      const preferred = initialParams.get("tenant");
      if (preferred && tenants.some((tenant) => tenant.tenant_id === preferred)) tenantSel.value = preferred;
    }
    let key = selectionKey();
    const { scope } = selection();
    const [status, events] = await Promise.all([
      get<ApiStatus>("/api/status"),
      get<QuakeEvent[]>("/api/events", { scope }),
    ]);
    if (!isCurrent() || key !== selectionKey()) return;
    if (status) renderStale(status);
    state.events = events ?? [];
    if (initialSelection) {
      initialSelection = false;
      const initialEvent = state.events.find((event) => event.earthquake_uid === state.uid)
        ?? (scope === "REPLAY" ? state.events.find((event) => event.preferred_id === "us7000tiur") : undefined);
      if (initialEvent) {
        state.uid = initialEvent.earthquake_uid;
        key = selectionKey();
        map.focusEvent(initialEvent);
      }
      rememberSelection();
    }
    if (state.uid && !state.events.some((e) => e.earthquake_uid === state.uid)) {
      state.uid = ""; // saiu da lista (outro escopo ou fundido em outro evento)
      changed();
      return;
    }
    renderEventOptions();
    drawEvents();
    if (state.uid) {
      const revisions =
        (await get<Revision[]>(`/api/events/${encodeURIComponent(state.uid)}/revisions`, { scope })) ?? [];
      if (!isCurrent() || key !== selectionKey()) return;
      state.revisions = revisions;
    } else {
      state.revisions = [];
    }
    renderEventPanel();
    await refreshPublication(key, isCurrent);
    if (!isCurrent() || key !== selectionKey()) return;
    clock.textContent = `atualizado às ${new Date().toLocaleTimeString("pt-BR")}`;
    clock.classList.remove("error");
  }, (err) => {
    if (err instanceof PrivacyRecalculation) {
      sync.reset();
      paint(null, null, err.message);
    }
    clock.textContent = `erro: ${err instanceof Error ? err.message : String(err)}`;
    clock.classList.add("error");
  });
}

let timer = 0;
async function tick(): Promise<void> {
  await refresh();
  clearTimeout(timer);
  timer = window.setTimeout(tick, REFRESH_MS);
}

function changed(): void {
  rememberSelection();
  sync.reset();
  paint(null, null, "Carregando.");
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
  scopeNote.className = `strip ${scopeSel.value}`;
  scopeNote.hidden = scopeSel.value === "LIVE";
  changed();
});
tenantSel.addEventListener("change", changed);
modeSel.addEventListener("change", changed);
eventSel.addEventListener("change", () => selectEvent(eventSel.value));
focusButton.addEventListener("click", () => {
  const event = state.events.find((event) => event.earthquake_uid === state.uid);
  if (event) map.focusEvent(event);
});
fitButton.addEventListener("click", () => map.fitAssets());

scopeNote.textContent = SCOPE_NOTE[scopeSel.value as Scope];
scopeNote.className = `strip ${scopeSel.value}`;
scopeNote.hidden = scopeSel.value === "LIVE";
paint(null, null, "Escolha um evento.");
void tick();
