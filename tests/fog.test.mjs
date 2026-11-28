import test from "node:test";
import assert from "node:assert/strict";
import { defaultFog, identityTransform, storeProject, expandProject, graphPreview } from "../src/fileRegistry.ts";
import { toolbarOperations, insertToolbarNode } from "../src/graphInsertion.ts";
import { ProjectHistory } from "../src/projectHistory.ts";

test("Fog inserts from toolbar and keeps settings through save, graph evaluation and history", () => {
  assert.ok(toolbarOperations.includes("fog"));
  const source = { id: "src", type: "stage", position: { x: 0, y: 0 }, data: { label: "Source", operation: "source", path: "I:/assets/smoke.vdb", nativeProject: "", publishedPath: "", assetPaths: [], transform: identityTransform } };
  const node = { ...source, id: "fog", data: { ...source.data, operation: "fog", path: "", fog: { ...defaultFog, densityScale: 2, volumePaths: ["/Scene/Volume"] } } };
  const initial = storeProject([source], [], []);
  const inserted = insertToolbarNode(initial, storeProject([node], [], []).nodes[0], { kind: "node", id: "src" }, () => "edge");
  const expanded = expandProject(JSON.parse(JSON.stringify(inserted)));
  assert.deepEqual(expanded.nodes[1].data.fog, node.data.fog);
  const graph = JSON.parse(graphPreview(inserted, "fog"));
  assert.deepEqual(graph.nodes.find(n => n.id === "fog").fog, node.data.fog);
  assert.equal(graph.dependencies.length, 1);
  assert.equal(graph.dependencies[0].path, "I:/assets/smoke.vdb");
  const history = new ProjectHistory();
  history.record(initial, inserted);
  assert.deepEqual(history.undo(inserted).project, initial);
  assert.deepEqual(history.redo(initial).project.nodes[1].data.fog, node.data.fog);
});
