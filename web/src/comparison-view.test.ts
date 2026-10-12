import { expect, it } from "vitest";
import type { ExposureResult } from "./api";
import { renderComparison, type ComparisonResponse } from "./comparison-view";

const result: ExposureResult = {
  calculation_id: "p", input_vector: { stream_scope: "REPLAY", earthquake_uid: "e", product_revision_id: "grid", tenant_id: "t", mode: "SHAKEMAP_MMI", portfolio_epoch: 1 },
  status: "COMPLETE", eligible_count: 1, qualifying_count: 1,
  qualifying_value_minor: { BRL: "9007199254740993" } as ExposureResult["qualifying_value_minor"],
  calculated_at: "2026-10-11T00:00:00Z", published_at: "2026-10-11T00:00:01Z", limitations: [],
};
const response: ComparisonResponse = {
  publication_id: "p", previous_publication_id: "old", previous_available: true, unavailable_reason: null,
  causes: ["<script>bad</script>"], before: { publication_id: "old", generation: null, result },
  after: { publication_id: "p", generation: 2, result }, total_changes: 0, limit: 100, offset: 0, changes: [],
};

it("preserves large amounts and displays an absent currency as zero", () => {
  const html = renderComparison(response);
  expect(html).toContain("R$ 90.071.992.547.409,93");
  expect(html).toContain("US$ 0,00");
  expect(html).not.toContain("<script>bad</script>");
});

it("withholds historical summaries when privacy invalidates the previous publication", () => {
  const html = renderComparison({ ...response, previous_available: false, unavailable_reason: "privacy_epoch", before: null });
  expect(html).toContain("invalidada após uma exclusão");
  expect(html).not.toContain("90.071.992");
  expect(html).not.toContain("Anterior: lon");
});
