import type { Asset } from "./api";
import { formatMinor } from "./money";

export interface AreaTotals {
  count: number;
  eligible: number;
  qualifying: number;
  insured: Record<"BRL" | "USD", string>;
  classified: Record<"BRL" | "USD", string>;
}

export function areaTotals(assets: readonly Asset[]): AreaTotals {
  const insured = { BRL: 0n, USD: 0n };
  const classified = { BRL: 0n, USD: 0n };
  let eligible = 0;
  let qualifying = 0;
  for (const asset of assets) {
    const currency = asset.currency;
    if (currency !== "BRL" && currency !== "USD") throw new RangeError("Moeda não suportada.");
    formatMinor(asset.insured_value_minor, currency);
    const value = BigInt(asset.insured_value_minor);
    insured[currency] += value;
    if (asset.eligible) eligible++;
    if (asset.qualifying) { qualifying++; classified[currency] += value; }
  }
  return {
    count: assets.length, eligible, qualifying,
    insured: { BRL: insured.BRL.toString(), USD: insured.USD.toString() },
    classified: { BRL: classified.BRL.toString(), USD: classified.USD.toString() },
  };
}

export function renderArea(assets: readonly Asset[]): string {
  const totals = areaTotals(assets);
  const count = (value: number) => value.toLocaleString("pt-BR");
  return `<h3>Área selecionada</h3><p>${count(totals.count)} ativos · ${count(totals.eligible)} elegíveis · ${count(totals.qualifying)} classificados</p>
    <div class="scroll"><table><thead><tr><th>Moeda</th><th>Valor na área</th><th>Valor classificado</th></tr></thead><tbody>
      ${(["BRL", "USD"] as const).map(currency => `<tr><th>${currency}</th><td class="money">${formatMinor(totals.insured[currency], currency)}</td><td class="money">${formatMinor(totals.classified[currency], currency)}</td></tr>`).join("")}
    </tbody></table></div><p class="muted">A lista mostra os ativos dessa área. O resumo e os gráficos acima continuam representando a publicação inteira.</p>`;
}
