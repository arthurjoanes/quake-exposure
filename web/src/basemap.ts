// O world-atlas 2 vem costurado no antimeridiano (feito para projeção esférica). No plano do
// Leaflet, o anel que cruza ±180 vira uma faixa horizontal no mapa inteiro. Aqui cada anel é
// desenrolado (a longitude segue contínua além de ±180) e o polígono é repetido do outro lado.
// O anel que dá a volta no polo (Antártida) é fechado pelo polo.

type Ring = number[][];
type Polygon = Ring[];

const wrap = (d: number): number => (d > 180 ? d - 360 : d < -180 ? d + 360 : d);
const shift = (polygon: Polygon, dx: number): Polygon =>
  polygon.map((ring) => ring.map(([x, y]) => [x + dx, y]));

function unwrap(ring: Ring): Ring {
  const out: Ring = [];
  for (const [i, [x, y]] of ring.entries()) {
    out.push([i === 0 ? x : out[i - 1][0] + wrap(x - ring[i - 1][0]), y]);
  }
  const first = out[0];
  const last = out[out.length - 1];
  if (Math.abs(last[0] - first[0]) > 180) {
    const pole = first[1] < 0 ? -90 : 90;
    out.push([last[0], pole], [first[0], pole], first);
  }
  const mean = out.reduce((s, p) => s + p[0], 0) / out.length;
  const dx = mean > 180 ? -360 : mean < -180 ? 360 : 0;
  return dx ? out.map(([x, y]) => [x + dx, y]) : out;
}

export function planar(polygons: Polygon[]): Polygon[] {
  const out: Polygon[] = [];
  for (const polygon of polygons) {
    const rings = polygon.map(unwrap);
    const lons = rings.flat().map((p) => p[0]);
    out.push(rings);
    if (lons.some((x) => x > 180)) out.push(shift(rings, -360));
    if (lons.some((x) => x < -180)) out.push(shift(rings, 360));
  }
  return out;
}
