import test from "node:test";
import assert from "node:assert/strict";
import {
  movementLocks,
  placementDelta,
  constrainTransform,
} from "../src/instancer.ts";
import {
  defaultInstancer,
  identityTransform,
  storeProject,
  expandProject,
  graphPreview,
} from "../src/fileRegistry.ts";
import { ProjectHistory } from "../src/projectHistory.ts";
const identity = [
  [1, 0, 0, 0],
  [0, 1, 0, 0],
  [0, 0, 1, 0],
  [0, 0, 0, 1],
];
test("movement constraints track stage up and lock translation only", () => {
  assert.deepEqual(movementLocks(defaultInstancer, "Y"), [false, true, false]);
  assert.deepEqual(movementLocks(defaultInstancer, "Z"), [false, false, true]);
  assert.deepEqual(movementLocks({ ...defaultInstancer, movement: "x" }, "Y"), [
    false,
    true,
    true,
  ]);
  const next = {
    translation: [4, 5, 6],
    rotation: [10, 20, 30],
    scale: [2, 2, 2],
  };
  assert.deepEqual(
    constrainTransform(next, identityTransform, [false, true, false]),
    { ...next, translation: [4, 0, 6] },
  );
});
test("orthographic plane drag uses exact world displacement and edge-on fallback stays finite", () => {
  const result = placementDelta(
    identity,
    200,
    200,
    [0, 0, 0],
    [100, 100],
    [150, 150],
    [false, false, true],
  );
  assert.deepEqual(result, [0.5, -0.5, 0]);
  const edge = placementDelta(
    identity,
    200,
    200,
    [0, 0, 0],
    [100, 100],
    [150, 150],
    [false, true, false],
  );
  assert.ok(edge.every(Number.isFinite));
  assert.equal(edge[1], 0);
  assert.ok(Math.abs(edge[0] - 0.5) < 1e-4);
  assert.deepEqual(
    placementDelta(
      identity,
      200,
      200,
      [0, 0, 0],
      [100, 100],
      [150, 150],
      [true, true, true],
    ),
    [0, 0, 0],
  );
});
test("perspective drag stays on the starting-height plane", () => {
  const matrix = [
    [1, 0, 0, 0],
    [0, 1, 0, 0],
    [0, 0, -1.02, -1],
    [0, 0, -0.202, 0],
  ];
  const delta = placementDelta(
    matrix,
    200,
    200,
    [0, 0, -2],
    [100, 100],
    [150, 100],
    [false, false, true],
  );
  assert.ok(Math.abs(delta[0] - 1) < 1e-8);
  assert.equal(delta[2], 0);
});
test("placements persist through storage, graph serialization and gesture undo", () => {
  const n = {
    id: "i",
    type: "stage",
    position: { x: 0, y: 0 },
    data: {
      label: "Instancer",
      operation: "instancer",
      app: "",
      axis: "preserve",
      scale: 1,
      path: "",
      nativeProject: "",
      publishedPath: "",
      instancer: defaultInstancer,
    },
  };
  const before = storeProject([n], [], []);
  const item = {
    id: "stable-id",
    sourcePath: "/Scene/Tree",
    visible: true,
    transform: { ...identityTransform, translation: [3, 0, 0] },
  };
  const after = structuredClone(before);
  after.nodes[0].data.instancer = { ...defaultInstancer, instances: [item] };
  assert.equal(
    expandProject(after).nodes[0].data.instancer.instances[0].id,
    "stable-id",
  );
  assert.equal(
    JSON.parse(graphPreview(after, "i")).nodes[0].instancer.instances[0]
      .transform.translation[0],
    3,
  );
  const history = new ProjectHistory();
  history.record(before, after, "Create instance");
  assert.equal(
    history.undo(after).project.nodes[0].data.instancer.instances.length,
    0,
  );
  assert.equal(
    history.redo(before).project.nodes[0].data.instancer.instances[0].id,
    "stable-id",
  );
});


test("Instancer is exposed in the main toolbar and accepts canvas and edge insertion", async () => {
  const { toolbarOperations, insertToolbarNode } = await import("../src/graphInsertion.ts");
  assert.equal(toolbarOperations[toolbarOperations.indexOf("scatter") + 1], "instancer");
  const node = (id, operation, x) => ({id, type: "stage", position: {x, y: 0}, data: {operation}});
  const project = {version: 2, dependencies: [], nodes: [node("s", "source", 0), node("out", "output", 400)], edges: [{id: "edge", source: "s", target: "out"}]};
  const added = node("manual", "instancer", 200);
  assert.equal(insertToolbarNode(project, added, null).nodes.at(-1).data.operation, "instancer");
  const inserted = insertToolbarNode(project, added, {kind: "edge", id: "edge"}, () => "new-edge");
  assert.ok(inserted.edges.some(e => e.source === "s" && e.target === "manual"));
  assert.ok(inserted.edges.some(e => e.source === "manual" && e.target === "out"));
});
