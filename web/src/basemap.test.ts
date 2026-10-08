import { describe, expect, it } from "vitest";
import { planar } from "./basemap";

const maxJump = (ring: number[][]): number =>
  Math.max(...ring.slice(1).map((p, i) => Math.abs(p[0] - ring[i][0])));

describe("planar", () => {
  it("não mexe em polígono longe do antimeridiano", () => {
    const square = [[[10, 0], [11, 0], [11, 1], [10, 1], [10, 0]]];
    expect(planar([square])).toEqual([square]);
  });

  it("desenrola o anel que cruza ±180 e repete do outro lado", () => {
    const fiji = [[[179, -17], [-179, -17], [-179, -16], [179, -16], [179, -17]]];
    expect(planar([fiji])).toEqual([
      [[[179, -17], [181, -17], [181, -16], [179, -16], [179, -17]]],
      [[[-181, -17], [-179, -17], [-179, -16], [-181, -16], [-181, -17]]],
    ]);
  });

  it("fecha pelo polo o anel que dá a volta no globo", () => {
    const antarctica = [[[-170, -80], [-60, -80], [60, -80], [170, -80], [-170, -80]]];
    const [first] = planar([antarctica]);
    const ring = first[0];
    expect(ring.slice(-4)).toEqual([[190, -80], [190, -90], [-170, -90], [-170, -80]]);
    expect(maxJump(ring.slice(0, 5))).toBeLessThanOrEqual(180);
  });
});
