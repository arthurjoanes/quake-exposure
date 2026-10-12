import type { Asset, CurrentPublication, Mode, QuakeEvent, Revision, Tenant } from "./api";

export interface DemoMetadata {
  snapshot_at: string;
  source_commit: string;
  fixture_manifest_sha256: string;
  scope: "REPLAY";
  portfolio: "synthetic";
  computation: "domain-fixtures" | "local-api";
  pipeline_executed: boolean;
  available_modes: Mode[];
  default_selection: { tenant: string; event: string; mode: Mode };
  scenario: {
    title: string;
    tenant: string;
    event: string;
    mode: Mode;
    publication_id: string;
    previous_publication_id: string | null;
    changes: number;
  } | null;
}

interface Snapshot {
  schema_version: 1;
  metadata: DemoMetadata;
  tenants: Tenant[];
  events: QuakeEvent[];
  revisions: Record<string, Revision[]>;
  status: unknown;
  operations: Record<string, unknown>;
  current: CurrentPublication[];
  publications: Record<string, { tenant: string; file: string }>;
}

interface SnapshotPublication {
  current: CurrentPublication;
  assets: Asset[];
  diff: unknown;
  comparison: {
    publication_id: string;
    previous_publication_id: string | null;
    previous_available: boolean;
    unavailable_reason: string | null;
    causes: string[];
    before: unknown;
    after: unknown;
    total_changes: number;
    offset: number;
    limit: number;
    changes: unknown[];
  };
}

let snapshotPromise: Promise<Snapshot> | undefined;
const publications = new Map<string, Promise<SnapshotPublication>>();

async function readJson<T>(file: string): Promise<T> {
  const response = await fetch(`${import.meta.env.BASE_URL}demo/${file}`, {
    headers: { Accept: "application/json" },
  });
  if (!response.ok) throw new Error(`Snapshot REPLAY: HTTP ${response.status}`);
  return (await response.json()) as T;
}

function snapshot(): Promise<Snapshot> {
  snapshotPromise ??= readJson<Snapshot>("snapshot.json").catch((error: unknown) => {
    snapshotPromise = undefined;
    throw error;
  });
  return snapshotPromise;
}

async function publication(id: string, index: Snapshot): Promise<SnapshotPublication> {
  let pending = publications.get(id);
  if (!pending) {
    pending = readJson<SnapshotPublication>(index.publications[id].file).catch((error: unknown) => {
      publications.delete(id);
      throw error;
    });
    publications.set(id, pending);
  }
  return pending;
}

const minorTotals = (assets: Asset[], qualifyingOnly: boolean): Record<string, string> => {
  const totals: Record<string, bigint> = { BRL: 0n, USD: 0n };
  for (const asset of assets) {
    if (!qualifyingOnly || asset.qualifying) {
      totals[asset.currency] = (totals[asset.currency] ?? 0n) + BigInt(asset.insured_value_minor);
    }
  }
  return Object.fromEntries(Object.entries(totals).map(([currency, value]) => [currency, String(value)]));
};

/** A mesma divisão geográfica usada na API; mantém totais completos em respostas limitadas. */
export function demoRegion(id: string, assets: Asset[], params: Record<string, string>): unknown {
  const west = Number(params.west ?? -180);
  const south = Number(params.south ?? -90);
  const east = Number(params.east ?? 180);
  const north = Number(params.north ?? 90);
  const zoom = Number(params.zoom ?? 0);
  const limit = Number(params.limit ?? 2000);
  if (![west, east, south, north, zoom, limit].every(Number.isFinite)
    || west < -180 || west > 180 || east < -180 || east > 180
    || south < -90 || north > 90 || south > north
    || !Number.isInteger(zoom) || zoom < 0 || zoom > 22
    || !Number.isInteger(limit) || limit < 1 || limit > 2000) {
    throw new Error("Região inválida.");
  }
  const selected = assets.filter((asset) => asset.lat >= south && asset.lat <= north
    && ((west <= east ? asset.lon >= west && asset.lon <= east : asset.lon >= west || asset.lon <= east)
      || (west === -180 && asset.lon === 180) || (east === 180 && asset.lon === -180)));
  const points = zoom >= 7 && selected.length <= limit;
  const width = west <= east ? east - west : 360 + east - west;
  const height = north - south;
  const cell = 90 / 2 ** zoom;
  const columns = Math.min(Math.max(1, Math.ceil(width / cell)), Math.floor(Math.sqrt(limit)));
  const rows = Math.min(Math.max(1, Math.ceil(height / cell)), Math.floor(limit / columns));
  const groups = new Map<string, { assets: Asset[]; lons: number[] }>();
  if (!points) {
    for (const asset of selected) {
      const lon = asset.lon === 180 && west === -180 ? -180
        : asset.lon === -180 && east === 180 && west > -180 ? 180
          : west > east && asset.lon < west ? asset.lon + 360 : asset.lon;
      const x = Math.min(columns - 1, Math.floor((lon - west) / Math.max(width, 1e-12) * columns));
      const y = Math.min(rows - 1, Math.floor((asset.lat - south) / Math.max(height, 1e-12) * rows));
      const key = `${columns}:${rows}:${x}:${y}`;
      let group = groups.get(key);
      if (!group) { group = { assets: [], lons: [] }; groups.set(key, group); }
      group.assets.push(asset);
      group.lons.push(lon);
    }
  }
  return {
    publication_id: id,
    representation: points ? "points" : "clusters",
    bounds: { west, south, east, north },
    zoom,
    total_count: selected.length,
    eligible_count: selected.filter((asset) => asset.eligible).length,
    qualifying_count: selected.filter((asset) => asset.qualifying).length,
    insured_value_minor: minorTotals(selected, false),
    qualifying_value_minor: minorTotals(selected, true),
    assets: points ? selected : [],
    clusters: [...groups].map(([key, group]) => ({
      cluster_id: key,
      lon: ((group.lons.reduce((sum, lon) => sum + lon, 0) / group.lons.length + 180) % 360 + 360) % 360 - 180,
      lat: group.assets.reduce((sum, asset) => sum + asset.lat, 0) / group.assets.length,
      count: group.assets.length,
      eligible_count: group.assets.filter((asset) => asset.eligible).length,
      qualifying_count: group.assets.filter((asset) => asset.qualifying).length,
      insured_value_minor: minorTotals(group.assets, false),
      qualifying_value_minor: minorTotals(group.assets, true),
    })),
  };
}

/** Todas as leituras do build público vêm do snapshot; não há chamadas para /api no servidor. */
export async function demoGet<T>(path: string, params: Record<string, string> = {}): Promise<T | null> {
  const index = await snapshot();
  if (params.scope && params.scope !== "REPLAY") return null;
  let value: unknown;
  if (path === "/api/demo") value = index.metadata;
  else if (path === "/api/tenants") value = index.tenants;
  else if (path === "/api/events") value = index.events;
  else if (path === "/api/status") value = index.status;
  else if (path === "/api/operations") value = index.operations[params.tenant] ?? null;
  else if (path === "/api/publications/current") {
    value = index.current.find((current) => {
      const vector = current.result.input_vector;
      return vector.tenant_id === params.tenant && vector.earthquake_uid === params.earthquake_uid
        && vector.mode === params.mode;
    }) ?? null;
  } else {
    const revision = /^\/api\/events\/([^/]+)\/revisions$/.exec(path);
    const pub = /^\/api\/publications\/([a-f0-9]{64})\/(assets|diff|comparison|region)$/.exec(path);
    if (revision) value = index.revisions[decodeURIComponent(revision[1])] ?? null;
    else if (pub && index.publications[pub[1]]?.tenant === params.tenant) {
      const stored = await publication(pub[1], index);
      if (pub[2] === "assets") value = { publication_id: pub[1], assets: stored.assets };
      else if (pub[2] === "diff") value = stored.diff;
      else if (pub[2] === "region") value = demoRegion(pub[1], stored.assets, params);
      else {
        const offset = Number(params.offset ?? 0);
        const limit = Number(params.limit ?? 2000);
        if (!Number.isInteger(offset) || offset < 0 || !Number.isInteger(limit) || limit < 1 || limit > 2000) {
          throw new Error("Paginação inválida.");
        }
        value = { ...stored.comparison, offset, limit, changes: stored.comparison.changes.slice(offset, offset + limit) };
      }
    } else value = null;
  }
  // Respostas isoladas: componentes não podem alterar o snapshot guardado no cache.
  return value === null || value === undefined ? null : structuredClone(value) as T;
}
