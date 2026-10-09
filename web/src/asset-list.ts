import type { Asset } from "./api";
import { formatMinor } from "./money";

export type AssetStatusFilter = "all" | "qualifying" | "eligible" | "ineligible";
export interface AssetFilters {
  search: string;
  status: AssetStatusFilter;
  currency: "all" | "BRL" | "USD";
}
export interface AssetPage {
  assets: Asset[];
  page: number;
  pages: number;
  total: number;
  first: number;
  last: number;
}

const STATUS = ["classificado", "elegível", "não elegível"];
const rank = (asset: Asset): number => asset.qualifying ? 0 : asset.eligible ? 1 : 2;
const esc = (text: string): string => text.replace(/[&<>"']/g, (c) => `&#${c.charCodeAt(0)};`);
const count = (value: number): string => value.toLocaleString("pt-BR");
const defaults = (): AssetFilters => ({ search: "", status: "all", currency: "all" });

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
  private readonly body: HTMLTableSectionElement;
  private readonly pageLabel: HTMLElement;
  private readonly previous: HTMLButtonElement;
  private readonly next: HTMLButtonElement;

  constructor(
    private readonly container: HTMLElement,
    private readonly onSelectAsset?: (asset: Asset) => void,
  ) {
    container.innerHTML = `
      <div class="asset-list-toolbar"><h2>Ativos</h2><p class="asset-list-count muted" aria-live="polite"></p></div>
      <div class="asset-list-filters">
        <label>Buscar ativo<input data-filter="search" type="search" placeholder="ID do ativo" autocomplete="off"></label>
        <label>Situação<select data-filter="status">
          <option value="all">Todos</option><option value="qualifying">Classificados</option>
          <option value="eligible">Elegíveis</option><option value="ineligible">Não elegíveis</option>
        </select></label>
        <label>Moeda<select data-filter="currency"><option value="all">Todas</option>
          <option value="BRL">BRL</option><option value="USD">USD</option></select></label>
        <label>Por página<select data-filter="size"><option value="50">50</option><option value="100">100</option></select></label>
      </div>
      <div class="scroll asset-list-table"><table>
        <caption class="sr-only">Ativos da publicação atual, ordenados por situação e ID</caption>
        <thead><tr><th scope="col">Ativo</th><th scope="col">Valor segurado</th><th scope="col">MMI</th>
          <th scope="col">Situação</th>${onSelectAsset ? '<th scope="col">Mapa</th>' : ""}</tr></thead>
        <tbody></tbody>
      </table></div>
      <nav class="asset-list-pager" aria-label="Páginas de ativos">
        <button type="button" data-action="previous">Anterior</button>
        <span data-page-label aria-live="polite"></span>
        <button type="button" data-action="next">Próxima</button>
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
    this.body = find<HTMLTableSectionElement>("tbody");
    this.pageLabel = find("[data-page-label]");
    this.previous = find<HTMLButtonElement>('[data-action="previous"]');
    this.next = find<HTMLButtonElement>('[data-action="next"]');
    container.addEventListener("input", (event) => {
      if (event.target !== this.search) return;
      this.filters.search = this.search.value;
      this.refilter();
    });
    container.addEventListener("change", (event) => {
      if (event.target === this.status) this.filters.status = this.status.value as AssetStatusFilter;
      else if (event.target === this.currency) this.filters.currency = this.currency.value as AssetFilters["currency"];
      else if (event.target === this.size) this.pageSize = this.size.value === "100" ? 100 : 50;
      else return;
      this.refilter();
    });
    container.addEventListener("click", (event) => {
      if (!(event.target instanceof Element)) return;
      const button = event.target.closest<HTMLButtonElement>("button");
      if (!button || button.disabled || !container.contains(button)) return;
      if (button.dataset.action === "previous") { this.page--; this.render(); }
      else if (button.dataset.action === "next") { this.page++; this.render(); }
      else if (button.dataset.assetIndex !== undefined) {
        const asset = this.filtered[Number(button.dataset.assetIndex)];
        if (asset) this.onSelectAsset?.(asset);
      }
    });
    this.clear();
  }

  setAssets(assets: Asset[] | null): void {
    if (assets === null) { this.clear(); return; }
    this.assets = orderAssets(assets);
    this.resetControls();
    this.container.hidden = false;
    this.refilter();
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
    this.container.hidden = true;
  }

  private resetControls(): void {
    this.filters = defaults();
    this.page = 0;
    this.search.value = "";
    this.status.value = "all";
    this.currency.value = "all";
  }

  private refilter(): void {
    this.filtered = filterAssets(this.assets, this.filters);
    this.page = 0;
    this.render();
  }

  private render(): void {
    const result = paginateAssets(this.filtered, this.page, this.pageSize);
    this.page = result.page;
    const qualifying = this.assets.filter((asset) => asset.qualifying).length;
    this.summary.textContent = `${count(this.assets.length)} ativos · ${count(qualifying)} classificados · ${count(result.total)} no filtro`;
    this.body.innerHTML = result.assets.map((asset, index) => {
      const situation = rank(asset);
      const mmi = asset.mmi === null ? "–" : asset.mmi.toFixed(2).replace(".", ",");
      const mapButton = this.onSelectAsset ?
        `<td><button type="button" class="asset-map-button" data-asset-index="${result.page * this.pageSize + index}" aria-label="Ver ativo ${esc(asset.asset_id)} no mapa">Ver no mapa</button></td>` : "";
      return `<tr class="r${situation}"><td>${esc(asset.asset_id)}</td>
        <td class="money">${formatMinor(asset.insured_value_minor, asset.currency)}</td>
        <td>${mmi}</td><td>${STATUS[situation]}</td>${mapButton}</tr>`;
    }).join("") || `<tr><td colspan="${this.onSelectAsset ? 5 : 4}" class="muted">${this.assets.length ? "Nenhum ativo corresponde aos filtros." : "Nenhum ativo nesta publicação."}</td></tr>`;
    this.pageLabel.textContent = `${count(result.first)}–${count(result.last)} de ${count(result.total)} · página ${count(result.page + 1)} de ${count(result.pages)}`;
    this.previous.disabled = result.page === 0;
    this.next.disabled = result.page + 1 >= result.pages;
  }
}
