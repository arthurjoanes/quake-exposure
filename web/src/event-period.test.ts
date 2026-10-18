import { describe, expect, it } from "vitest";
import { filterEventPeriod, normalizeDay, recentEventPeriod } from "./event-period";

const events = [
  { event_time: "2026-09-21T01:41:36Z", id: "september" },
  { event_time: "2026-10-06T23:59:59.999Z", id: "october" },
];

describe("event period in UTC", () => {
  it("keeps the complete catalog without boundaries and does not mutate it", () => {
    expect(filterEventPeriod(events, "", "")).toEqual(events);
    expect(filterEventPeriod(events, "", "")).not.toBe(events);
  });
  it("includes both complete boundary days", () => {
    expect(filterEventPeriod(events, "2026-10-06", "2026-10-06")).toEqual([events[1]]);
  });
  it("uses the instant in UTC even when an event has an offset", () => {
    expect(filterEventPeriod([{ event_time: "2026-10-07T00:30:00+02:00" }], "2026-10-06", "2026-10-06")).toHaveLength(1);
  });
  it("supports one boundary and a genuinely empty period", () => {
    expect(filterEventPeriod(events, "", "2026-09-30")).toEqual([events[0]]);
    expect(filterEventPeriod(events, "2026-11-01", "")).toEqual([]);
  });
  it.each(["2026-02-30", "2026-13-01", "bad", "2026-1-1"])("rejects invalid calendar day %s", day => {
    expect(normalizeDay(day)).toBe("");
  });
  it("rejects inverted boundaries instead of silently swapping them", () => {
    expect(() => filterEventPeriod(events, "2026-10-06", "2026-09-21")).toThrow(RangeError);
  });
  it("anchors presets to the latest catalog event, not today's clock", () => {
    expect(recentEventPeriod(events, 7)).toEqual({ from: "2026-09-30", to: "2026-10-06" });
    expect(recentEventPeriod([], 30)).toEqual({ from: "", to: "" });
  });
});
