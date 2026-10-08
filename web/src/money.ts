// Dinheiro chega como string em unidade menor e só passa por operações de string: sem Number,
// valores acima de 2^53 saem exatos.

const SYMBOL = new Map([
  ["BRL", "R$"],
  ["USD", "US$"],
]);

/** "123450", "USD" -> "US$ 1.234,50". Recusa o que o contrato recusa: sinal, zero à esquerda, vazio. */
export function formatMinor(minor: string, currency: string): string {
  const symbol = SYMBOL.get(currency);
  if (symbol === undefined) throw new RangeError(`moeda não suportada: ${currency}`);
  if (!/^(0|[1-9][0-9]*)$/.test(minor)) throw new RangeError(`valor inválido: "${minor}"`);
  const digits = minor.padStart(3, "0");
  const units = digits.slice(0, -2).replace(/\B(?=(\d{3})+$)/g, ".");
  return `${symbol} ${units},${digits.slice(-2)}`;
}
