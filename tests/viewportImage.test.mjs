import test from "node:test";
import assert from "node:assert/strict";
import { paintViewportBitmap } from "../src/viewportImage.ts";

function surface() {
  const operations = [];
  let width = 640, height = 480;
  const canvas = {
    get width() { return width; }, set width(v) { width = v; operations.push("clear-width"); },
    get height() { return height; }, set height(v) { height = v; operations.push("clear-height"); },
    getContext: () => ({ drawImage: bitmap => operations.push(bitmap.id) }),
  };
  return { canvas, operations };
}

test("successive decoded PNG, RGBA and JPEG frames paint without clearing a same-sized canvas", () => {
  const { canvas, operations } = surface();
  for (const id of ["idle PNG", "drag RGBA", "drag JPEG", "release PNG"]) {
    assert.equal(paintViewportBitmap(canvas, { width:640, height:480, imageBitmap:{id} }), true);
  }
  assert.deepEqual(operations, ["idle PNG", "drag RGBA", "drag JPEG", "release PNG"]);
});

test("missing frames retain previous pixels even when their requested dimensions differ", () => {
  const { canvas, operations } = surface();
  assert.equal(paintViewportBitmap(canvas, { width:800, height:600 }), false);
  assert.deepEqual(operations, []);
  assert.equal(canvas.width, 640);
  assert.equal(canvas.height, 480);
});

test("resize clears only changed dimensions and immediately paints the decoded replacement", () => {
  const { canvas, operations } = surface();
  paintViewportBitmap(canvas, { width:800, height:480, imageBitmap:{id:"replacement"} });
  assert.deepEqual(operations, ["clear-width", "replacement"]);
  assert.equal(paintViewportBitmap(null, {width:800,height:480}), false);
});
