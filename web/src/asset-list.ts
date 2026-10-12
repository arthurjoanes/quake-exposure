import type { Asset } from "./api";
import { formatMinor } from "./money";

export type AssetStatusFilter = "all" | "qualifying" | "eligible" | "ineligible";
export interface AssetFilters {
  search: string;
  status: AssetStatusFilter;
  currency: "all" | "BRL" | "USD";
}
export interface AssetListState {
  filters: AssetFilters;
  page: number;
  pageSize: 50 | 100;
}
export interface AssetPage {
  assets: Asset[];
  page: number;
  pages: number;
  total: number;
  first: number;
  last: number;
}
export type AssetListContext = "Carteira inteira" | "Área selecionada";

const STATUS = ["classificado", "elegível", "não elegível"];
const rank = (asset: Asset): number => asset.qualifying ? 0 : asset.eligible ? 1 : 2;
const esc = (text: string): string => text.replace(/[&<>"']/g, (c) => `&#${c.charCodeAt(0)};`);
const count = (value: number): string => value.toLocaleString("pt-BR");
const defaults = (): AssetFilters => ({ search: "", status: "all", currency: "all" });
let instanceCount = 0;

/** Estado recebido de navegação externa passa pelas mesmas opções dos controles. */
export function normalizeAssetListState(value: unknown): AssetListState {
  const record = (item: unknown): Record<string, unknown> =>
    typeof item === "object" && item !== null && !Array.isArray(item) ? item as Record<string, unknown> : {};
  const state = record(value);
  const filters = record(state.filters);
  const status = filters.status;
  const currency = filters.currency;
  return {
    filters: {
      search: typeof filters.search === "string" ? filters.search : "",
      status: status === "qualifying" || status === "eligible" || status === "ineligible" ? status : "all",
      currency: currency === "BRL" || currency === "USD" ? currency : "all",
    },
    page: typeof state.page === "number" && Number.isFinite(state.page) &&
      Number.isSafeInteger(Math.trunc(state.page)) ? Math.max(0, Math.trunc(state.page)) : 0,
    pageSize: state.pageSize === 100 ? 100 : 50,
  };
}

const hasFilters = (filters: AssetFilters): boolean =>
  filters.search.trim() !== "" || filters.status !== "all" || filters.currency !== "all";

const sameAssets = (left: readonly Asset[], right: readonly Asset[]): boolean =>
  left.length === right.length && left.every((asset, index) => {
    const other = right[index];
    return other !== undefined && asset.asset_id === other.asset_id && asset.lon === other.lon &&
      asset.lat === other.lat && asset.currency === other.currency &&
      asset.insured_value_minor === other.insured_value_minor && asset.eligible === other.eligible &&
      asset.qualifying === other.qualifying && asset.mmi === other.mmi;
  });

/** Ordem fixa entre publicações: classificação, depois ID; não altera a resposta da API. */
export function orderAssets(assets: readonly Asset[]): Asset[] {
  return [...assets].sort((a, b) => rank(a) - rank(b) ||
    (a.asset_id < b.asset_id ? -1 : a.asset_id > b.asset_id ? 1 : 0));
}

/** Elegíveis inclui os classificados. Busca literal no ID, sem expressão regular. */
export function filterAssets(assets: readonly Asset[], filters: AssetFilters): Asset[] {
  const search = filters.search.trim().toLocaleLowerCase("pt-BR");
  return assets.filter((asset) =>
    (!search || asset.asset_id.toLocaleLowerCase("pt-BR").includes(search)) &&
    (filters.currency === "all" || asset.currency === filters.currency) &&
    (filters.status === "all" ||
      (filters.status === "qualifying" && asset.qualifying) ||
      (filters.status === "eligible" && asset.eligible) ||
      (filters.status === "ineligible" && !asset.eligible)),
  );
}

/** A página é baseada em zero; todos os ativos continuam disponíveis pela navegação. */
export function paginateAssets(assets: readonly Asset[], requestedPage: number, pageSize: 50 | 100): AssetPage {
  const pages = Math.max(1, Math.ceil(assets.length / pageSize));
  const page = Math.min(pages - 1, Math.max(0, Number.isFinite(requestedPage) ? Math.trunc(requestedPage) : 0));
  const offset = page * pageSize;
  return {
    assets: assets.slice(offset, offset + pageSize),
    page,
    pages,
    total: assets.length,
    first: assets.length ? offset + 1 : 0,
    last: Math.min(offset + pageSize, assets.length),
  };
}

/** Só as linhas da página corrente entram no DOM; os controles não são recriados ao digitar. */
export class AssetList {
  private assets: Asset[] = [];
  private filtered: Asset[] = [];
  private filters = defaults();
  private page = 0;
  private pageSize: 50 | 100 = 50;
  private readonly search: HTMLInputElement;
  private readonly status: HTMLSelectElement;
  private readonly currency: HTMLSelectElement;
  private readonly size: HTMLSelectElement;
  private readonly summary: HTMLElement;
  private readonly contextLabel: HTMLElement;
  private readonly caption: HTMLTableCaptionElement;
  private readonly body: HTMLTableSectionElement;
  private readonly pageLabel: HTMLElement;
  private readonly previous: HTMLButtonElement;
  private readonly next: HTMLButtonElement;
  private readonly reset: HTMLButtonElement;
  private context: AssetListContext = "Carteira inteira";

  constructor(
    private readonly container: HTMLElement,
    private readonly onSelectAsset?: (asset: Asset) => void,
    private readonly onStateChange?: (state: AssetListState) => void,
  ) {
    const prefix = `asset-list-${++instanceCount}`;
    container.innerHTML = `
      <div class="asset-list-toolbar"><div class="asset-list-heading"><h2>Ativos</h2>
        <span class="asset-list-context">Carteira inteira</span></div>
        <p class="asset-list-count muted" role="status" aria-live="polite" aria-atomic="true"></p></div>
      <p class="asset-list-hint sr-only" id="${prefix}-hint">Os filtros abaixo alteram apenas esta lista.</p>
      <div class="asset-list-filters">
        <label for="${prefix}-search">Buscar ativo<input id="${prefix}-search" name="asset-search" data-filter="search" type="search" placeholder="ID do ativo" autocomplete="off" aria-describedby="${prefix}-hint" aria-controls="${prefix}-table"></label>
        <label for="${prefix}-status">Situação<select id="${prefix}-status" name="asset-status" data-filter="status" aria-controls="${prefix}-table">
          <option value="all">Todos</option><option value="qualifying">Classificados</option>
          <option value="eligible">Elegíveis</option><option value="ineligible">Não elegíveis</option>
        </select></label>
        <label for="${prefix}-currency">Moeda<select id="${prefix}-currency" name="asset-currency" data-filter="currency" aria-controls="${prefix}-table"><option value="all">Todas</option>
          <option value="BRL">BRL</option><option value="USD">USD</option></select></label>
        <label for="${prefix}-size">Por página<select id="${prefix}-size" name="asset-page-size" data-filter="size" aria-controls="${prefix}-table"><option value="50">50</option><option value="100">100</option></select></label>
        <button type="button" class="asset-list-reset" data-action="reset" disabled>Limpar filtros</button>
      </div>
      <div class="scroll asset-list-table" tabindex="0" role="region" aria-label="Tabela de ativos"><table id="${prefix}-table">
        <caption class="sr-only">Ativos da publicação atual, ordenados por situação e ID</caption>
        <thead><tr><th scope="col">Ativo</th><th scope="col">Valor segurado</th><th scope="col">MMI</th>
          <th scope="col">Situação</th>${onSelectAsset ? '<th scope="col">Mapa</th>' : ""}</tr></thead>
        <tbody></tbody>
      </table></div>
      <nav class="asset-list-pager" aria-label="Paginação da lista de ativos">
        <button type="button" data-action="previous" aria-controls="${prefix}-table">Anterior</button>
        <span data-page-label tabindex="-1" aria-current="page"></span>
        <button type="button" data-action="next" aria-controls="${prefix}-table">Próxima</button>
      </nav>`;
    const find = <T extends HTMLElement>(selector: string): T => {
      const element = container.querySelector<T>(selector);
      if (!element) throw new Error(`controle de ativos ausente: ${selector}`);
      return element;
    };
    this.search = find<HTMLInputElement>('[data-filter="search"]');
    this.status = find<HTMLSelectElement>('[data-filter="status"]');
    this.currency = find<HTMLSelectElement>('[data-filter="currency"]');
    this.size = find<HTMLSelectElement>('[data-filter="size"]');
    this.summary = find(".asset-list-count");
    this.contextLabel = find(".asset-list-context");
    this.caption = find<HTMLTableCaptionElement>("caption");
    this.body = find<HTMLTableSectionElement>("tbody");
    this.pageLabel = find("[data-page-label]");
    this.previous = find<HTMLButtonElement>('[data-action="previous"]');
    this.next = find<HTMLButtonElement>('[data-action="next"]');
    this.reset = find<HTMLButtonElement>('.asset-list-reset');
    container.addEventListener("input", (event) => {
      if (event.target !== this.search) return;
      this.filters.search = this.search.value;
      this.refilter();
      this.emitStateChange();
    });
    container.addEventListener("change", (event) => {
      if (event.target === this.status) this.filters.status = this.status.value as AssetStatusFilter;
      else if (event.target === this.currency) this.filters.currency = this.currency.value as AssetFilters["currency"];
      else if (event.target === this.size) this.pageSize = this.size.value === "100" ? 100 : 50;
      else return;
      this.refilter();
      this.emitStateChange();
    });
    container.addEventListener("click", (event) => {
      if (!(event.target instanceof Element)) return;
      const button = event.target.closest<HTMLButtonElement>("button");
      if (!button || button.disabled || !container.contains(button)) return;
      if (button.dataset.action === "previous" || button.dataset.action === "next") {
        this.page += button.dataset.action === "previous" ? -1 : 1;
        this.render();
        this.pageLabel.focus({ preventScroll: true });
        this.emitStateChange();
      }
      else if (button.dataset.action === "reset") {
        this.resetControls();
        this.refilter();
        this.search.focus({ preventScroll: true });
        this.emitStateChange();
      }
      else if (button.dataset.assetIndex !== undefined) {
        const asset = this.filtered[Number(button.dataset.assetIndex)];
        if (asset) this.onSelectAsset?.(asset);
      }
    });
    this.clear();
  }

  setAssets(assets: Asset[] | null): void {
    if (assets === null) { this.clear(); return; }
    const ordered = orderAssets(assets);
    if (!this.container.hidden && sameAssets(this.assets, ordered)) return;
    this.assets = ordered;
    this.container.hidden = false;
    this.refilter(false);
  }

  setContext(context: AssetListContext): void {
    if (context === this.context) return;
    this.context = context;
    this.contextLabel.textContent = context;
    this.caption.textContent = `${context === "Área selecionada" ? "Ativos da área selecionada" : "Ativos da carteira"}, ordenados por situação e ID`;
    if (!this.container.hidden) this.render();
  }

  getState(): AssetListState {
    return { filters: { ...this.filters }, page: this.page, pageSize: this.pageSize };
  }

  restoreState(state: AssetListState): void {
    const restored = normalizeAssetListState(state);
    this.filters = restored.filters;
    this.page = restored.page;
    this.pageSize = restored.pageSize;
    this.search.value = this.filters.search;
    this.status.value = this.filters.status;
    this.currency.value = this.filters.currency;
    this.size.value = String(this.pageSize);
    this.reset.disabled = !hasFilters(this.filters);
    this.filtered = filterAssets(this.assets, this.filters);
    if (!this.container.hidden) this.render();
  }

  clear(): void {
    this.assets = [];
    this.filtered = [];
    this.resetControls();
    this.summary.textContent = "";
    this.body.replaceChildren();
    this.pageLabel.textContent = "";
    this.previous.disabled = true;
    this.next.disabled = true;
    this.reset.disabled = true;
    this.container.hidden = true;
  }

  private resetControls(): void {
    this.filters = defaults();
    this.page = 0;
    this.search.value = "";
    this.status.value = "all";
    this.currency.value = "all";
  }

  private emitStateChange(): void {
    this.onStateChange?.(this.getState());
  }

  private refilter(resetPage = true): void {
    this.filtered = filterAssets(this.assets, this.filters);
    if (resetPage) this.page = 0;
    this.render();
  }

  private render(): void {
    const result = paginateAssets(this.filtered, this.page, this.pageSize);
    this.page = result.page;
    const qualifying = this.assets.filter((asset) => asset.qualifying).length;
    this.summary.textContent = `${count(this.assets.length)} ${this.assets.length === 1 ? "ativo" : "ativos"} · ${count(qualifying)} ${qualifying === 1 ? "classificado" : "classificados"} · ${count(result.total)} ${result.total === 1 ? "encontrado" : "encontrados"}`;
    this.reset.disabled = !hasFilters(this.filters);
    this.body.innerHTML = result.assets.map((asset, index) => {
      const situation = rank(asset);
      const mmi = asset.mmi === null ? "–" : asset.mmi.toFixed(2).replace(".", ",");
      const mapButton = this.onSelectAsset ?
        `<td><button type="button" class="asset-map-button" data-asset-index="${result.page * this.pageSize + index}" aria-label="Ver ativo ${esc(asset.asset_id)} no mapa">Ver no mapa</button></td>` : "";
      return `<tr class="r${situation}"><td>${esc(asset.asset_id)}</td>
        <td class="money">${formatMinor(asset.insured_value_minor, asset.currency)}</td>
        <td>${mmi}</td><td><span class="asset-status asset-status-${situation}">${STATUS[situation]}</span></td>${mapButton}</tr>`;
    }).join("") || `<tr><td colspan="${this.onSelectAsset ? 5 : 4}" class="asset-list-empty">
      <p>${this.assets.length ? "Nenhum ativo corresponde aos filtros." : this.context === "Área selecionada" ? "Nenhum ativo nesta área. Selecione outra área no mapa." : "Nenhum ativo nesta publicação."}</p>
      ${hasFilters(this.filters) ? '<button type="button" class="asset-list-empty-action" data-action="reset">Limpar filtros</button>' : ""}</td></tr>`;
    this.pageLabel.textContent = result.total
      ? `${count(result.first)}–${count(result.last)} de ${count(result.total)}. Página ${count(result.page + 1)} de ${count(result.pages)}.`
      : "0 ativos na lista.";
    this.previous.disabled = result.page === 0;
    this.next.disabled = result.page + 1 >= result.pages;
    this.previous.setAttribute("aria-label", `Página anterior, ${count(Math.max(1, result.page))} de ${count(result.pages)}`);
    this.next.setAttribute("aria-label", `Próxima página, ${count(Math.min(result.pages, result.page + 2))} de ${count(result.pages)}`);
  }
}
