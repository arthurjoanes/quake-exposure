import "leaflet/dist/leaflet.css";
import "./style.css";
import * as L from "leaflet";
import { feature } from "topojson-client";
import land from "world-atlas/land-110m.json";
import {
  type ApiStatus,
  type Asset,
  type AssetsResponse,
  type CurrentPublication,
  type DiffItem,
  type DiffResponse,
  get,
  type Mode,
  type QuakeEvent,
  type Revision,
  type ResultStatus,
  type Scope,
  type Tenant,
} from "./api";
import { planar } from "./basemap";
import { formatMinor } from "./money";
import { PublicationSync } from "./publication";

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

const COLOR = { qualifying: "#c0392b", eligible: "#2c6fa6", other: "#9a9a9a", event: "#6a3d9a" };

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

const state = {
  events: [] as QuakeEvent[],
  revisions: [] as Revision[],
  uid: "",
  shown: null as CurrentPublication | null, // publicação cujos ativos, resumo e diff estão na tela
};
const sync = new PublicationSync();
let eventSignature = "";

// ---------- mapa ----------

const map = L.map("map", { minZoom: 1, maxZoom: 12 }).setView([15, 0], 2);
map.createPane("land").style.zIndex = "200";
map.createPane("assets").style.zIndex = "350";
const landFc = feature(land, land.objects.land);
const landPolygons = ("features" in landFc ? landFc.features : [landFc]).flatMap((f) =>
  f.geometry.type === "MultiPolygon" ? f.geometry.coordinates : f.geometry.type === "Polygon" ? [f.geometry.coordinates] : [],
);
const landGeometry = { type: "MultiPolygon" as const, coordinates: planar(landPolygons) };
L.geoJSON(landGeometry, {
  pane: "land",
  interactive: false,
  attribution: "Natural Earth",
  style: { color: "#a9b19c", weight: 0.6, fillColor: "#eceee4", fillOpacity: 1 },
}).addTo(map);
const assetRenderer = L.canvas({ pane: "assets" });
const assetLayer = L.layerGroup().addTo(map);
const eventLayer = L.layerGroup().addTo(map);

// ---------- formatação ----------

const esc = (s: string): string => s.replace(/[&<>"']/g, (c) => `&#${c.charCodeAt(0)};`);
const num = (x: number | null, digits: number): string =>
  x === null ? "–" : x.toFixed(digits).replace(".", ",");
const utc = (iso: string | null): string =>
  iso === null ? "–" : `${iso.slice(0, 19).replace("T", " ")} UTC`;
const shortId = (id: string): string => `<code title="${esc(id)}">${esc(id.slice(0, 12))}</code>`;
const count = (n: number | null): string => (n === null ? "–" : String(n));
const mmiChange = (i: DiffItem): string =>
  i.mmi_before === null && i.mmi_after === null ? "" : `MMI ${num(i.mmi_before, 2)} → ${num(i.mmi_after, 2)}`;
const eventLabel = (e: QuakeEvent): string =>
  `${utc(e.event_time)} · M ${num(e.mag, 1)} · ${e.preferred_id}` +
  (e.lifecycle === "RETRACTED" ? " · retirado" : "");

const rank = (a: Asset): number => (a.qualifying ? 0 : a.eligible ? 1 : 2);
const situation = ["classificado", "elegível", "não elegível"];
const rankColor = [COLOR.qualifying, COLOR.eligible, COLOR.other];

const selection = () => ({
  scope: scopeSel.value as Scope,
  tenant: tenantSel.value,
  uid: state.uid,
  mode: modeSel.value as Mode,
});
const selectionKey = (): string => Object.values(selection()).join("|");

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
  eventLayer.clearLayers();
  for (const e of state.events) {
    const selected = e.earthquake_uid === state.uid;
    const retracted = e.lifecycle === "RETRACTED";
    L.circleMarker([e.lat, e.lon], {
      radius: 4 + Math.max(0, e.mag ?? 0) * 1.5,
      color: selected ? "#000" : COLOR.event,
      weight: selected ? 3 : 1.5,
      fillColor: COLOR.event,
      fillOpacity: retracted ? 0 : 0.25,
      dashArray: retracted ? "3 3" : undefined,
    })
      .bindTooltip(esc(eventLabel(e)))
      .on("click", () => selectEvent(e.earthquake_uid))
      .addTo(eventLayer);
  }
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
      <dt>Lifecycle</dt><dd>${e.lifecycle === "RETRACTED" ? "RETRACTED (retirado pelo USGS)" : "ACTIVE"}</dd>
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
  assets: string;
  markers: L.CircleMarker[];
}

/** Monta tudo antes de tocar na tela: se algum valor for inválido, nada muda. */
function build(pub: CurrentPublication, assets: AssetsResponse, diff: DiffResponse): View {
  const r = pub.result;
  const totals = r.qualifying_value_minor;
  const summary = `
    <h2>Publicação ${scopeBadge(r.input_vector.stream_scope)}</h2>
    <dl>
      <dt>Status</dt><dd>${r.status} (${STATUS_TEXT[r.status]})</dd>
      <dt>Elegíveis</dt><dd>${count(r.eligible_count)}</dd>
      <dt>Classificados</dt><dd>${count(r.qualifying_count)}</dd>
      <dt>Valor classificado BRL</dt><dd class="money">${totals ? formatMinor(totals.BRL, "BRL") : "–"}</dd>
      <dt>Valor classificado USD</dt><dd class="money">${totals ? formatMinor(totals.USD, "USD") : "–"}</dd>
      <dt>Mapa usado</dt><dd>${r.input_vector.product_revision_id === "NONE" ? "nenhum (raio)" : `<code>${esc(r.input_vector.product_revision_id)}</code>`}</dd>
      <dt>Geração</dt><dd>${pub.generation}</dd>
      <dt>Calculada em</dt><dd>${esc(utc(r.calculated_at))}</dd>
      <dt>Publicada em</dt><dd>${esc(utc(r.published_at))}</dd>
      <dt>Publicação</dt><dd>${shortId(pub.publication_id)}</dd>
    </dl>
    <p class="muted">${MODE_NOTE[r.input_vector.mode]}</p>
    <p class="muted">Limitações: ${r.limitations.map((l) => `<code>${esc(l)}</code>`).join(" ") || "–"}</p>`;

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

  const sorted = [...assets.assets].sort((a, b) => rank(a) - rank(b));
  const assetRows = sorted
    .map((a) => {
      const k = rank(a);
      return (
        `<tr class="r${k}"><td>${esc(a.asset_id)}</td><td class="money">${formatMinor(a.insured_value_minor, a.currency)}</td>` +
        `<td>${num(a.mmi, 2)}</td><td>${situation[k]}</td></tr>`
      );
    })
    .join("");
  const nQualifying = sorted.filter((a) => a.qualifying).length;
  const assetsHtml = `
    <h2>Ativos (${sorted.length}, ${nQualifying} classificados)</h2>
    <div class="scroll"><table>
      <thead><tr><th>Ativo</th><th>Valor segurado</th><th>MMI</th><th>Situação</th></tr></thead>
      <tbody>${assetRows}</tbody>
    </table></div>`;

  // não classificados primeiro: os classificados ficam por cima no canvas
  const markers = [...sorted].reverse().map((a) => {
    const k = rank(a);
    const tip = `${a.asset_id} · ${formatMinor(a.insured_value_minor, a.currency)}` +
      (a.mmi === null ? "" : ` · MMI ${num(a.mmi, 2)}`) + ` · ${situation[k]}`;
    return L.circleMarker([a.lat, a.lon], {
      renderer: assetRenderer,
      radius: k === 0 ? 4 : 3,
      weight: 0,
      fillColor: rankColor[k],
      fillOpacity: 0.85,
    }).bindTooltip(esc(tip));
  });

  return { summary, diff: diffHtml, assets: assetsHtml, markers };
}

function paint(pub: CurrentPublication | null, view: View | null, message = ""): void {
  state.shown = pub;
  assetLayer.clearLayers();
  if (view) {
    for (const m of view.markers) m.addTo(assetLayer);
    summaryBox.innerHTML = view.summary;
    diffBox.innerHTML = view.diff;
    assetsBox.innerHTML = view.assets;
  } else {
    summaryBox.innerHTML = `<h2>Publicação</h2><p class="muted">${esc(message)}</p><p class="muted">${MODE_NOTE[modeSel.value as Mode]}</p>`;
    diffBox.innerHTML = "";
    assetsBox.innerHTML = "";
  }
  renderEventPanel();
}

async function refreshPublication(key: string): Promise<void> {
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
  if (key !== selectionKey()) return;
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
  if (key !== selectionKey() || !assets || !diff) return;
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
  staleBox.hidden = lines.length === 0;
  staleBox.innerHTML = lines.map((l) => `<div>${esc(l)}</div>`).join("");
}

// ---------- ciclo ----------

async function refresh(): Promise<void> {
  try {
    if (!tenantSel.options.length) {
      const tenants = (await get<Tenant[]>("/api/tenants")) ?? [];
      tenantSel.replaceChildren(...tenants.map((t) => new Option(`${t.name} (${t.tenant_id})`, t.tenant_id)));
    }
    const key = selectionKey();
    const { scope } = selection();
    const [status, events] = await Promise.all([
      get<ApiStatus>("/api/status"),
      get<QuakeEvent[]>("/api/events", { scope }),
    ]);
    if (status) renderStale(status);
    if (key !== selectionKey()) return;
    state.events = events ?? [];
    if (state.uid && !state.events.some((e) => e.earthquake_uid === state.uid)) {
      state.uid = ""; // saiu da lista (outro escopo ou fundido em outro evento)
      changed();
      return;
    }
    renderEventOptions();
    drawEvents();
    if (state.uid) {
      state.revisions =
        (await get<Revision[]>(`/api/events/${encodeURIComponent(state.uid)}/revisions`, { scope })) ?? [];
      if (key !== selectionKey()) return;
    } else {
      state.revisions = [];
    }
    renderEventPanel();
    await refreshPublication(key);
    clock.textContent = `atualizado às ${new Date().toLocaleTimeString("pt-BR")}`;
    clock.classList.remove("error");
  } catch (err) {
    clock.textContent = `erro: ${err instanceof Error ? err.message : String(err)}`;
    clock.classList.add("error");
  }
}

let timer = 0;
async function tick(): Promise<void> {
  await refresh();
  clearTimeout(timer);
  timer = window.setTimeout(tick, REFRESH_MS);
}

function changed(): void {
  sync.reset();
  paint(null, null, "Carregando.");
  void tick();
}

function selectEvent(uid: string): void {
  state.uid = uid;
  eventSel.value = uid;
  const e = state.events.find((x) => x.earthquake_uid === uid);
  if (e) map.setView([e.lat, e.lon], Math.max(map.getZoom(), 6));
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

paint(null, null, "Escolha um evento.");
void tick();
