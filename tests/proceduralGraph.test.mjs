import assert from "node:assert/strict";
import test from "node:test";
import { graphPreview } from "../src/fileRegistry.ts";

test("graphPreview changes when proceduralGraph nodes or edges change", () => {
  const baseProject = {
    version: 1,
    nodes: [
      { id: "src", type: "graph", position: { x: 0, y: 0 }, data: { label: "Source", operation: "source", inputDependencyId: "d1" } },
      { id: "mx", type: "graph", position: { x: 200, y: 0 }, data: { label: "Mat", operation: "materialx" } },
    ],
    edges: [{ id: "e1", source: "src", target: "mx" }],
    dependencies: [{ id: "d1", kind: "usd", path: "I:/test/scene.usda" }],
  };

  const sigEmpty = graphPreview(baseProject, "mx");

  const withProc = {
    ...baseProject,
    proceduralGraph: {
      nodes: [
        { id: "n1", type: "procedural", position: { x: 0, y: 0 }, data: { kind: "worley2d", params: { jitter: 0.8 } } },
        {
          id: "o1",
          type: "procedural",
          position: { x: 200, y: 0 },
          data: { kind: "output", materialXNodeId: "mx", parameter: "base_color", mappingType: "replace", strength: 1, enabled: true },
        },
      ],
      edges: [{ id: "pe1", source: "n1", sourceHandle: "out", target: "o1", targetHandle: "in" }],
    },
  };

  const sigWithProc = graphPreview(withProc, "mx");
  assert.notEqual(sigWithProc, sigEmpty);

  const modifiedParam = {
    ...withProc,
    proceduralGraph: {
      ...withProc.proceduralGraph,
      nodes: [
        { id: "n1", type: "procedural", position: { x: 0, y: 0 }, data: { kind: "worley2d", params: { jitter: 0.2 } } },
        withProc.proceduralGraph.nodes[1],
      ],
    },
  };

  assert.notEqual(graphPreview(modifiedParam, "mx"), sigWithProc);
});
