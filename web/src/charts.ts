import type { Asset, AssetsResponse, CurrentPublication, DiffResponse, ResultStatus } from "./api";
import { formatMinor } from "./money";

const COLOR = { qualifying: "#bf4b35", eligible: "#087f8c", other: "#a7b8c0", comparison: "#426b61" };
const CURRENCIES = ["BRL", "USD"] as const;
type Currency = (typeof CURRENCIES)[number];

const count = (value: number): string => value.toLocaleString("pt-BR");
const escape = (value: string): string => value.replace(/[&<>"']/g, (c) => `&#${c.charCodeAt(0)};`);

interface MoneySeries {
  eligible: bigint;
  qualifying: bigint;
}

export interface ChartData {
  qualifying: number;
  eligibleOnly: number;
  other: number;
  mmi: { label: string; count: number; qualifying: boolean }[];
  withoutMmi: number;
  money: Record<Currency, MoneySeries>;
}

/** Agrega só os fatos desta publicação; valores monetários permanecem inteiros. */
export function summarizeAssets(assets: readonly Asset[]): ChartData {
  const data: ChartData = {
    qualifying: 0,
    eligibleOnly: 0,
    other: 0,
    mmi: [
      { label: "< 4", count: 0, qualifying: false },
      { label: "4 a < 5", count: 0, qualifying: false },
      { label: "5 a < 6", count: 0, qualifying: false },
      { label: "6 a < 7", count: 0, qualifying: true },
      { label: "7 a < 8", count: 0, qualifying: true },
      { label: "≥ 8", count: 0, qualifying: true },
    ],
    withoutMmi: 0,
    money: { BRL: { eligible: 0n, qualifying: 0n }, USD: { eligible: 0n, qualifying: 0n } },
  };
  for (const asset of assets) {
    if (asset.qualifying && !asset.eligible) throw new RangeError("ativo classificado não elegível");
    if (asset.qualifying) data.qualifying++;
    else if (asset.eligible) data.eligibleOnly++;
    else data.other++;

    // Também valida moeda e representação inteira antes de aceitar o valor.
    formatMinor(asset.insured_value_minor, asset.currency);
    const money = data.money[asset.currency as Currency];
    const minor = BigInt(asset.insured_value_minor);
    if (asset.eligible) money.eligible += minor;
    if (asset.qualifying) money.qualifying += minor;

    if (!asset.eligible || asset.mmi === null || !Number.isFinite(asset.mmi)) {
      data.withoutMmi++;
      continue;
    }
    const index = asset.mmi < 4 ? 0 : asset.mmi < 5 ? 1 : asset.mmi < 6 ? 2 : asset.mmi < 7 ? 3 : asset.mmi < 8 ? 4 : 5;
    data.mmi[index].count++;
  }
  return data;
}

/** Somente a razão normalizada passa a Number, depois da divisão inteira. */
function percentage(part: bigint, total: bigint): { width: number; text: string } {
  if (total === 0n) return { width: 0, text: "sem valor elegível" };
  const basisPoints = (part * 10_000n) / total;
  const width = Number(basisPoints) / 100;
  const text = part > 0n && basisPoints === 0n
    ? "menos de 0,01%"
    : `${width.toLocaleString("pt-BR", { maximumFractionDigits: 2 })}%`;
  return { width, text };
}

function panel(title: string, body: string, caption: string): string {
  return `<article class="chart-panel"><h3 class="chart-heading">${title}</h3>${body}<details class="chart-details"><summary>Sobre o gráfico</summary><p class="chart-caption">${caption}</p></details></article>`;
}

function composition(data: ChartData): string {
  const series = [
    { label: "Classificados", count: data.qualifying, color: COLOR.qualifying },
    { label: "Elegíveis não classificados", count: data.eligibleOnly, color: COLOR.eligible },
    { label: "Não elegíveis", count: data.other, color: COLOR.other },
  ];
  const total = series.reduce((sum, entry) => sum + entry.count, 0);
  let left = 0;
  const bars = series.map((entry) => {
    const width = total === 0 ? 0 : (entry.count / total) * 320;
    const rect = `<rect class="chart-segment" x="${left}" y="0" width="${width}" height="24" fill="${entry.color}"><title>${entry.label}: ${count(entry.count)}</title></rect>`;
    left += width;
    return rect;
  }).join("");
  const description = series.map((entry) => `${entry.label}: ${count(entry.count)}`).join("; ");
  const legend = series.map((entry) => `<li><span class="chart-key" style="background:${entry.color}"></span><span>${entry.label}</span><strong>${count(entry.count)}</strong></li>`).join("");
  return panel(
    "Composição da carteira",
    `<div class="chart-number">${count(data.qualifying)}<span class="chart-kicker"> ${data.qualifying === 1 ? "classificado" : "classificados"} de ${count(total)} ${total === 1 ? "ativo" : "ativos"}</span></div>` +
    `<svg viewBox="0 0 320 24" width="100%" height="24" role="img" aria-label="${description}"><rect width="320" height="24" fill="#edf1f3"/>${bars}</svg>` +
    `<ul class="chart-legend">${legend}</ul>`,
    "Classificados fazem parte dos elegíveis. Categorias sem sobreposição; ativos vigentes no horário do evento.",
  );
}

function mmiHistogram(data: ChartData): string {
  const maximum = Math.max(1, ...data.mmi.map((entry) => entry.count));
  const measured = data.mmi.reduce((sum, entry) => sum + entry.count, 0);
  const total = measured + data.withoutMmi;
  const coverage = total ? `${(measured / total * 100).toLocaleString("pt-BR", { maximumFractionDigits: 1 })}%` : "sem ativos";
  const bars = data.mmi.map((entry, index) => {
    const x = 10 + index * 53;
    const height = (entry.count / maximum) * 92;
    return `<g><rect class="chart-column" x="${x}" y="${120 - height}" width="35" height="${height}" rx="3" fill="${entry.qualifying ? COLOR.qualifying : COLOR.eligible}"/>` +
      `<text x="${x + 17.5}" y="${112 - height}" text-anchor="middle" fill="#33434c" font-size="11">${count(entry.count)}</text>` +
      `<text x="${x + 17.5}" y="140" text-anchor="middle" fill="#526773" font-size="10">${escape(entry.label)}</text></g>`;
  }).join("");
  const description = data.mmi.map((entry) => `MMI ${entry.label}: ${count(entry.count)} ativos`).join("; ");
  return panel(
    "Intensidade nos ativos",
    `<div class="chart-number">${coverage}<span class="chart-kicker"> da carteira com MMI amostrado</span></div>` +
    `<p class="chart-kicker">${count(measured)} com MMI · ${count(data.withoutMmi)} sem amostra elegível</p>` +
    `<svg viewBox="0 0 330 150" width="100%" role="img" aria-label="${escape(description)}"><line x1="4" x2="325" y1="120" y2="120" stroke="#dce4e8"/>${bars}</svg>`,
    `${count(measured)} ativos com MMI amostrado; ${count(data.withoutMmi)} sem amostra elegível. MMI ≥ 6,0 classifica. Ausência de MMI não é zero.`,
  );
}

function moneyChart(data: ChartData): string {
  const rows = CURRENCIES.map((currency) => {
    const values = data.money[currency];
    const ratio = percentage(values.qualifying, values.eligible);
    const qualifying = formatMinor(values.qualifying.toString(), currency);
    const eligible = formatMinor(values.eligible.toString(), currency);
    return `<div class="chart-money-row"><div class="chart-money-label"><strong>${currency}</strong><span>${ratio.text}</span></div>` +
      `<div class="chart-track" role="img" aria-label="${qualifying} classificados de ${eligible} elegíveis; ${ratio.text}"><span style="width:${ratio.width}%;background:${COLOR.qualifying}"></span></div>` +
      `<div class="chart-value">${qualifying}</div><div class="chart-caption">de ${eligible} elegíveis</div></div>`;
  }).join("");
  return panel("Valor segurado classificado", rows, "Cada barra usa o valor elegível da própria moeda. BRL e USD permanecem separados.");
}

function changes(diff: DiffResponse): string {
  if (diff.previous_publication_id === null) {
    return panel("Mudança de classificação", `<p class="chart-empty">Primeira publicação disponível.</p>`, "Sem publicação anterior para comparar. As listas mostram os ativos desta publicação.");
  }
  const series = [
    { label: "Entraram", count: diff.entered.length, color: COLOR.qualifying },
    { label: "Saíram", count: diff.left.length, color: COLOR.comparison },
  ];
  const maximum = Math.max(1, ...series.map((entry) => entry.count));
  const bars = series.map((entry, index) => {
    const y = 16 + index * 45;
    return `<text x="0" y="${y + 17}" font-size="12" fill="#526773">${entry.label}</text>` +
      `<rect x="66" y="${y}" width="220" height="24" rx="3" fill="#edf1f3"/>` +
      `<rect class="chart-bar" x="66" y="${y}" width="${(entry.count / maximum) * 220}" height="24" rx="3" fill="${entry.color}"/>` +
      `<text x="326" y="${y + 17}" text-anchor="end" font-size="12" fill="#33434c">${count(entry.count)}</text>`;
  }).join("");
  const description = `${count(diff.entered.length)} ativos entraram; ${count(diff.left.length)} ativos saíram da classificação`;
  return panel(
    "Mudança de classificação",
    `<svg viewBox="0 0 330 105" width="100%" role="img" aria-label="${description}">${bars}</svg>`,
    "Ativos que entraram ou saíram da classificação desde a publicação anterior. Detalhes nas listas abaixo.",
  );
}

const UNAVAILABLE: Record<Exclude<ResultStatus, "COMPLETE">, string> = {
  NO_MAP: "Sem ShakeMap para calcular a intensidade nos ativos.",
  UNSUPPORTED_GRID: "O grid disponível não é suportado para este cálculo.",
  RETRACTED: "Evento retirado. Os gráficos de exposição não se aplicam.",
  REDACTED_BY_ERASURE: "Publicação removida após exclusão. Aguarde o recálculo.",
};

/** HTML puro: identidade única para gráficos, valores, mapa e listas. */
export function renderCharts(pub: CurrentPublication, assets: AssetsResponse, diff: DiffResponse): string {
  if (pub.publication_id !== assets.publication_id || pub.publication_id !== diff.publication_id) {
    throw new RangeError("gráficos com publicações diferentes");
  }
  const result = pub.result;
  if (result.status !== "COMPLETE") {
    return `<p class="chart-empty">${UNAVAILABLE[result.status]} Contagens e valores indisponíveis.</p>`;
  }
  if (result.eligible_count === null || result.qualifying_count === null || result.qualifying_value_minor === null) {
    return `<p class="chart-empty">Contagens e valores ainda indisponíveis para esta publicação.</p>`;
  }
  const data = summarizeAssets(assets.assets);
  if (data.qualifying !== result.qualifying_count || data.eligibleOnly + data.qualifying !== result.eligible_count) {
    throw new RangeError("contagens dos gráficos divergem da publicação");
  }
  for (const currency of CURRENCIES) {
    if (data.money[currency].qualifying !== BigInt(result.qualifying_value_minor[currency])) {
      throw new RangeError("valores dos gráficos divergem da publicação");
    }
  }
  return `<div class="chart-grid" data-publication="${escape(pub.publication_id)}">` +
    composition(data) + moneyChart(data) +
    (result.input_vector.mode === "SHAKEMAP_MMI" ? mmiHistogram(data) : "") + changes(diff) + "</div>";
}
