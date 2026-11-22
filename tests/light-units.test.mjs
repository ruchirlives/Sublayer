import test from "node:test";
import assert from "node:assert/strict";
import {
  effectiveLightScale,
  formatStageUnitLabel,
  linearInputStep,
  metersToStageUnitsFactor,
  presetScaleFactor,
  scaleLightItemForStage,
} from "../src/lightUnits.ts";

test("unit scale factor and labels match standard USD metersPerUnit values", () => {
  assert.equal(metersToStageUnitsFactor(1.0), 1);
  assert.equal(metersToStageUnitsFactor(0.01), 100);
  assert.equal(metersToStageUnitsFactor(0.001), 1000);
  assert.equal(metersToStageUnitsFactor(undefined), 1);

  assert.equal(formatStageUnitLabel(1.0), "m");
  assert.equal(formatStageUnitLabel(0.01), "cm");
  assert.equal(formatStageUnitLabel(0.001), "mm");
  assert.equal(formatStageUnitLabel(0.0254), "in");
  assert.equal(formatStageUnitLabel(0.3048), "ft");

  assert.equal(linearInputStep(1.0), 0.1);
  assert.equal(linearInputStep(0.01), 10);
  assert.equal(linearInputStep(0.001), 100);
});

test("builtin presets scale to stage units when bounds are absent and to scene bounds when present", () => {
  const key = {
    lightPath: "/Scene/Lights/KeyLight",
    lightType: "RectLight",
    intensity: 12,
    translate: [3.2, 3.0, 3.2],
    rotate: [-35, 45, 0],
    width: 2.0,
    height: 1.5,
  };

  // Without bounds: scales by 1 / metersPerUnit
  const cmFactor = presetScaleFactor(undefined, 0.01, true, null);
  assert.equal(cmFactor, 100);
  const cmKey = scaleLightItemForStage(key, cmFactor, null, true);
  assert.deepEqual(cmKey.translate, [320, 300, 320]);
  assert.equal(cmKey.width, 200);
  assert.equal(cmKey.height, 150);
  assert.deepEqual(cmKey.rotate, [-35, 45, 0]);
  assert.equal(cmKey.intensity, 12);

  // With 100x scene bounds on a 1.0 m/unit composed stage: scales & recenters around subject
  const bigBounds = { center: [0, 85, 0], size: [60, 170, 40], radius: 100 };
  const bigFactor = presetScaleFactor(undefined, 1.0, true, bigBounds);
  assert.equal(bigFactor, 100);
  const bigKey = scaleLightItemForStage(key, bigFactor, bigBounds, true);
  assert.deepEqual(bigKey.translate, [320, 285, 320]);
  assert.equal(bigKey.width, 200);
  assert.equal(bigKey.height, 150);
  assert.equal(effectiveLightScale(1.0, bigBounds), 100);
  assert.equal(linearInputStep(1.0, bigBounds), 10);
});

test("custom presets convert between recorded metersPerUnit and target stage units", () => {
  assert.equal(presetScaleFactor(0.01, 1.0, false), 0.01);
  assert.equal(presetScaleFactor(0.01, 0.01, false), 1);
  assert.equal(presetScaleFactor(undefined, 0.01, false), 1);
});
