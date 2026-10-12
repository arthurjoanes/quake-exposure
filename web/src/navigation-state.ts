import { normalizeAssetListState, type AssetListState } from "./asset-list";

const urlPage = (value: string | null): number => {
  if (value === null || !/^[1-9]\d*$/.test(value)) return 0;
  const page = Number(value);
  return Number.isSafeInteger(page) ? page - 1 : 0;
};

/** A URL usa páginas a partir de um; a lista usa índices a partir de zero. */
export function readListState(params: URLSearchParams): AssetListState {
  return normalizeAssetListState({
    filters: {
      search: params.get("q") ?? "",
      status: params.get("situation"),
      currency: params.get("currency"),
    },
    page: urlPage(params.get("page")),
    pageSize: params.get("pageSize") === "100" ? 100 : 50,
  });
}

/** Modifica só os parâmetros da lista; a seleção da análise permanece intacta. */
export function writeListState(params: URLSearchParams, state: AssetListState): void {
  const normalized = normalizeAssetListState(state);
  const write = (key: string, value: string | null): void => {
    if (value === null) params.delete(key);
    else params.set(key, value);
  };
  write("q", normalized.filters.search.trim() ? normalized.filters.search : null);
  write("situation", normalized.filters.status === "all" ? null : normalized.filters.status);
  write("currency", normalized.filters.currency === "all" ? null : normalized.filters.currency);
  const page = Math.min(normalized.page, Number.MAX_SAFE_INTEGER - 1);
  write("page", page === 0 ? null : String(page + 1));
  write("pageSize", normalized.pageSize === 50 ? null : "100");
}

export function readComparison(params: URLSearchParams): boolean {
  return params.get("comparison") === "1";
}

export function writeComparison(params: URLSearchParams, opened: boolean): void {
  if (opened) params.set("comparison", "1");
  else params.delete("comparison");
}
