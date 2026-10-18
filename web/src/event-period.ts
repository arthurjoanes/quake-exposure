const DAY_MS = 86_400_000;

export function normalizeDay(value: string): string {
  if (!/^\d{4}-\d{2}-\d{2}$/.test(value)) return "";
  const time = Date.parse(`${value}T00:00:00Z`);
  return Number.isFinite(time) && new Date(time).toISOString().slice(0, 10) === value ? value : "";
}

export function filterEventPeriod<T extends { event_time: string }>(events: readonly T[], from: string, to: string): T[] {
  from = normalizeDay(from);
  to = normalizeDay(to);
  if (from && to && from > to) throw new RangeError("A data inicial deve vir antes da final.");
  if (!from && !to) return [...events];
  const start = from ? Date.parse(`${from}T00:00:00Z`) : -Infinity;
  const end = to ? Date.parse(`${to}T00:00:00Z`) + DAY_MS : Infinity;
  return events.filter(event => {
    const instant = Date.parse(event.event_time);
    return instant >= start && instant < end;
  });
}

export function recentEventPeriod(events: readonly { event_time: string }[], days: number): { from: string; to: string } {
  const latest = Math.max(...events.map(event => Date.parse(event.event_time)).filter(Number.isFinite));
  if (!Number.isFinite(latest)) return { from: "", to: "" };
  const to = new Date(latest).toISOString().slice(0, 10);
  const from = new Date(Date.parse(`${to}T00:00:00Z`) - (days - 1) * DAY_MS).toISOString().slice(0, 10);
  return { from, to };
}
