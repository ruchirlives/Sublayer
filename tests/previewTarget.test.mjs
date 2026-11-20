import test from "node:test";
import assert from "node:assert/strict";
import {previewTarget, sceneHierarchyKey} from "../src/previewTarget.ts";

test("groom point and strand edits keep the same scene hierarchy", () => {
  const graph = {
    targetNodeId: "hair",
    nodes: [{ id: "hair", operation: "hair", hair: { simEnabled: true } }],
    edges: [],
  };
  const before = sceneHierarchyKey(JSON.stringify(graph));
  graph.nodes[0].hair.groomDeltas = { "/Scene/Curves": { pointsZB64: "updated", countsZB64: "updated" } };
  assert.equal(sceneHierarchyKey(JSON.stringify(graph)), before);
  graph.nodes[0].hair.targetQuery = "/Scene/OtherCurves";
  assert.notEqual(sceneHierarchyKey(JSON.stringify(graph)), before);
  graph.nodes.push({ id: "output", operation: "output" });
  graph.targetNodeId = "output";
  assert.notEqual(sceneHierarchyKey(JSON.stringify(graph)), before);
});
const project={version:2,nodes:[{id:"app",data:{label:"Blender",operation:"application",inputDependencyId:null,publishedDependencyId:null}}],edges:[],dependencies:[]};
test("empty application states do not issue a render request",()=>{
  assert.equal(previewTarget(project,"app","handoff").path,"");
  assert.equal(previewTarget(project,"app","handoff").title,"No handoff USD yet");
  assert.equal(previewTarget(project,"app","published").title,"No published output yet");
  assert.equal(previewTarget(project,"app","input").title,"No incoming scene");
});
test("handoff and publication preview use distinct artifacts without changing graph output",()=>{
  const p=structuredClone(project);
  p.nodes[0].data.handoff={dependencyId:"handoff"};p.nodes[0].data.publishedDependencyId="output";
  p.dependencies=[{id:"handoff",path:"I:/handoff.usda"},{id:"output",path:"I:/published.usda"}];
  assert.equal(previewTarget(p,"app","handoff").path,"I:/handoff.usda");
  const published=JSON.parse(previewTarget(p,"app","published").graph);
  assert.equal(published.nodes[0].publishedDependencyId,"output");
  assert.equal(p.nodes[0].data.publishedDependencyId,"output");
});
