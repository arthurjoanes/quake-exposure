import type { Asset } from "./api";
import { near } from "./basemap";

export type LonLat = readonly [lon: number, lat: number];
export type AreaPolygon = readonly LonLat[];
export interface MapViewport { west: number; south: number; east: number; north: number; zoom: number }

const EPSILON = 1e-9;
const MAX_LATITUDE = 85.0511287798;
const finitePoint = ([lon, lat]: LonLat): boolean => Number.isFinite(lon) && Number.isFinite(lat)
  && lat >= -90 && lat <= 90;
const samePoint = (a: LonLat, b: LonLat): boolean => Math.abs(near(a[0], b[0]) - b[0]) <= EPSILON
  && Math.abs(a[1] - b[1]) <= EPSILON;

/** Cada aresta segue a cópia mais próxima da longitude anterior. */
export function unwrapArea(polygon: AreaPolygon): [number, number][] {
  const points = polygon.length > 1 && samePoint(polygon[0], polygon[polygon.length - 1])
    ? polygon.slice(0, -1) : polygon;
  const unwrapped: [number, number][] = [];
  for (const [lon, lat] of points) {
    unwrapped.push([unwrapped.length ? near(lon, unwrapped[unwrapped.length - 1][0]) : lon, lat]);
  }
  return unwrapped;
}

/** Mesmo plano do Leaflet: a borda filtrada coincide com a linha desenhada. */
function projectArea(points: AreaPolygon): [number, number][] {
  return points.map(([lon, lat]) => {
    const radians = Math.max(-MAX_LATITUDE, Math.min(MAX_LATITUDE, lat)) * Math.PI / 180;
    return [lon, Math.log(Math.tan(Math.PI / 4 + radians / 2)) * 180 / Math.PI];
  });
}

function cross(a: LonLat, b: LonLat, c: LonLat): number {
  return (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0]);
}

function onSegment(point: LonLat, a: LonLat, b: LonLat): boolean {
  const tolerance = EPSILON * Math.max(1, Math.abs(b[0] - a[0]), Math.abs(b[1] - a[1]));
  return Math.abs(cross(a, b, point)) <= tolerance
    && point[0] >= Math.min(a[0], b[0]) - EPSILON && point[0] <= Math.max(a[0], b[0]) + EPSILON
    && point[1] >= Math.min(a[1], b[1]) - EPSILON && point[1] <= Math.max(a[1], b[1]) + EPSILON;
}

function intersects(a: LonLat, b: LonLat, c: LonLat, d: LonLat): boolean {
  const abC = cross(a, b, c), abD = cross(a, b, d), cdA = cross(c, d, a), cdB = cross(c, d, b);
  return ((abC > 0 && abD < 0 || abC < 0 && abD > 0) && (cdA > 0 && cdB < 0 || cdA < 0 && cdB > 0))
    || onSegment(c, a, b) || onSegment(d, a, b) || onSegment(a, c, d) || onSegment(b, c, d);
}

/** Polígonos simples, sem furos, com borda inclusiva e arestas curtas no antimeridiano. */
export function areaValidationError(polygon: AreaPolygon): string | null {
  if (polygon.some((point) => !finitePoint(point))) return "A área contém coordenadas inválidas.";
  const points = projectArea(unwrapArea(polygon));
  if (points.length < 3) return "Marque pelo menos três pontos para fechar a área.";
  const first = points[0], last = points[points.length - 1];
  if (Math.abs(near(first[0], last[0]) - first[0]) > EPSILON) {
    return "Divida a seleção em áreas menores que uma volta no mundo.";
  }
  let twiceArea = 0;
  for (let i = 0; i < points.length; i++) {
    const a = points[i], b = points[(i + 1) % points.length];
    if (Math.abs(a[0] - b[0]) <= EPSILON && Math.abs(a[1] - b[1]) <= EPSILON) return "Use pontos distintos para desenhar a área.";
    twiceArea += a[0] * b[1] - b[0] * a[1];
    for (let j = i + 1; j < points.length; j++) {
      if (j === i + 1 || i === 0 && j === points.length - 1) continue;
      if (intersects(a, b, points[j], points[(j + 1) % points.length])) {
        return "As linhas da área se cruzam. Mova ou remova um ponto.";
      }
    }
  }
  return Math.abs(twiceArea) <= EPSILON ? "A área precisa ter largura e altura." : null;
}

function contains(point: LonLat, points: AreaPolygon): boolean {
  if (!finitePoint(point)) return false;
  const anchor = points.reduce((sum, p) => sum + p[0], 0) / points.length;
  const candidate: LonLat = [near(point[0], anchor), projectArea([point])[0][1]];
  let inside = false;
  for (let i = 0, j = points.length - 1; i < points.length; j = i++) {
    const a = points[j], b = points[i];
    if (onSegment(candidate, a, b)) return true;
    if ((a[1] > candidate[1]) !== (b[1] > candidate[1])
      && candidate[0] < (b[0] - a[0]) * (candidate[1] - a[1]) / (b[1] - a[1]) + a[0]) inside = !inside;
  }
  return inside;
}

export function pointInPolygon(point: LonLat, polygon: AreaPolygon): boolean {
  return areaValidationError(polygon) === null && contains(point, projectArea(unwrapArea(polygon)));
}

export function filterAssetsInPolygon(assets: readonly Asset[], polygon: AreaPolygon): Asset[] {
  if (areaValidationError(polygon) !== null) return [];
  const points = projectArea(unwrapArea(polygon));
  return assets.filter((asset) => contains([asset.lon, asset.lat], points));
}

export function normalizeViewport(west: number, south: number, east: number, north: number, zoom: number): MapViewport {
  const longitude = (lon: number): number => ((lon + 180) % 360 + 360) % 360 - 180;
  return { west: east - west >= 360 ? -180 : longitude(west),
    east: east - west >= 360 ? 180 : longitude(east),
    south: Math.max(-90, Math.min(90, south)), north: Math.max(-90, Math.min(90, north)), zoom };
}
