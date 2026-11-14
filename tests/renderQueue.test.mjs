import test from "node:test";
import assert from "node:assert/strict";
import { LatestRenderQueue, previewSize } from "../src/renderQueue.ts";
const settled = () => new Promise((resolve) => setImmediate(resolve));
function fixture() {
  let next = 0;
  const callbacks = new Map();
  const renders = [],
    frames = [],
    errors = [],
    busy = [];
  const queue = new LatestRenderQueue({
    render: (request) =>
      new Promise((resolve, reject) =>
        renders.push({ request, resolve, reject }),
      ),
    present: (frame) => frames.push(frame),
    error: (error) => errors.push(error),
    busy: (value) => busy.push(value),
    schedule: (callback) => {
      const id = ++next;
      callbacks.set(id, callback);
      return id;
    },
    cancel: (id) => callbacks.delete(id),
  });
  const tick = () => {
    const batch = [...callbacks.values()];
    callbacks.clear();
    batch.forEach((callback) => callback());
  };
  return { queue, tick, renders, frames, errors, busy, callbacks };
}

test("continuous motion presents completed frames while keeping only the newest pending camera", async () => {
  const f = fixture();
  f.queue.enqueue("stage", { yaw: 1 });
  f.tick();
  for (let yaw = 2; yaw <= 100; yaw++) f.queue.enqueue("stage", { yaw });
  assert.equal(f.renders.length, 1, "never queue multiple backend requests");
  f.renders[0].resolve("first motion frame");
  await settled();
  assert.deepEqual(
    f.frames,
    ["first motion frame"],
    "new camera input must not starve display",
  );
  f.tick();
  assert.deepEqual(f.renders[1].request, { yaw: 100 });
  f.renders[1].resolve("latest motion frame");
  await settled();
  assert.equal(f.frames.length, 2);
});

test("release keeps the last full-quality request even with a preview in flight", async () => {
  const f = fixture();
  f.queue.enqueue("stage", { yaw: 1, interactive: true });
  f.tick();
  f.queue.enqueue("stage", { yaw: 2, interactive: true });
  f.queue.enqueue("stage", { yaw: 3, interactive: false });
  f.renders[0].resolve("preview");
  await settled();
  f.tick();
  assert.deepEqual(f.renders[1].request, { yaw: 3, interactive: false });
  f.renders[1].resolve("full quality");
  await settled();
  assert.deepEqual(f.frames, ["preview", "full quality"]);
  assert.equal(f.busy.at(-1), false);
});

test("scene changes reject old frames, including switching away and back", async () => {
  const f = fixture();
  f.queue.enqueue("A", 1);
  f.tick();
  f.queue.enqueue("B", 2);
  f.queue.enqueue("A", 3);
  f.renders[0].resolve("old A");
  await settled();
  assert.deepEqual(f.frames, []);
  f.tick();
  assert.equal(f.renders[1].request, 3);
  f.renders[1].resolve("new A");
  await settled();
  assert.deepEqual(f.frames, ["new A"]);
});

test("errors do not block subsequent camera updates", async () => {
  const f = fixture();
  f.queue.enqueue("A", 1);
  f.tick();
  f.queue.enqueue("A", 2);
  f.renders[0].reject("render error");
  await settled();
  f.tick();
  assert.deepEqual(f.errors, ["render error"]);
  f.renders[1].resolve("recovered");
  await settled();
  assert.deepEqual(f.frames, ["recovered"]);
});

test("clear and disposal suppress stale frames and cancel scheduled work", async () => {
  const f = fixture();
  f.queue.enqueue("A", 1);
  f.tick();
  f.queue.clear();
  f.renders[0].resolve("stale");
  await settled();
  assert.deepEqual(f.frames, []);
  f.queue.enqueue("B", 2);
  f.queue.dispose();
  f.tick();
  assert.equal(f.renders.length, 1);
  assert.equal(f.callbacks.size, 0);
});

test("interactive resolution preserves aspect and release restores display resolution", () => {
  assert.deepEqual(previewSize(1440, 1080, true), { width: 800, height: 600 });
  assert.deepEqual(previewSize(1440, 1080, false), {
    width: 1440,
    height: 1080,
  });
  assert.deepEqual(previewSize(400, 200, true), { width: 400, height: 200 });
});
