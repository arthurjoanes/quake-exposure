import * as L from "leaflet";
import { feature } from "topojson-client";
import type { Asset, Mode, QuakeEvent } from "./api";
import { near, planar } from "./basemap";
import { AssetGroupCache, eventSignature, type AssetGroup } from "./map-data";
import { formatMinor } from "./money";

export interface MapColors { qualifying: string; eligible: string; other: string; event: string }
export interface MapDensity { assets: number; groups: number; clustered: boolean }
export interface QuakeMapOptions {
  colors?: MapColors;
  onSelect: (uid: string) => void;
  onBaseStatus?: (message: string) => void;
  onDensityChange?: (density: MapDensity) => void;
}

const DEFAULT_COLORS: MapColors = {
  qualifying: "#bf4b35", eligible: "#087f8c", other: "#a7b8c0", event: "#67549a",
};
const esc = (text: string): string => text.replace(/[&<>"']/g, (c) => `&#${c.charCodeAt(0)};`);
const number = new Intl.NumberFormat("pt-BR");
const magnitude = (mag: number | null): string => mag === null ? "–" : mag.toFixed(1).replace(".", ",");
const eventLabel = (event: QuakeEvent): string =>
  `${event.preferred_id} · M ${magnitude(event.mag)} · ${event.event_time.slice(0, 19).replace("T", " ")} UTC`
  + (event.lifecycle === "RETRACTED" ? " · retirado" : "");
const assetLabel = (asset: Asset): string =>
  `${asset.asset_id} · ${formatMinor(asset.insured_value_minor, asset.currency)}`
  + (asset.mmi === null ? "" : ` · MMI ${asset.mmi.toFixed(2).replace(".", ",")}`)
  + ` · ${asset.qualifying ? "classificado" : asset.eligible ? "elegível" : "não elegível"}`;

interface EventMarker { signature: string; event: QuakeEvent; marker: L.CircleMarker }
interface AssetMarker { group: AssetGroup; marker: L.CircleMarker }

/** Camadas persistentes: polling e pan atualizam só os pontos que realmente mudaram. */
export class QuakeMap {
  private readonly map: L.Map;
  private readonly colors: MapColors;
  private readonly assetRenderer: L.Canvas;
  private readonly eventRenderer: L.Canvas;
  private readonly assetLayer: L.LayerGroup;
  private readonly eventLayer: L.LayerGroup;
  private readonly offline = L.layerGroup();
  private readonly groups = new AssetGroupCache();
  private readonly events = new Map<string, EventMarker>();
  private readonly resize: ResizeObserver;
  private readonly animate: boolean;
  private assetMarkers: AssetMarker[] = [];
  private assets: readonly Asset[] = [];
  private selectedUid = "";
  private selectedEvent: QuakeEvent | undefined;
  private radius: L.Circle | null = null;
  private renderedGroups: AssetGroup[] | null = null;
  private offlineLoading = false;
  private offlineLoaded = false;
  private offlineReason = "";
  private destroyed = false;
  private resizeFrame = 0;

  constructor(container: string | HTMLElement, private readonly options: QuakeMapOptions) {
    this.colors = options.colors ?? DEFAULT_COLORS;
    this.animate = !window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    this.map = L.map(container, {
      minZoom: 2, maxZoom: 18, preferCanvas: true, worldCopyJump: true,
      zoomAnimation: this.animate, fadeAnimation: this.animate,
      markerZoomAnimation: this.animate, wheelPxPerZoomLevel: 90,
      zoomControl: false,
    }).setView([15, 0], 2);
    this.map.createPane("land").style.zIndex = "200";
    this.map.createPane("radius").style.zIndex = "320";
    this.map.createPane("assets").style.zIndex = "350";
    this.map.createPane("events").style.zIndex = "400";
    this.assetRenderer = L.canvas({ pane: "assets", padding: 0.3 });
    this.eventRenderer = L.canvas({ pane: "events", padding: 0.3 });
    this.assetLayer = L.layerGroup().addTo(this.map);
    this.eventLayer = L.layerGroup().addTo(this.map);
    L.control.zoom({ zoomInTitle: "Aproximar", zoomOutTitle: "Afastar" }).addTo(this.map);
    L.control.scale({ imperial: false, maxWidth: 140 }).addTo(this.map);

    // O navegador mantém o cache HTTP e o Referer. Sem prefetch, proxy ou download offline.
    const detailed = L.tileLayer("https://tile.openstreetmap.org/{z}/{x}/{y}.png", {
      maxZoom: 18, updateWhenIdle: true, updateWhenZooming: false, keepBuffer: 1,
      attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors',
    });
    let loaded = 0;
    let failed = 0;
    detailed.on("add", () => {
      loaded = 0;
      failed = 0;
      this.offlineReason = "";
      this.options.onBaseStatus?.("OpenStreetMap · ruas e lugares");
    });
    detailed.on("loading", () => { loaded = 0; failed = 0; });
    detailed.on("tileload", () => { loaded++; });
    detailed.on("tileerror", () => {
      failed++;
      if (failed >= 3 && loaded === 0 && this.map.hasLayer(detailed)) {
        this.offlineReason = "Mapa detalhado indisponível · base offline";
        this.map.removeLayer(detailed);
        this.offline.addTo(this.map);
      }
    });
    this.offline.on("add", () => {
      this.options.onBaseStatus?.(this.offlineReason || "Natural Earth · base offline");
      void this.loadOffline();
    });
    L.control.layers({ "Ruas e lugares": detailed, "Base offline": this.offline }, {}, {
      position: "topright", collapsed: false,
    }).addTo(this.map);
    detailed.addTo(this.map);
    this.map.on("moveend", () => this.rewrap());
    this.map.on("zoomend", () => this.drawAssets());
    this.resize = new ResizeObserver(() => {
      cancelAnimationFrame(this.resizeFrame);
      this.resizeFrame = requestAnimationFrame(() => {
        if (!this.destroyed) this.map.invalidateSize({ pan: true, animate: false });
      });
    });
    this.resize.observe(this.map.getContainer());
  }

  private async loadOffline(): Promise<void> {
    if (this.offlineLoading || this.offlineLoaded) return;
    this.offlineLoading = true;
    try {
      const { default: countries } = await import("world-atlas/countries-50m.json");
      if (this.destroyed) return;
      const collection = feature(countries, countries.objects.countries);
      const polygons = ("features" in collection ? collection.features : [collection]).flatMap((country) =>
        country.geometry.type === "MultiPolygon" ? country.geometry.coordinates
          : country.geometry.type === "Polygon" ? [country.geometry.coordinates] : []);
      const geometry = { type: "MultiPolygon" as const, coordinates: planar(polygons) };
      L.geoJSON(geometry, {
        pane: "land", interactive: false,
        attribution: '<a href="https://www.naturalearthdata.com/">Natural Earth</a>',
        style: { renderer: L.canvas({ pane: "land", padding: 0.3 }), color: "#a6b7b9", weight: 0.65,
          fillColor: "#f2f1e9", fillOpacity: 1 },
      }).addTo(this.offline);
      this.offlineLoaded = true;
    } catch {
      if (!this.destroyed) this.options.onBaseStatus?.("Não foi possível carregar a base offline. Recarregue a página.");
    } finally {
      this.offlineLoading = false;
    }
  }

  setEvents(events: readonly QuakeEvent[], selectedUid: string): void {
    const previous = this.selectedUid;
    this.selectedUid = selectedUid;
    let changed = previous !== selectedUid;
    const wanted = new Set(events.map((event) => event.earthquake_uid));
    for (const [uid, entry] of this.events) {
      if (!wanted.has(uid)) {
        this.eventLayer.removeLayer(entry.marker);
        this.events.delete(uid);
        changed = true;
      }
    }
    const center = this.map.getCenter().lng;
    for (const event of events) {
      const uid = event.earthquake_uid;
      const signature = eventSignature(event);
      const current = this.events.get(uid);
      if (current && current.signature === signature) {
        if (previous !== selectedUid && (uid === previous || uid === selectedUid)) this.styleEvent(current);
        continue;
      }
      changed = true;
      if (current) {
        current.event = event;
        current.signature = signature;
        current.marker.setLatLng([event.lat, near(event.lon, center)])
          .setRadius(3.5 + Math.max(0, event.mag ?? 0) * 1.2).setTooltipContent(esc(eventLabel(event)));
        this.styleEvent(current);
      } else {
        const marker = L.circleMarker([event.lat, near(event.lon, center)], {
          renderer: this.eventRenderer, radius: 3.5 + Math.max(0, event.mag ?? 0) * 1.2,
        }).bindTooltip(esc(eventLabel(event))).on("click", () => this.options.onSelect(uid)).addTo(this.eventLayer);
        const entry = { signature, event, marker };
        this.events.set(uid, entry);
        this.styleEvent(entry);
      }
    }
    const selected = this.events.get(selectedUid);
    if (selected && changed) selected.marker.bringToFront();
  }

  private styleEvent(entry: EventMarker): void {
    const selected = entry.event.earthquake_uid === this.selectedUid;
    entry.marker.setStyle({
      color: selected ? "#183c4d" : this.colors.event, weight: selected ? 3 : 1.3,
      fillColor: this.colors.event, fillOpacity: entry.event.lifecycle === "RETRACTED" ? 0 : selected ? 0.5 : 0.18,
      dashArray: entry.event.lifecycle === "RETRACTED" ? "3 3" : undefined,
    });
  }

  setAssets(assets: readonly Asset[], publicationId: string, mode: Mode, event?: QuakeEvent): void {
    this.assets = assets;
    this.selectedEvent = event;
    if (this.groups.set(assets, publicationId, event?.lon ?? assets[0]?.lon ?? 0)) this.renderedGroups = null;
    if (this.radius) { this.radius.remove(); this.radius = null; }
    if (mode === "RADIUS_TRIAGE" && event) {
      this.radius = L.circle([event.lat, near(event.lon, this.map.getCenter().lng)], {
        pane: "radius", radius: 100_000, color: this.colors.qualifying, weight: 1.5,
        dashArray: "6 6", fillColor: this.colors.qualifying, fillOpacity: 0.045,
        interactive: false,
      }).addTo(this.map);
    }
    this.drawAssets();
  }

  private drawAssets(): void {
    const groups = this.groups.get(this.map.getZoom());
    if (this.renderedGroups === groups) return;
    this.renderedGroups = groups;
    this.assetLayer.clearLayers();
    const center = this.map.getCenter().lng;
    this.assetMarkers = groups.map((group) => {
      const count = group.assets.length;
      const color = group.qualifying > 0 ? this.colors.qualifying : group.eligible > 0 ? this.colors.eligible : this.colors.other;
      const marker = L.circleMarker([group.lat, near(group.lon, center)], {
        renderer: this.assetRenderer, radius: count > 1 ? Math.min(23, 10 + Math.log2(count) * 1.7) : group.qualifying > 0 ? 5 : 3.5,
        color: "#fff", weight: count > 1 ? 1.5 : 0.6, fillColor: color, fillOpacity: count > 1 ? 0.94 : 0.85,
      }).addTo(this.assetLayer);
      if (count === 1) marker.bindTooltip(esc(assetLabel(group.assets[0])));
      else {
        const label = `${number.format(count)} ativos · ${number.format(group.qualifying)} classificados · ${number.format(group.eligible)} elegíveis. Clique para aproximar.`;
        marker.bindTooltip(`<span title="${esc(label)}" aria-label="${esc(label)}">${number.format(count)}</span>`, {
          permanent: true, direction: "center", className: "map-cluster-count", opacity: 1,
        });
        marker.on("click", () => {
          const anchor = near(group.lon, this.map.getCenter().lng);
          const bounds = L.latLngBounds(group.assets.map((asset) => [asset.lat, near(asset.lon, anchor)]));
          this.map.fitBounds(bounds, { padding: [45, 45], maxZoom: 13, animate: this.animate });
        });
      }
      return { group, marker };
    });
    this.options.onDensityChange?.({ assets: this.assets.length, groups: groups.length,
      clustered: groups.some((group) => group.assets.length > 1) });
  }

  private rewrap(): void {
    const center = this.map.getCenter().lng;
    for (const { event, marker } of this.events.values()) this.wrapMarker(marker, event.lat, event.lon, center);
    for (const { group, marker } of this.assetMarkers) this.wrapMarker(marker, group.lat, group.lon, center);
    if (this.radius && this.selectedEvent) {
      this.wrapMarker(this.radius, this.selectedEvent.lat, this.selectedEvent.lon, center);
    }
  }

  private wrapMarker(marker: L.CircleMarker, lat: number, lon: number, center: number): void {
    const wrapped = near(lon, center);
    if (marker.getLatLng().lng !== wrapped) marker.setLatLng([lat, wrapped]);
  }

  focusEvent(event: QuakeEvent): void {
    this.map.flyTo([event.lat, near(event.lon, this.map.getCenter().lng)], Math.max(7, Math.min(10, this.map.getZoom())), {
      animate: this.animate, duration: 0.6,
    });
  }

  focusAsset(asset: Asset): void {
    this.map.setView([asset.lat, near(asset.lon, this.map.getCenter().lng)], Math.max(12, this.map.getZoom()), {
      animate: false,
    });
    this.drawAssets();
    this.assetMarkers.find(({ group }) => group.assets.length === 1 && group.assets[0].asset_id === asset.asset_id)?.marker.openTooltip();
  }

  fitAssets(): void {
    if (!this.assets.length) {
      if (this.selectedEvent) this.focusEvent(this.selectedEvent);
      return;
    }
    const anchor = near(this.selectedEvent?.lon ?? this.assets[0].lon, this.map.getCenter().lng);
    this.map.fitBounds(L.latLngBounds(this.assets.map((asset) => [asset.lat, near(asset.lon, anchor)])), {
      padding: [36, 36], maxZoom: 13, animate: this.animate,
    });
  }

  clearAssets(): void {
    this.assets = [];
    this.selectedEvent = undefined;
    this.assetMarkers = [];
    this.groups.clear();
    this.renderedGroups = null;
    this.assetLayer.clearLayers();
    if (this.radius) { this.radius.remove(); this.radius = null; }
    this.options.onDensityChange?.({ assets: 0, groups: 0, clustered: false });
  }

  destroy(): void {
    this.destroyed = true;
    this.resize.disconnect();
    cancelAnimationFrame(this.resizeFrame);
    this.map.remove();
  }
}
