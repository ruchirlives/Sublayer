export const DEFAULT_METERS_PER_UNIT = 1.0;

export type SceneBounds = {
  center: [number, number, number];
  size: [number, number, number];
  radius: number;
};

export type LightUnitScalable = {
  translate?: [number, number, number] | null;
  width?: number | null;
  height?: number | null;
  radius?: number | null;
};

export function normalizeMetersPerUnit(metersPerUnit?: number): number {
  if (typeof metersPerUnit === "number" && Number.isFinite(metersPerUnit) && metersPerUnit > 0) {
    return metersPerUnit;
  }
  return DEFAULT_METERS_PER_UNIT;
}

export function roundUnitValue(value: number): number {
  const rounded = Number(value.toFixed(4));
  return Object.is(rounded, -0) ? 0 : rounded;
}

export function metersToStageUnitsFactor(metersPerUnit?: number): number {
  return 1.0 / normalizeMetersPerUnit(metersPerUnit);
}

/**
 * Effective linear scale for creating new lights or applying built-in meter-scale
 * studio presets: uses the visible scene bounding radius when available (built-in
 * presets are authored around a ~1 m radius subject centered at [0, 1, 0]), and
 * otherwise falls back to 1 / stageMetersPerUnit.
 */
export function effectiveLightScale(metersPerUnit?: number, sceneBounds?: SceneBounds | null): number {
  if (sceneBounds && Number.isFinite(sceneBounds.radius) && sceneBounds.radius > 1e-4) {
    return Math.max(sceneBounds.radius, 1e-3);
  }
  return metersToStageUnitsFactor(metersPerUnit);
}

export function presetScaleFactor(
  presetMetersPerUnit: number | undefined,
  stageMetersPerUnit: number | undefined,
  isBuiltin?: boolean,
  sceneBounds?: SceneBounds | null,
): number {
  if (isBuiltin) {
    return effectiveLightScale(stageMetersPerUnit, sceneBounds);
  }
  const targetMpu = normalizeMetersPerUnit(stageMetersPerUnit);
  if (typeof presetMetersPerUnit === "number" && Number.isFinite(presetMetersPerUnit) && presetMetersPerUnit > 0) {
    return presetMetersPerUnit / targetMpu;
  }
  return 1.0;
}

export function scaleLightItemForStage<T extends LightUnitScalable>(
  item: T,
  factor: number,
  sceneBounds?: SceneBounds | null,
  recenterBuiltin = false,
): T {
  const safeFactor = Number.isFinite(factor) && factor > 0 ? factor : 1.0;
  const next: T = { ...item };
  if (item.translate) {
    if (recenterBuiltin && sceneBounds) {
      const [cx, cy, cz] = sceneBounds.center;
      next.translate = [
        roundUnitValue(cx + item.translate[0] * safeFactor),
        roundUnitValue(cy + (item.translate[1] - 1.0) * safeFactor),
        roundUnitValue(cz + item.translate[2] * safeFactor),
      ];
    } else {
      next.translate = [
        roundUnitValue(item.translate[0] * safeFactor),
        roundUnitValue(item.translate[1] * safeFactor),
        roundUnitValue(item.translate[2] * safeFactor),
      ];
    }
  }
  if (typeof item.width === "number" && Number.isFinite(item.width)) {
    next.width = roundUnitValue(item.width * safeFactor);
  }
  if (typeof item.height === "number" && Number.isFinite(item.height)) {
    next.height = roundUnitValue(item.height * safeFactor);
  }
  if (typeof item.radius === "number" && Number.isFinite(item.radius)) {
    next.radius = roundUnitValue(item.radius * safeFactor);
  }
  return next;
}

export function formatStageUnitLabel(metersPerUnit?: number): string {
  const mpu = normalizeMetersPerUnit(metersPerUnit);
  const close = (a: number, b: number) => Math.abs(a - b) <= Math.max(a, b) * 1e-3;
  if (close(mpu, 1.0)) return "m";
  if (close(mpu, 0.01)) return "cm";
  if (close(mpu, 0.001)) return "mm";
  if (close(mpu, 0.0254)) return "in";
  if (close(mpu, 0.3048)) return "ft";
  return `${Number(mpu.toPrecision(3))} m/u`;
}

export function linearInputStep(metersPerUnit?: number, sceneBounds?: SceneBounds | null): number {
  const scale = effectiveLightScale(metersPerUnit, sceneBounds);
  // Pick a clean power-of-10 step roughly 5-10% of the subject radius (0.1 for 1m, 10 for 100m, etc.)
  const raw = scale * 0.1;
  const exp = Math.floor(Math.log10(Math.max(raw, 1e-4)));
  const mag = Math.pow(10, exp);
  const norm = raw / mag;
  const nice = norm < 1.5 ? 1 : norm < 3.5 ? 2 : norm < 7.5 ? 5 : 10;
  return roundUnitValue(nice * mag);
}
