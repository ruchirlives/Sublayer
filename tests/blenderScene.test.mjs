import test from "node:test";
import assert from "node:assert/strict";
import { graphPreview, expandProject, storeProject } from "../src/fileRegistry.ts";

test("Blender scene survives save/reload and changes preview identity with shared objects", () => {
  const project = {
    version: 2,
    nodes: [{ id: "blender", type: "stage", position: { x: 0, y: 0 },
      data: { label: "Miko", operation: "blender", app: "Blender", axis: "preserve", scale: 1,
        nativeProjectDependencyId: "blend", blenderScene: "Octane", blenderSelections: ["Body"] } }],
    edges: [], dependencies: [{ id: "blend", kind: "nativeProject", path: "I:/Miko.blend" }],
  };
  const expanded = expandProject(JSON.parse(JSON.stringify(project)));
  const saved = storeProject(expanded.nodes, expanded.edges, project.dependencies);
  assert.equal(saved.nodes[0].data.blenderScene, "Octane");
  const octane = graphPreview(saved, "blender");
  assert.equal(JSON.parse(octane).nodes[0].blenderScene, "Octane");
  saved.nodes[0].data.blenderScene = "Cycles";
  assert.notEqual(graphPreview(saved, "blender"), octane);
});
