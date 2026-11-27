import test from "node:test";
import assert from "node:assert/strict";
import { viewportGraphContext } from "../src/viewportGraph.ts";
import { LatestRenderQueue, previewSize } from "../src/renderQueue.ts";
const settled = () => new Promise((resolve) => setImmediate(resolve));
function fixture() {
  let next = 0;
  const callbacks = new Map();
  const renders = [],
    frames = [],
    discarded = [],
    errors = [],
    busy = [];
  const queue = new LatestRenderQueue({
    render: (request) =>
      new Promise((resolve, reject) =>
        renders.push({ request, resolve, reject }),
      ),
    present: (frame) => frames.push(frame),
    discard: (frame) => discarded.push(frame),
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
  return { queue, tick, renders, frames, discarded, errors, busy, callbacks };
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

test("a settled frame can rebase a queued orbit before it is sent", async () => {
  const f = fixture();
  f.queue.enqueue("stage", { panX: 0, interactive: false });
  f.tick();
  f.queue.enqueue("stage", { panX: 0, yaw: 50, interactive: true });
  f.renders[0].resolve("settled");
  await settled();
  f.queue.updatePending((request) => ({ ...request, panX: request.panX - 1 }));
  f.tick();
  assert.deepEqual(f.renders[1].request, { panX: -1, yaw: 50, interactive: true });
  f.renders[1].resolve("orbited");
  await settled();
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

test("a queued groom commit runs before the new node preview and its stale image is discarded", async () => {
  const f = fixture();
  f.queue.enqueue("hair", "stroke in flight");
  f.tick();
  f.queue.enqueueRequired("hair", "last brush sample");
  f.queue.enqueueRequired("hair", "commit groom");
  f.queue.enqueue("other", "old preview");
  f.queue.enqueue("other", "latest preview");
  for (const [index, request] of ["stroke in flight", "last brush sample", "commit groom", "latest preview"].entries()) {
    assert.equal(f.renders[index].request, request);
    f.renders[index].resolve(request);
    await settled();
    f.tick();
  }
  assert.deepEqual(f.frames, ["latest preview"]);
  assert.deepEqual(f.discarded, ["stroke in flight", "last brush sample", "commit groom"]);
});

test("selecting a node with no preview keeps the old node's queued groom commit", async () => {
  const f = fixture();
  f.queue.enqueue("hair", "old preview");
  f.queue.enqueueRequired("hair", "commit groom");
  f.queue.clear();
  f.tick();
  assert.deepEqual(f.renders.map(({ request }) => request), ["commit groom"]);
  f.renders[0].resolve("old hair image");
  await settled();
  assert.deepEqual(f.frames, []);
  assert.equal(f.busy.at(-1), false);
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


test("continuous graph transform updates present moving frames before pointer release", async () => {
  const f = fixture();
  const graph = x => JSON.stringify({targetNodeId:"out", nodes:[
    {id:"move", operation:"transform", transform:{translation:[x,0,0]}, transformTargets:[{pattern:"/Scene/Box"}]},
    {id:"out",operation:"output"}], edges:[{id:"edge",source:"move",target:"out"}]});
  const enqueue = (x, interactive) => f.queue.enqueue(viewportGraphContext(graph(x)), {graph:graph(x),interactive});
  enqueue(1, true);
  f.tick();
  for (let x=2; x<=50; x++) enqueue(x,true);
  f.renders[0].resolve("prim at 1 while dragging");
  await settled();
  assert.deepEqual(f.frames,["prim at 1 while dragging"]);
  f.tick();
  assert.equal(JSON.parse(f.renders[1].request.graph).nodes[0].transform.translation[0],50);
  enqueue(51,false);
  f.renders[1].resolve("prim at 50 while dragging");
  await settled();
  f.tick();
  assert.equal(f.renders[2].request.interactive,false);
  f.renders[2].resolve("prim at 51 after release");
  await settled();
  assert.deepEqual(f.frames,["prim at 1 while dragging","prim at 50 while dragging","prim at 51 after release"]);
});

test("changing transform target paths invalidates in-flight images", async () => {
  const f = fixture();
  const graph = pattern => JSON.stringify({nodes:[{id:"move",operation:"transform",transformTargets:[{pattern}]}]});
  f.queue.enqueue(viewportGraphContext(graph("/Scene/A")),1);
  f.tick();
  f.queue.enqueue(viewportGraphContext(graph("/Scene/B")),2);
  f.renders[0].resolve("wrong target");
  await settled();
  assert.deepEqual(f.frames,[]);
});
