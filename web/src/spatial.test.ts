import { describe, expect, it } from "vitest";
import type { Asset } from "./api";
import { areaValidationError, filterAssetsInPolygon, normalizeViewport, pointInPolygon, unwrapArea, type AreaPolygon } from "./spatial";

const square: AreaPolygon = [[10, 10], [20, 10], [20, 20], [10, 20]];
const asset = (asset_id: string, lon: number, lat: number): Asset => ({
  asset_id, lon, lat, eligible: true, qualifying: true, currency: "BRL", insured_value_minor: "90071992547409931", mmi: 6.5,
});

describe("seleção por área", () => {
  it("inclui interior, arestas e vértices sem incluir pontos do lado de fora", () => {
    for (const point of [[15, 15], [10, 15], [20, 15], [15, 10], [15, 20], [10, 10], [20, 20]] as const) {
      expect(pointInPolygon(point, square)).toBe(true);
      expect(pointInPolygon(point, [...square].reverse())).toBe(true);
    }
    for (const point of [[9.999, 15], [20.001, 15], [15, 9.999], [15, 20.001]] as const) {
      expect(pointInPolygon(point, square)).toBe(false);
    }
  });

  it("respeita a reentrância de um polígono côncavo", () => {
    const polygon: AreaPolygon = [[0, 0], [4, 0], [4, 4], [2, 2], [0, 4]];
    expect(pointInPolygon([2, 1], polygon)).toBe(true);
    expect(pointInPolygon([2, 3], polygon)).toBe(false);
    expect(pointInPolygon([2, 2], polygon)).toBe(true);
  });

  it("segue a diagonal desenhada no plano do mapa em latitudes altas", () => {
    // Em Mercator, a metade do caminho de 0° a 80° fica perto de 57°N.
    const triangle: AreaPolygon = [[0, 0], [10, 0], [10, 80]];
    expect(pointInPolygon([5, 50], triangle)).toBe(true);
    expect(pointInPolygon([5, 60], triangle)).toBe(false);
  });

  it("filtra ambos os lados do antimeridiano sem selecionar o outro lado do mundo", () => {
    const polygon: AreaPolygon = [[179, -18], [-179, -18], [-179, -16], [179, -16], [179, -18]];
    const assets = [asset("leste", 179.5, -17), asset("oeste", -179.5, -17), asset("borda", 180, -18),
      asset("fora", 178, -17), asset("greenwich", 0, -17), asset("norte", -179.5, -15)];
    const before = JSON.stringify(assets);
    expect(filterAssetsInPolygon(assets, polygon).map((entry) => entry.asset_id)).toEqual(["leste", "oeste", "borda"]);
    expect(filterAssetsInPolygon(assets, [...polygon].reverse()).map((entry) => entry.asset_id)).toEqual(["leste", "oeste", "borda"]);
    expect(JSON.stringify(assets)).toBe(before);
    expect(unwrapArea(polygon)).toEqual([[179, -18], [181, -18], [181, -16], [179, -16]]);
    expect(pointInPolygon([540, -17], polygon)).toBe(true);
  });

  it("aceita um anel fechado e recusa linhas, cruzamentos e coordenadas inválidas", () => {
    expect(areaValidationError([...square, square[0]])).toBeNull();
    const invalid: AreaPolygon[] = [[], [[0, 0], [1, 1]], [[0, 0], [1, 0], [2, 0]],
      [[0, 0], [2, 2], [0, 2], [2, 0]], [[0, 0], [2, 0], [0, 0], [0, 2]],
      [[0, 0], [Number.NaN, 0], [0, 2]], [[0, 91], [2, 0], [0, 2]],
      [[0, 0], [120, 0], [240, 1], [360, 1]]];
    for (const polygon of invalid) {
      expect(areaValidationError(polygon)).not.toBeNull();
      expect(pointInPolygon([1, 1], polygon)).toBe(false);
      expect(filterAssetsInPolygon([asset("a", 1, 1)], polygon)).toEqual([]);
    }
    expect(pointInPolygon([Infinity, 15], square)).toBe(false);
  });
});

describe("extensão do mapa", () => {
  it("representa travessia do antimeridiano com leste menor que oeste", () => {
    expect(normalizeViewport(170, -20, 190, 20, 4)).toEqual({ west: 170, south: -20, east: -170, north: 20, zoom: 4 });
    expect(normalizeViewport(-190, -20, -170, 20, 4)).toEqual({ west: 170, south: -20, east: -170, north: 20, zoom: 4 });
  });

  it("limita a extensão global e a latitude antes da consulta", () => {
    expect(normalizeViewport(-220, -100, 220, 100, 2)).toEqual({ west: -180, south: -90, east: 180, north: 90, zoom: 2 });
  });
});
