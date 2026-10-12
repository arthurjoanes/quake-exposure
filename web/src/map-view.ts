import * as L from "leaflet";
import { feature } from "topojson-client";
import type { Asset, Mode, QuakeEvent } from "./api";
import { near, planar } from "./basemap";
import { AssetGroupCache, eventSignature, type AssetGroup } from "./map-data";
import { assetMoved, classificationChange, comparisonCoordinates, type MapComparisonChange } from "./map-comparison";
import type { RegionCluster, RegionResponse } from "./map-region";
import { formatMinor } from "./money";
import { areaValidationError, normalizeViewport, unwrapArea, type AreaPolygon, type LonLat, type MapViewport } from "./spatial";

export interface MapColors { qualifying: string; eligible: string; other: string; event: string }
export interface MapDensity { assets: number; groups: number; clustered: boolean }
export interface AreaSelectionStatus { drawing: boolean; vertices: number; message: string }
export interface QuakeMapOptions {
  colors?: MapColors;
  onSelect: (uid: string) => void;
  onBaseStatus?: (message: string) => void;
  onDensityChange?: (density: MapDensity) => void;
  onAreaChange?: (polygon: AreaPolygon | null) => void;
  onAreaStatus?: (state: AreaSelectionStatus) => void;
  onViewportChange?: (viewport: MapViewport) => void;
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
const assetDescription = (asset: Asset): string =>
  `${formatMinor(asset.insured_value_minor, asset.currency)}`
  + (asset.mmi === null ? "" : ` · MMI ${asset.mmi.toFixed(2).replace(".", ",")}`)
  + ` · ${asset.qualifying ? "classificado" : asset.eligible ? "elegível" : "não elegível"}`;
const assetLabel = (asset: Asset): string => `${asset.asset_id} · ${assetDescription(asset)}`;

interface EventMarker { signature: string; event: QuakeEvent; marker: L.CircleMarker }
interface AssetMarker { group: AssetGroup; marker: L.CircleMarker }
interface RegionalMarker { cluster: RegionCluster; marker: L.CircleMarker }
interface ComparisonMarker { asset: Asset; anchor: number; marker: L.CircleMarker }
interface ComparisonLine { before: Asset; after: Asset; line: L.Polyline }

/** Camadas persistentes: polling e pan atualizam só os pontos que realmente mudaram. */
export class QuakeMap {
  private readonly map: L.Map;
  private readonly colors: MapColors;
  private readonly assetRenderer: L.Canvas;
  private readonly eventRenderer: L.Canvas;
  private readonly assetLayer: L.LayerGroup;
  private readonly eventLayer: L.LayerGroup;
  private readonly areaLayer: L.LayerGroup;
  private readonly comparisonLayer: L.LayerGroup;
  private readonly comparisonRenderer: L.Canvas;
  private readonly offline = L.layerGroup();
  private readonly groups = new AssetGroupCache();
  private readonly events = new Map<string, EventMarker>();
  private readonly resize: ResizeObserver;
  private readonly animate: boolean;
  private assetMarkers: AssetMarker[] = [];
  private focusedAsset: { asset: Asset; marker: L.CircleMarker } | null = null;
  private assetPublication = "";
  private regionalMarkers: RegionalMarker[] = [];
  private region: RegionResponse | null = null;
  private regionSignature = "";
  private renderedRegion = "";
  private comparisonMarkers: ComparisonMarker[] = [];
  private comparisonLines: ComparisonLine[] = [];
  private comparisonSignature = "";
  private area: [number, number][] | null = null;
  private draft: [number, number][] | null = null;
  private draftPath: L.Polyline | null = null;
  private doubleClickWasEnabled = true;
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
    this.map.createPane("comparison").style.zIndex = "420";
    this.map.createPane("area").style.zIndex = "450";
    this.assetRenderer = L.canvas({ pane: "assets", padding: 0.3 });
    this.eventRenderer = L.canvas({ pane: "events", padding: 0.3 });
    this.comparisonRenderer = L.canvas({ pane: "comparison", padding: 0.3 });
    this.assetLayer = L.layerGroup().addTo(this.map);
    this.eventLayer = L.layerGroup().addTo(this.map);
    this.areaLayer = L.layerGroup().addTo(this.map);
    this.comparisonLayer = L.layerGroup().addTo(this.map);
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
    this.map.on("moveend", () => {
      this.rewrap();
      this.options.onViewportChange?.(this.getViewport());
    });
    this.map.on("zoomend", () => this.drawAssets());
    this.map.on("click", (event: L.LeafletMouseEvent) => {
      if (this.draft !== null) this.addAreaVertex([event.latlng.lng, event.latlng.lat]);
    });
    this.map.on("mousemove", (event: L.LeafletMouseEvent) => {
      if (!this.draftPath || !this.draft?.length) return;
      const points = this.areaLatLngs(this.draft);
      const last = points[points.length - 1];
      this.draftPath.setLatLngs([...points, [event.latlng.lat, near(event.latlng.lng, last.lng)]]);
    });
    this.map.on("dblclick", (event: L.LeafletMouseEvent) => {
      if (this.draft !== null) {
        L.DomEvent.stop(event.originalEvent);
        this.finishAreaSelection();
      }
    });
    this.map.getContainer().addEventListener("keydown", this.areaKeydown, true);
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
        }).bindTooltip(esc(eventLabel(event))).on("click", () => {
          if (this.draft === null) this.options.onSelect(uid);
        }).addTo(this.eventLayer);
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
    const publication = publicationId.split(":region:")[0].split("|")[0];
    if (publication !== this.assetPublication) this.clearFocusedAsset();
    if (publicationId.includes("|") && this.focusedAsset && !assets.some(asset => asset.asset_id === this.focusedAsset!.asset.asset_id)) this.clearFocusedAsset();
    this.assetPublication = publication;
    this.region = null;
    this.regionSignature = "";
    this.renderedRegion = "";
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

  /** A chave regional muda quando outro conjunto da mesma publicação entra no mapa. */
  setRegionalAssets(assets: readonly Asset[], publicationId: string, mode: Mode, event?: QuakeEvent, regionKey?: string): void {
    this.setAssets(assets, `${publicationId}:region:${regionKey ?? assets.map((asset) => asset.asset_id).join("|")}`, mode, event);
  }

  setRegion(response: RegionResponse, publicationId: string, mode: Mode, event?: QuakeEvent): void {
    if (response.publication_id !== publicationId) return;
    const signature = JSON.stringify(response);
    if (this.regionSignature === signature) return;
    this.setRegionalAssets(response.assets, publicationId, mode, event,
      JSON.stringify([response.bounds, response.zoom, response.representation, response.assets.map((asset) => asset.asset_id)]));
    if (response.representation === "clusters") {
      this.region = response;
      this.regionSignature = signature;
      this.renderedGroups = null;
      this.drawAssets();
    }
  }

  private drawAssets(): void {
    if (this.region?.representation === "clusters") {
      this.drawRegionalClusters(this.region);
      return;
    }
    const groups = this.groups.get(this.map.getZoom());
    if (this.renderedGroups === groups) return;
    this.renderedGroups = groups;
    this.assetLayer.clearLayers();
    this.regionalMarkers = [];
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
          if (this.draft !== null) return;
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
    for (const { cluster, marker } of this.regionalMarkers) this.wrapMarker(marker, cluster.lat, cluster.lon, center);
    if (this.focusedAsset) this.wrapMarker(this.focusedAsset.marker, this.focusedAsset.asset.lat, this.focusedAsset.asset.lon, center);
    for (const { asset, anchor, marker } of this.comparisonMarkers) this.wrapMarker(marker, asset.lat, asset.lon, near(anchor, center));
    for (const { before, after, line } of this.comparisonLines) {
      const first = near(before.lon, center);
      line.setLatLngs([[before.lat, first], [after.lat, near(after.lon, first)]]);
    }
    if (this.area || this.draft) this.drawArea();
    if (this.radius && this.selectedEvent) {
      this.wrapMarker(this.radius, this.selectedEvent.lat, this.selectedEvent.lon, center);
    }
  }

  getViewport(): MapViewport {
    const bounds = this.map.getBounds();
    return normalizeViewport(bounds.getWest(), bounds.getSouth(), bounds.getEast(), bounds.getNorth(), this.map.getZoom());
  }

  private drawRegionalClusters(response: RegionResponse): void {
    if (this.renderedRegion === this.regionSignature) return;
    this.renderedRegion = this.regionSignature;
    this.assetLayer.clearLayers();
    this.assetMarkers = [];
    const center = this.map.getCenter().lng;
    this.regionalMarkers = response.clusters.map((cluster) => {
      const color = cluster.qualifying_count > 0 ? this.colors.qualifying
        : cluster.eligible_count > 0 ? this.colors.eligible : this.colors.other;
      const marker = L.circleMarker([cluster.lat, near(cluster.lon, center)], {
        renderer: this.assetRenderer, radius: Math.min(24, 10 + Math.log2(Math.max(1, cluster.count)) * 1.7),
        color: "#fff", weight: 1.5, fillColor: color, fillOpacity: 0.94,
      }).addTo(this.assetLayer);
      const label = `${number.format(cluster.count)} ativos · ${number.format(cluster.qualifying_count)} classificados · ${number.format(cluster.eligible_count)} elegíveis`;
      marker.bindTooltip(`<span title="${esc(label)}" aria-label="${esc(label)}">${number.format(cluster.count)}</span>`, {
        permanent: true, direction: "center", className: "map-cluster-count", opacity: 1,
      });
      const popup = document.createElement("div");
      popup.className = "map-region-popup";
      const moneyRows = Object.keys(cluster.insured_value_minor).sort().map((currency) =>
        `<tr><th>${esc(currency)}</th><td>${esc(formatMinor(cluster.insured_value_minor[currency], currency))}</td>`
        + `<td>${esc(formatMinor(cluster.qualifying_value_minor[currency] ?? "0", currency))}</td></tr>`).join("");
      popup.innerHTML = `<p>${esc(label)}</p><table><thead><tr><th>Moeda</th><th>Carteira</th><th>Classificados</th></tr></thead>`
        + `<tbody>${moneyRows}</tbody></table>`;
      const zoom = document.createElement("button");
      zoom.type = "button";
      zoom.textContent = "Aproximar esta região";
      zoom.addEventListener("click", () => {
        this.map.closePopup();
        this.map.setView([cluster.lat, near(cluster.lon, this.map.getCenter().lng)], Math.min(18, this.map.getZoom() + 2), {
          animate: this.animate,
        });
      });
      popup.append(zoom);
      marker.bindPopup(popup).off("click").on("click", () => {
        if (this.draft === null) marker.openPopup();
      });
      return { cluster, marker };
    });
    this.options.onDensityChange?.({ assets: response.total_count, groups: response.clusters.length, clustered: true });
  }

  getAreaSelection(): AreaPolygon | null {
    return this.area?.map(([lon, lat]) => [lon, lat] as const) ?? null;
  }

  startAreaSelection(): void {
    if (this.draft !== null) return;
    this.map.stop();
    this.draft = [];
    this.doubleClickWasEnabled = this.map.doubleClickZoom.enabled();
    this.map.doubleClickZoom.disable();
    this.map.closePopup();
    this.map.getContainer().style.cursor = "crosshair";
    this.map.getContainer().focus();
    this.drawArea();
    this.areaStatus("Clique nos pontos da área. Enter fecha; Backspace desfaz; Esc cancela. Use Espaço para marcar o centro do mapa.");
  }

  private addAreaVertex(point: LonLat): void {
    if (!this.draft) return;
    this.map.closePopup();
    if (this.draft.length >= 200) { this.areaStatus("A área aceita até 200 pontos. Feche a seleção ou desfaça o último ponto."); return; }
    const last = this.draft[this.draft.length - 1];
    const vertex: [number, number] = [last ? near(point[0], last[0]) : point[0], point[1]];
    if (last && Math.abs(last[0] - vertex[0]) < 1e-9 && Math.abs(last[1] - vertex[1]) < 1e-9) return;
    this.draft.push(vertex);
    this.drawArea();
    const count = this.draft.length;
    this.areaStatus(`${number.format(count)} ${count === 1 ? "ponto" : "pontos"}. `
      + (count < 3 ? "Marque pelo menos três pontos para fechar." : "Clique no primeiro ponto ou pressione Enter para fechar."));
  }

  finishAreaSelection(): boolean {
    if (this.draft === null) return false;
    const error = areaValidationError(this.draft);
    if (error) { this.areaStatus(error); return false; }
    this.area = unwrapArea(this.draft);
    this.stopDrawing();
    this.drawArea();
    this.options.onAreaChange?.(this.getAreaSelection());
    this.areaStatus("Área selecionada. Arraste os pontos para ajustar; nas teclas de seta, cada ponto move 2 pixels.");
    return true;
  }

  undoAreaVertex(): void {
    if (this.draft === null) return;
    this.draft.pop();
    this.drawArea();
    this.areaStatus(`${number.format(this.draft.length)} pontos. Marque outro ponto para continuar.`);
  }

  cancelAreaSelection(): void {
    if (this.draft === null) return;
    this.stopDrawing();
    this.drawArea();
    this.areaStatus(this.area ? "Desenho cancelado. A área anterior foi mantida." : "Desenho cancelado.");
  }

  clearAreaSelection(): void {
    const hadArea = this.area !== null;
    this.stopDrawing();
    this.area = null;
    this.areaLayer.clearLayers();
    this.draftPath = null;
    if (hadArea) this.options.onAreaChange?.(null);
    this.areaStatus("Selecione uma área para filtrar os ativos.");
  }

  private stopDrawing(): void {
    if (this.draft !== null && this.doubleClickWasEnabled) this.map.doubleClickZoom.enable();
    this.draft = null;
    this.map.getContainer().style.cursor = "";
  }

  private areaStatus(message: string): void {
    this.options.onAreaStatus?.({ drawing: this.draft !== null, vertices: (this.draft ?? this.area ?? []).length, message });
  }

  private areaLatLngs(points: AreaPolygon): L.LatLng[] {
    if (!points.length) return [];
    const shift = near(points[0][0], this.map.getCenter().lng) - points[0][0];
    return points.map(([lon, lat]) => L.latLng(lat, lon + shift));
  }

  private drawArea(): void {
    const focused = document.activeElement instanceof HTMLElement
      && this.map.getContainer().contains(document.activeElement) ? document.activeElement.dataset.areaVertex : undefined;
    this.areaLayer.clearLayers();
    this.draftPath = null;
    const points = this.draft ?? this.area;
    if (!points?.length) return;
    const latLngs = this.areaLatLngs(points);
    if (this.draft !== null) {
      this.draftPath = L.polyline(latLngs, { pane: "area", color: "#14344b", weight: 2, dashArray: "5 5", interactive: false }).addTo(this.areaLayer);
    } else {
      L.polygon(latLngs, { pane: "area", color: "#14344b", weight: 2, fillColor: this.colors.eligible, fillOpacity: 0.08,
        interactive: false }).addTo(this.areaLayer);
    }
    latLngs.forEach((point, index) => {
      const handle = L.marker(point, {
        pane: "area", draggable: this.draft === null, keyboard: true,
        title: this.draft === null ? `Ponto ${index + 1} da área. Arraste ou use as setas para ajustar.`
          : `Ponto ${index + 1} da área.${index === 0 ? " Clique para fechar depois de marcar três pontos." : ""}`,
        icon: L.divIcon({ className: "map-area-vertex", iconSize: [16, 16], iconAnchor: [8, 8],
          html: '<span style="display:block;width:14px;height:14px;border:2px solid #14344b;border-radius:50%;background:#fff;box-sizing:border-box"></span>' }),
      }).addTo(this.areaLayer);
      const element = handle.getElement();
      if (element) {
        element.dataset.areaVertex = String(index);
        element.setAttribute("aria-label", `Ponto ${index + 1} da área`);
      }
      handle.on("click", (event: L.LeafletMouseEvent) => {
        L.DomEvent.stopPropagation(event.originalEvent);
        if (this.draft !== null && index === 0) this.finishAreaSelection();
      });
      handle.on("dragend", () => this.moveAreaVertex(index, handle.getLatLng()));
    });
    if (focused !== undefined) this.map.getContainer().querySelector<HTMLElement>(`[data-area-vertex="${focused}"]`)?.focus();
  }

  private moveAreaVertex(index: number, point: L.LatLng): void {
    if (!this.area) return;
    const next = this.area.map(([lon, lat]) => [lon, lat] as [number, number]);
    next[index] = [near(point.lng, this.area[index][0]), point.lat];
    const error = areaValidationError(next);
    if (!error) { this.area = unwrapArea(next); this.options.onAreaChange?.(this.getAreaSelection()); }
    this.drawArea();
    this.areaStatus(error ?? "Área ajustada.");
  }

  private readonly areaKeydown = (event: KeyboardEvent): void => {
    const target = event.target instanceof HTMLElement ? event.target : null;
    if (target?.closest("input, select, textarea, button") || event.ctrlKey || event.metaKey || event.altKey) return;
    const index = target?.dataset.areaVertex === undefined ? null : Number(target.dataset.areaVertex);
    if (this.draft !== null && ["Escape", "Enter", "Backspace", " "].includes(event.key)) {
      event.preventDefault();
      event.stopImmediatePropagation();
      if (event.key === "Escape") this.cancelAreaSelection();
      else if (event.key === "Enter") this.finishAreaSelection();
      else if (event.key === "Backspace") this.undoAreaVertex();
      else { const center = this.map.getCenter(); this.addAreaVertex([center.lng, center.lat]); }
      this.map.getContainer().focus();
    } else if (this.area && index !== null && ["ArrowLeft", "ArrowRight", "ArrowUp", "ArrowDown"].includes(event.key)) {
      event.preventDefault();
      event.stopImmediatePropagation();
      const [lon, lat] = this.area[index];
      const amount = event.shiftKey ? 10 : 2;
      const offset = L.point(event.key === "ArrowLeft" ? -amount : event.key === "ArrowRight" ? amount : 0,
        event.key === "ArrowUp" ? -amount : event.key === "ArrowDown" ? amount : 0);
      this.moveAreaVertex(index, this.map.containerPointToLatLng(this.map.latLngToContainerPoint([lat, near(lon, this.map.getCenter().lng)]).add(offset)));
      this.map.getContainer().querySelector<HTMLElement>(`[data-area-vertex="${index}"]`)?.focus();
    }
  };

  setComparison(changes: readonly MapComparisonChange[]): void {
    const signature = JSON.stringify(changes);
    if (signature === this.comparisonSignature) return;
    this.clearComparison();
    this.comparisonSignature = signature;
    const center = this.map.getCenter().lng;
    const position = (asset: Asset): string => `lon ${asset.lon.toFixed(5).replace(".", ",")}, lat ${asset.lat.toFixed(5).replace(".", ",")}`;
    for (const change of changes) {
      const coordinates = comparisonCoordinates(change, center);
      const anchor = change.before?.lon ?? change.after?.lon ?? center;
      const classification = classificationChange(change);
      const label = change.change === "added" ? "Ativo incluído" : change.change === "removed" ? "Ativo removido"
        : classification === "entered" ? "Entrou na classificação" : classification === "left" ? "Saiu da classificação" : "Ativo alterado";
      const color = classification === "entered" ? this.colors.qualifying : classification === "left" ? "#14344b" : this.colors.eligible;
      const popup = `<strong>${esc(change.asset_id)}</strong><p>${esc(label)}</p>`
        + (change.before ? `<p>Antes: ${esc(position(change.before))}<br>${esc(assetLabel(change.before))}</p>` : "<p>Antes: ausente nesta publicação.</p>")
        + (change.after ? `<p>Depois: ${esc(position(change.after))}<br>${esc(assetLabel(change.after))}</p>` : "<p>Depois: ausente nesta publicação.</p>");
      if (assetMoved(change) && change.before && change.after) {
        const line = L.polyline([[coordinates.before![1], coordinates.before![0]], [coordinates.after![1], coordinates.after![0]]], {
          renderer: this.comparisonRenderer, color, weight: 2, dashArray: "5 4", interactive: false,
        }).addTo(this.comparisonLayer);
        this.comparisonLines.push({ before: change.before, after: change.after, line });
      }
      for (const [when, asset, point] of [["before", change.before, coordinates.before], ["after", change.after, coordinates.after]] as const) {
        if (!asset || !point) continue;
        const marker = L.circleMarker([point[1], point[0]], {
          renderer: this.comparisonRenderer, radius: when === "before" ? 8 : 5,
          color, weight: when === "before" ? 2 : 1.5, fillColor: color, fillOpacity: when === "before" ? 0 : 0.95,
          dashArray: when === "before" ? "3 3" : undefined,
        }).bindTooltip(esc(`${change.asset_id} · ${when === "before" ? "antes" : "depois"} · ${label.toLowerCase()}`))
          .bindPopup(popup).addTo(this.comparisonLayer);
        marker.off("click").on("click", () => {
          if (this.draft === null) marker.openPopup();
        });
        this.comparisonMarkers.push({ asset, anchor, marker });
      }
    }
  }

  clearComparison(): void {
    this.comparisonLayer.clearLayers();
    this.comparisonMarkers = [];
    this.comparisonLines = [];
    this.comparisonSignature = "";
  }

  fitComparison(): void {
    if (!this.comparisonMarkers.length || this.draft !== null) return;
    const first = this.comparisonMarkers[0].asset;
    const anchor = near(first.lon, this.map.getCenter().lng);
    this.map.fitBounds(L.latLngBounds(this.comparisonMarkers.map(({ asset }) => [asset.lat, near(asset.lon, anchor)])), {
      padding: [45, 45], maxZoom: 13, animate: this.animate,
    });
  }

  private wrapMarker(marker: L.CircleMarker, lat: number, lon: number, center: number): void {
    const wrapped = near(lon, center);
    if (marker.getLatLng().lng !== wrapped) marker.setLatLng([lat, wrapped]);
  }

  focusEvent(event: QuakeEvent, animate = this.animate): void {
    this.map.flyTo([event.lat, near(event.lon, this.map.getCenter().lng)], Math.max(7, Math.min(10, this.map.getZoom())), {
      animate, duration: 0.6,
    });
  }

  focusAsset(asset: Asset): void {
    if (this.draft !== null) return;
    this.clearFocusedAsset();
    this.map.setView([asset.lat, near(asset.lon, this.map.getCenter().lng)], Math.max(12, this.map.getZoom()), {
      animate: false,
    });
    const color = asset.qualifying ? this.colors.qualifying : asset.eligible ? this.colors.eligible : this.colors.other;
    const marker = L.circleMarker([asset.lat, near(asset.lon, this.map.getCenter().lng)], {
      renderer: this.comparisonRenderer, radius: 9, color: "#14344b", weight: 2,
      fillColor: color, fillOpacity: 1,
    }).bindPopup(`<strong>${esc(asset.asset_id)}</strong><p>${esc(assetDescription(asset))}</p>`).addTo(this.map);
    this.focusedAsset = { asset, marker };
    marker.openPopup();
  }

  private clearFocusedAsset(): void {
    this.focusedAsset?.marker.remove();
    this.focusedAsset = null;
  }

  fitAssets(assets: readonly Asset[] = this.assets): void {
    if (!assets.length) {
      if (this.selectedEvent) this.focusEvent(this.selectedEvent);
      return;
    }
    const anchor = near(this.selectedEvent?.lon ?? assets[0].lon, this.map.getCenter().lng);
    this.map.fitBounds(L.latLngBounds(assets.map((asset) => [asset.lat, near(asset.lon, anchor)])), {
      padding: [36, 36], maxZoom: 13, animate: this.animate,
    });
  }

  clearAssets(): void {
    this.clearFocusedAsset();
    this.assetPublication = "";
    this.assets = [];
    this.selectedEvent = undefined;
    this.assetMarkers = [];
    this.regionalMarkers = [];
    this.region = null;
    this.regionSignature = "";
    this.renderedRegion = "";
    this.groups.clear();
    this.renderedGroups = null;
    this.assetLayer.clearLayers();
    this.clearComparison();
    this.clearAreaSelection();
    if (this.radius) { this.radius.remove(); this.radius = null; }
    this.options.onDensityChange?.({ assets: 0, groups: 0, clustered: false });
  }

  destroy(): void {
    this.destroyed = true;
    this.resize.disconnect();
    cancelAnimationFrame(this.resizeFrame);
    this.map.getContainer().removeEventListener("keydown", this.areaKeydown, true);
    this.map.remove();
  }
}
