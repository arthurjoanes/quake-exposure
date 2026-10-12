import type { Asset } from "./api";
import type { MapViewport } from "./spatial";

export interface RegionCluster {
  cluster_id: string;
  lon: number;
  lat: number;
  count: number;
  eligible_count: number;
  qualifying_count: number;
  insured_value_minor: Record<string, string>;
  qualifying_value_minor: Record<string, string>;
}

export interface RegionResponse {
  publication_id: string;
  representation: "points" | "clusters";
  bounds: Omit<MapViewport, "zoom">;
  zoom: number;
  total_count: number;
  eligible_count: number;
  qualifying_count: number;
  insured_value_minor: Record<string, string>;
  qualifying_value_minor: Record<string, string>;
  assets: Asset[];
  clusters: RegionCluster[];
}
