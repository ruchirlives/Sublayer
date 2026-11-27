import type { InstancerSettings, Transform } from "./fileRegistry";
export type Vec3 = [number, number, number];
export type InstancerHandle = {
  nodeId: string;
  id: string;
  path: string;
  sourcePath: string;
  origin: Vec3;
  translation: Vec3;
};
export type InstancerEditor = {
  nodeId: string;
  settings: InstancerSettings;
  enabled: boolean;
  selectedId: string | null;
  onSelect: (id: string | null) => void;
  onChange: (settings: InstancerSettings, group?: string) => void;
  onCommit: () => void;
  onPreviewNode: () => void;
};
export function movementLocks(
  settings: InstancerSettings,
  upAxis: "Y" | "Z",
): [boolean, boolean, boolean] {
  if (settings.movement === "custom") return settings.lockedAxes;
  if (settings.movement === "ground")
    return upAxis === "Z" ? [false, false, true] : [false, true, false];
  if (settings.movement === "free") return [false, false, false];
  return [
    settings.movement !== "x",
    settings.movement !== "y",
    settings.movement !== "z",
  ];
}
export function constrainTransform(
  next: Transform,
  start: Transform,
  locks: boolean[],
): Transform {
  return {
    ...next,
    translation: next.translation.map((v, i) =>
      locks[i] ? start.translation[i] : v,
    ) as Vec3,
  };
}
function project(
  p: number[],
  m: number[][],
  w: number,
  h: number,
): Vec3 | null {
  const v = [...p, 1];
  const c = [0, 1, 2, 3].map((j) => v.reduce((a, x, i) => a + x * m[i][j], 0));
  if (Math.abs(c[3]) < 1e-10) return null;
  return [
    ((c[0] / c[3] + 1) * w) / 2,
    ((1 - c[1] / c[3]) * h) / 2,
    c[2] / c[3],
  ];
}
function inverse(m: number[][]): number[][] | null {
  const a = m.map((row, i) => [
    ...row,
    ...[0, 1, 2, 3].map((j) => (i === j ? 1 : 0)),
  ]);
  for (let i = 0; i < 4; i++) {
    let k = i;
    for (let j = i + 1; j < 4; j++)
      if (Math.abs(a[j][i]) > Math.abs(a[k][i])) k = j;
    if (Math.abs(a[k][i]) < 1e-12) return null;
    [a[i], a[k]] = [a[k], a[i]];
    const d = a[i][i];
    a[i] = a[i].map((v) => v / d);
    for (let j = 0; j < 4; j++)
      if (i !== j) {
        const f = a[j][i];
        a[j] = a[j].map((v, k) => v - f * a[i][k]);
      }
  }
  return a.map((row) => row.slice(4));
}
function unproject(
  x: number,
  y: number,
  z: number,
  inv: number[][],
  w: number,
  h: number,
): Vec3 {
  const v = [(2 * x) / w - 1, 1 - (2 * y) / h, z, 1];
  const c = [0, 1, 2, 3].map((j) =>
    v.reduce((a, n, i) => a + n * inv[i][j], 0),
  );
  return c.slice(0, 3).map((n) => n / c[3]) as Vec3;
}
export function placementDelta(
  matrix: number[][],
  width: number,
  height: number,
  start: Vec3,
  from: [number, number],
  to: [number, number],
  locks: boolean[],
): Vec3 {
  const axes = [0, 1, 2].filter((i) => !locks[i]);
  if (!axes.length) return [0, 0, 0];
  const inv = inverse(matrix);
  if (inv && axes.length >= 2) {
    const ray = (xy: [number, number]) => {
      const a = unproject(...xy, -1, inv, width, height),
        b = unproject(...xy, 1, inv, width, height);
      return { a, d: b.map((v, i) => v - a[i]) };
    };
    const r0 = ray(from),
      r1 = ray(to);
    const normal =
      axes.length === 2 ? [0, 1, 2].map((i) => (locks[i] ? 1 : 0)) : r0.d;
    const hit = (r: ReturnType<typeof ray>) => {
      const den = r.d.reduce((s, v, i) => s + v * normal[i], 0);
      const cos = Math.abs(den) / (Math.hypot(...r.d) * Math.hypot(...normal));
      if (cos < 0.02) return null;
      const t =
        r.a.reduce((s, v, i) => s + (start[i] - v) * normal[i], 0) / den;
      return r.a.map((v, i) => v + t * r.d[i]);
    };
    const a = hit(r0),
      b = hit(r1);
    if (a && b) return b.map((v, i) => (locks[i] ? 0 : v - a[i])) as Vec3;
  }
  // Damped screen-space solve handles edge-on planes and view-parallel axes.
  const p = project(start, matrix, width, height);
  if (!p) return [0, 0, 0];
  const cols = axes.map((i) => {
    const q = [...start];
    q[i] += 1;
    const v = project(q, matrix, width, height);
    return v ? [v[0] - p[0], v[1] - p[1]] : [0, 0];
  });
  const aa = cols.reduce((s, c) => s + c[0] * c[0], 0) + 1e-3,
    bb = cols.reduce((s, c) => s + c[0] * c[1], 0),
    dd = cols.reduce((s, c) => s + c[1] * c[1], 0) + 1e-3;
  const det = aa * dd - bb * bb,
    dx = to[0] - from[0],
    dy = to[1] - from[1];
  const u = (dd * dx - bb * dy) / det,
    v = (aa * dy - bb * dx) / det;
  const out: Vec3 = [0, 0, 0];
  axes.forEach((axis, i) => {
    out[axis] = cols[i][0] * u + cols[i][1] * v;
  });
  return out;
}
