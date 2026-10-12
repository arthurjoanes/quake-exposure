// Formatos de src/quakeexposure/api/__init__.py e contracts/exposure-result.schema.json.

export type Scope = "LIVE" | "REPLAY" | "FAULT";
export type Mode = "RADIUS_TRIAGE" | "SHAKEMAP_MMI";
export type ResultStatus =
  | "COMPLETE"
  | "NO_MAP"
  | "UNSUPPORTED_GRID"
  | "RETRACTED"
  | "REDACTED_BY_ERASURE";

export interface Tenant {
  tenant_id: string;
  name: string;
}

export interface QuakeEvent {
  earthquake_uid: string;
  preferred_id: string;
  stream_scope: Scope;
  lifecycle: "ACTIVE" | "RETRACTED";
  event_time: string;
  lon: number;
  lat: number;
  depth_km: number | null;
  mag: number | null;
  event_type: string | null;
  source_updated_at: string;
  product_revision_id: string | null;
  revisions: number;
}

export interface Revision {
  revision_id: string;
  provider_event_id: string;
  source_updated_at: string;
  mag: number | null;
  lon: number;
  lat: number;
  status: string | null;
  event_type: string | null;
}

export interface ExposureResult {
  calculation_id: string;
  input_vector: {
    stream_scope: Scope;
    earthquake_uid: string;
    product_revision_id: string;
    tenant_id: string;
    mode: Mode;
    portfolio_epoch: number;
  };
  status: ResultStatus;
  eligible_count: number | null;
  qualifying_count: number | null;
  qualifying_value_minor: { BRL: string; USD: string } | null;
  calculated_at: string;
  published_at: string | null;
  limitations: string[];
}

export interface CurrentPublication {
  publication_id: string;
  generation: number;
  result: ExposureResult;
}

export interface Asset {
  asset_id: string;
  lon: number;
  lat: number;
  currency: string;
  insured_value_minor: string;
  eligible: boolean;
  qualifying: boolean;
  mmi: number | null;
}

export interface AssetsResponse {
  publication_id: string;
  assets: Asset[];
}

export interface DiffItem {
  asset_id: string;
  currency: string;
  insured_value_minor: string;
  mmi_before: number | null;
  mmi_after: number | null;
}

export interface DiffResponse {
  publication_id: string;
  previous_publication_id: string | null;
  causes: string[];
  entered: DiffItem[];
  left: DiffItem[];
}

export interface SourceStatus {
  last_ok_age_s: number | null;
  stale: boolean;
  last_error: string | null;
}

export interface ApiStatus {
  sources: { feed: SourceStatus; incremental: SourceStatus };
  jobs_pending: number;
  jobs_failed: number;
  publications_outdated: number;
  cdc?: {
    connector_state: string;
    task_state: string;
    pending_transactions: number;
    retained_wal_bytes: number | null;
    stale: boolean;
  };
}

export class PrivacyRecalculation extends Error {
  constructor() { super("Recalculando após exclusão."); }
}

/** GET relativo (mesma origem). null em 404; outro erro HTTP vira exceção. */
export async function get<T>(path: string, params: Record<string, string> = {}): Promise<T | null> {
  if (import.meta.env.VITE_DEMO === "1") {
    const { demoGet } = await import("./demo-api");
    return demoGet<T>(path, params);
  }
  const query = new URLSearchParams(params).toString();
  const r = await fetch(query ? `${path}?${query}` : path, { headers: { Accept: "application/json" } });
  if (r.status === 404) return null;
  if (r.status === 409) throw new PrivacyRecalculation();
  if (!r.ok) throw new Error(`${path}: HTTP ${r.status}`);
  return (await r.json()) as T;
}
