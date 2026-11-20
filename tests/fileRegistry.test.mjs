import test from "node:test";
import assert from "node:assert/strict";
import {
  fileKey,
  storeProject,
  expandProject,
  registerFiles,
  relinkDependency,
  referencedBy,
  editNodeData,
  overrideDependency,
  overrideInputs,
} from "../src/fileRegistry.ts";
const node = (id, path = "", nativeProject = "", publishedPath = "") => ({
  id,
  type: "stage",
  position: { x: 0, y: 0 },
  selected: true,
  data: {
    label: id,
    operation: "source",
    app: "",
    axis: "preserve",
    scale: 1,
    path,
    nativeProject,
    publishedPath,
  },
});
let counter = 0;
const id = () => `test-${++counter}`;
test("single JSON stores originals once and nodes store only IDs", () => {
  const p = storeProject(
    [
      node("a", "I:/original.usd"),
      node("b", "i:\\ORIGINAL.usd", "I:/native.blend"),
    ],
    [],
    [],
    id,
  );
  assert.equal(p.version, 2);
  assert.equal(p.dependencies.length, 2);
  assert.equal(
    p.nodes[0].data.inputDependencyId,
    p.nodes[1].data.inputDependencyId,
  );
  assert.equal(p.nodes[0].data.path, undefined);
  assert.equal(p.nodes[1].data.nativeProject, undefined);
  const expanded = expandProject(p);
  assert.equal(expanded.nodes[1].data.path, "I:/original.usd");
  assert.equal(expanded.nodes[0].selected, true);
});
test("Browse-style registration reuses Windows case/slash/path variants with warning IDs", () => {
  const initial = registerFiles([], ["I:/assets/scene.usd"], "usd", id);
  const again = registerFiles(
    initial.dependencies,
    ["i:\\assets\\part\\..\\SCENE.usd"],
    "asset",
    id,
  );
  assert.equal(again.dependencies.length, 1);
  assert.deepEqual(again.reused, initial.ids);
  assert.equal(
    fileKey("I:/assets/./scene.usd"),
    fileKey("i:\\ASSETS\\scene.usd"),
  );
});
test("relink preserves stable ID and changes every shared node view", () => {
  const p = storeProject(
    [node("a", "I:/old.usd"), node("b", "I:/old.usd")],
    [],
    [],
    id,
  );
  const dependency = p.dependencies[0];
  const relinked = relinkDependency(p, dependency.id, "Z:/replacement.usd");
  assert.equal(relinked.dependencies[0].id, dependency.id);
  assert.equal(referencedBy(relinked, dependency.id).length, 2);
  assert.ok(
    expandProject(relinked).nodes.every(
      (n) => n.data.path === "Z:/replacement.usd",
    ),
  );
  assert.equal(
    p.dependencies[0].path,
    "I:/old.usd",
    "input model stays immutable",
  );
});
test("relink to existing file merges dependencies and updates input, publication and assets", () => {
  const p = storeProject(
    [node("a", "I:/a.usd"), node("b", "I:/b.usd", "", "I:/a.usd")],
    [],
    [],
    id,
  );
  const a = p.dependencies[0].id,
    b = p.dependencies[1].id;
  p.nodes[1].data.assetDependencyIds = [a, b];
  const merged = relinkDependency(p, a, "i:\\B.usd");
  assert.equal(merged.dependencies.length, 1);
  assert.equal(merged.nodes[0].data.inputDependencyId, b);
  assert.equal(merged.nodes[1].data.publishedDependencyId, b);
  assert.deepEqual(merged.nodes[1].data.assetDependencyIds, [b]);
});
test("unreferenced asset registrations survive save/load round trip", () => {
  const assets = registerFiles(
    [],
    ["I:/textures/diffuse.png"],
    "asset",
    id,
  ).dependencies;
  const p = storeProject([node("a")], [], assets, id);
  assert.equal(p.dependencies.length, 1);
  assert.equal(referencedBy(p, assets[0].id).length, 0);
  assert.equal(
    storeProject(expandProject(p).nodes, [], p.dependencies, id).dependencies[0]
      .path,
    "I:/textures/diffuse.png",
  );
});
test("inspector edits keep a stable entry while typing and detach from shared originals", () => {
  let p = storeProject(
    [node("a", "I:/shared.usd"), node("b", "I:/shared.usd")],
    [],
    [],
    id,
  );
  const shared = p.dependencies[0].id;
  p = editNodeData(p, "a", { path: "I:/new.usd" });
  const own = p.nodes[0].data.inputDependencyId;
  assert.notEqual(own, shared);
  assert.equal(expandProject(p).nodes[1].data.path, "I:/shared.usd");
  const count = p.dependencies.length;
  p = editNodeData(p, "a", { path: "I:/newer.usd" });
  assert.equal(p.dependencies.length, count);
  assert.equal(p.nodes[0].data.inputDependencyId, own);
});

test("USD graph preview ignores UI moves but includes transform edits and every incoming edge", async () => {
  const { graphPreview } = await import("../src/fileRegistry.ts");
  const a = node("a", "I:/a.usd"),
    b = node("b", "I:/b.usd"),
    out = node("out");
  out.data.operation = "output";
  const p = storeProject(
    [a, b, out],
    [
      { id: "a-out", source: "a", target: "out" },
      { id: "b-out", source: "b", target: "out" },
    ],
    [],
    id,
  );
  const before = graphPreview(p, "out");
  p.nodes[0].position = { x: 900, y: 600 };
  p.nodes[0].data.label = "Renamed";
  assert.equal(graphPreview(p, "out"), before);
  assert.equal(JSON.parse(before).edges.length, 2);
  p.nodes[2].data.operation = "transform";
  p.nodes[2].data.transform = {
    translation: [2, 0, 0],
    rotation: [0, 45, 0],
    scale: [1, 2, 1],
  };
  assert.notEqual(graphPreview(p, "out"), before);
  const loaded = expandProject(p);
  assert.equal(loaded.nodes[2].data.transform.rotation[1], 45);
});

test("discovered missing assets retain provenance, shared node usage and explicit relinks", async () => {
  const { mergeUsdDiscovery } = await import("../src/fileRegistry.ts");
  let p = storeProject(
    [node("a", "I:/scene.usd"), node("b", "I:/scene.usd"), node("out")],
    [{ id: "a-out", source: "a", target: "out" }],
    [],
    id,
  );
  const source = p.nodes[0].data.inputDependencyId;
  p = mergeUsdDiscovery(
    p,
    source,
    [{ path: "I:/Texture/missing.png", kind: "asset" }],
    id,
  );
  const asset = p.dependencies.find((d) => d.kind === "asset");
  assert.deepEqual(
    referencedBy(p, asset.id).map((n) => n.id),
    ["a", "b", "out"],
  );
  p = relinkDependency(p, asset.id, "Z:/found/missing.png");
  p = mergeUsdDiscovery(
    p,
    source,
    [{ path: "I:/Texture/missing.png", kind: "asset" }],
    id,
  );
  assert.equal(p.dependencies.length, 2);
  assert.equal(
    p.dependencies.find((d) => d.id === asset.id).path,
    "Z:/found/missing.png",
  );
  assert.equal(
    p.dependencies.find((d) => d.id === asset.id).usdReferences[0].path,
    "I:/Texture/missing.png",
  );
  p = mergeUsdDiscovery(p, source, [], id);
  assert.equal(
    referencedBy(p, asset.id).length,
    0,
    "removed USD references no longer report usage",
  );
});

test("pinned preview scope refreshes for upstream changes and ignores downstream edits", async () => {
  const { graphPreview } = await import("../src/fileRegistry.ts");
  const a = node("a", "I:/a.usd"),
    t = node("t"),
    out = node("out");
  t.data.operation = "transform";
  out.data.operation = "output";
  const p = storeProject(
    [a, t, out],
    [
      { id: "at", source: "a", target: "t" },
      { id: "to", source: "t", target: "out" },
    ],
    [],
    id,
  );
  p.viewport = { pinnedNodeId: "t" };
  const before = graphPreview(p, p.viewport.pinnedNodeId);
  p.nodes[2].data.transform = {
    translation: [10, 0, 0],
    rotation: [0, 0, 0],
    scale: [1, 1, 1],
  };
  assert.equal(
    graphPreview(p, "t"),
    before,
    "downstream-only edits must not invalidate a pinned output",
  );
  p.dependencies[0].path = "I:/changed-source.usd";
  assert.notEqual(
    graphPreview(p, "t"),
    before,
    "upstream source changes must invalidate the pinned output",
  );
  assert.equal(JSON.parse(JSON.stringify(p)).viewport.pinnedNodeId, "t");
});

test("asset overrides are scoped and survive JSON round trips", () => {
  const source = node("source", "I:/scene.usd");
  const left = node("left"), right = node("right");
  left.data.operation = right.data.operation = "assetOverride";
  const p = storeProject([source, left, right], [{id:"a", source:"source", target:"left"},{id:"b", source:"source", target:"right"}], [], id);
  p.dependencies.push({id:"missing",kind:"asset",path:"I:/old/texture.png",usdReferences:[{sourceDependencyId:p.nodes[0].data.inputDependencyId,path:"I:/old/texture.png"}]});
  const next = overrideDependency(p,"left","missing","I:/new/texture.png");
  assert.equal(next.dependencies.find(d => d.id === "missing").path, "I:/old/texture.png");
  assert.equal(next.nodes[2].data.assetOverrides, undefined);
  assert.equal(next.nodes[1].data.assetOverrides.length, 1);
  assert.equal(overrideInputs(next,"left").length,1);
  const restored = storeProject(expandProject(next).nodes,next.edges,next.dependencies);
  assert.deepEqual(restored.nodes[1].data.assetOverrides,next.nodes[1].data.assetOverrides);
});

test("overrideInputs includes MaterialX rules, HDRI textures and mergeDiscoveredInputs reuses links", async () => {
  const { mergeDiscoveredInputs } = await import("../src/fileRegistry.ts");
  const mtlx = node("mtlx");
  mtlx.data.operation = "materialx";
  mtlx.data.materialXRules = [{
    id: "r1",
    pattern: "*",
    mode: "simple",
    material: { baseColorTextureDependencyId: "tex-1", roughnessTextureDependencyId: null, baseColor: [0.8, 0.8, 0.8], roughness: 0.4, metallic: 0, ior: 1.5, opacity: 1, uvScale: 1, useTexture: true, useRoughnessTexture: false, scope: "whole", primPath: "" },
  }];
  const hdri = node("hdri");
  hdri.data.operation = "hdri";
  hdri.data.hdri = { textureDependencyId: "hdri-1", intensity: 1, exposure: 0, rotation: 0, visible: true };
  const over = node("over");
  over.data.operation = "assetOverride";
  const p = storeProject(
    [mtlx, hdri, over],
    [{ id: "e1", source: "mtlx", target: "hdri" }, { id: "e2", source: "hdri", target: "over" }],
    [{ id: "tex-1", kind: "asset", path: "I:/textures/color.png" }, { id: "hdri-1", kind: "asset", path: "I:/textures/sky.exr" }],
    id,
  );
  const inputs = overrideInputs(p, "over").map((d) => d.id).sort();
  assert.deepEqual(inputs, ["hdri-1", "tex-1"]);
  const merged = mergeDiscoveredInputs(p, [
    { path: "i:/TEXTURES/color.png", kind: "asset" },
    { path: "I:/textures/packed.png", kind: "asset" },
  ], id);
  assert.equal(merged.dependencies.length, 3);
  assert.equal(merged.dependencies[2].path, "I:/textures/packed.png");
});

test("application executable is a shared registry link and does not affect USD preview identity", async () => {
  const { graphPreview } = await import("../src/fileRegistry.ts");
  const app = node("app", "", "I:/model.blend", "I:/model.usda");
  app.data.operation = "application";
  app.data.app = "Blender";
  app.data.appExecutable = "C:/Program Files/Blender/blender.exe";
  const p = storeProject([app],[],[],id);
  assert.equal(p.nodes[0].data.appExecutable, undefined);
  const executableId = p.nodes[0].data.appExecutableDependencyId;
  assert.equal(p.dependencies.find(d => d.id === executableId).kind,"executable");
  assert.equal(expandProject(p).nodes[0].data.appExecutable, app.data.appExecutable);
  assert.deepEqual(referencedBy(p, executableId).map(n => n.id), ["app"]);
  const next = editNodeData(p,"app",{appExecutable:"D:/Blender/blender.exe"});
  assert.equal(next.nodes[0].data.appExecutableDependencyId,executableId);
  assert.equal(graphPreview(next,"app"),graphPreview(p,"app"));
  const merged = relinkDependency(next,executableId,"I:/other/blender.exe");
  assert.equal(expandProject(merged).nodes[0].data.appExecutable,"I:/other/blender.exe");
});

test("application preparation composes its inputs while publication stays a leaf", async () => {
  const {graphPreview} = await import("../src/fileRegistry.ts");
  const source=node("source","I:/source.usd"), app=node("app","I:/own-input.usd","I:/native.blend","I:/published.usd");
  app.data.operation="application";
  const p=storeProject([source,app],[{id:"input",source:"source",target:"app"}],[],id);
  const published=JSON.parse(graphPreview(p,"app"));
  assert.deepEqual(published.nodes.map(n=>n.id),["app"]);
  const input=JSON.parse(graphPreview(p,"app",true));
  assert.equal(input.nodes.length,2);
  assert.equal(input.nodes[1].operation,"source");
  assert.equal(input.nodes[1].publishedDependencyId,null);
  assert.ok(!input.dependencies.some(d=>d.path==="I:/published.usd"));
  app.data.coordinateDraft={version:1,app:"Blender",upAxis:"Z",metersPerUnit:0.01,scale:2};
  const stored=storeProject([app],[],[],id);
  assert.deepEqual(expandProject(stored).nodes[0].data.coordinateDraft,app.data.coordinateDraft);
});

test("handoff registration preserves file roles, conversion and links across save/load", async()=>{
  const {recordHandoff} = await import("../src/fileRegistry.ts");
  const app=node("app","I:/source.usd","I:/native.blend","I:/published.usd");app.data.operation="application";
  const p=storeProject([app],[],[],id);
  const draft={version:1,upAxis:"Z",metersPerUnit:0.01,scale:2};
  const policy={profile:{id:"blender",revision:1},editScope:[]};
  const recorded=recordHandoff(p,"app",{path:"I:/handoff.usda",layerPaths:["I:/handoff.layers/layer-0000.usda"],draft,policy});
  assert.equal(expandProject(recorded).nodes[0].data.publishedPath,"I:/published.usd");
  assert.equal(expandProject(recorded).nodes[0].data.nativeProject,"I:/native.blend");
  assert.deepEqual(recorded.nodes[0].data.handoff.conversion,draft);
  assert.deepEqual(recorded.nodes[0].data.handoff.policy,policy);
  const restored=storeProject(expandProject(recorded).nodes,recorded.edges,recorded.dependencies);
  assert.deepEqual(restored.nodes[0].data.handoff,recorded.nodes[0].data.handoff);
  assert.ok(referencedBy(restored,recorded.nodes[0].data.handoff.dependencyId).some(n=>n.id==="app"));
});

test("first import links native project and freezes actual handoff provenance without publishing",async()=>{
 const {recordNativeCreation}=await import("../src/fileRegistry.ts");
 const app=node("app","I:/input.usd","","I:/published.usd");app.data.operation="application";
 const p=storeProject([app],[],[],id);
 p.dependencies.push({id:"handoff",kind:"usd",path:"I:/handoff.usda"});
 const conversion={upAxis:"Z",metersPerUnit:0.01,scale:2};
 const result={baselinePath:"I:/baseline.usdc",path:"I:/native.blend",handoffDependencyId:"handoff",conversion,policy:{profile:{id:"blender",revision:1},editScope:[]},report:{adapterVersion:2,blenderVersion:"5.2.1"}};
 const next=recordNativeCreation(p,"app",result);
 assert.equal(expandProject(next).nodes[0].data.nativeProject,result.path);
 assert.equal(expandProject(next).nodes[0].data.publishedPath,"I:/published.usd");
 assert.deepEqual(next.nodes[0].data.nativeImport.conversion,conversion);
 assert.deepEqual(next.nodes[0].data.nativeImport.policy,result.policy);
 assert.throws(()=>recordNativeCreation(next,"app",result),/preserved/);
 assert.equal(p.nodes[0].data.nativeProjectDependencyId,null);
});

test("rerunning Quickstart stages a fresh round trip without touching the active links or output", async () => {
  const {prepareQuickstart, recordHandoff, recordNativeCreation, recordManualBaseline} = await import("../src/fileRegistry.ts");
  const app = node("app", "I:/source.usd", "", "I:/published.usd");
  app.data.operation = "application";
  const policy = {profile:{id:"blender",revision:1},editScope:[]};
  const conversion = {upAxis:"Z",metersPerUnit:1};
  const original = recordHandoff(storeProject([app], [], [], id), "app", {
    path:"I:/old_handoff.usd", layerPaths:[], draft:conversion, policy,
  });
  const oldHandoff = original.nodes[0].data.handoff.dependencyId;
  const imported = recordNativeCreation(original, "app", {
    path:"I:/old.blend", baselinePath:"I:/old.baseline.usdc",
    handoffDependencyId:oldHandoff, conversion, policy, report:{},
  });
  const active = recordManualBaseline(imported, "app", {
    baselinePath:"I:/manual.usdc", nativeDependencyId:imported.nodes[0].data.nativeProjectDependencyId,
    handoffDependencyId:oldHandoff, conversion, policy, report:{},
  });
  const before = JSON.stringify(active);
  const staged = prepareQuickstart(active, "app", {
    path:"I:/new_handoff.usd", layerPaths:[], draft:conversion, policy,
  });
  assert.equal(JSON.stringify(active), before);
  assert.equal(staged.nodes[0].data.nativeProjectDependencyId, null);
  assert.equal(staged.nodes[0].data.manualBaseline, null);
  const next = recordNativeCreation(staged, "app", {
    path:"I:/new.blend", baselinePath:"I:/new.baseline.usdc",
    handoffDependencyId:staged.nodes[0].data.handoff.dependencyId, conversion, policy, report:{},
  });
  const view = expandProject(next).nodes[0].data;
  assert.equal(view.nativeProject, "I:/new.blend");
  assert.equal(view.publishedPath, "I:/published.usd");
  assert.equal(view.nativeImport.handoffDependencyId, view.handoff.dependencyId);
  assert.notEqual(view.nativeImport.baselineDependencyId, active.nodes[0].data.nativeImport.baselineDependencyId);
  for (const dependency of active.dependencies) assert.ok(next.dependencies.some(d => d.id === dependency.id));
});

test("publication promotion keeps previous output on stale policy and persists all artifact roles",async()=>{
 const {recordNativeCreation,recordPublication}=await import("../src/fileRegistry.ts");
 const app=node("app");app.data.operation="application";app.data.applicationProfileId="blender";
 const base=storeProject([app],[],[],id);base.dependencies.push({id:"handoff",kind:"usd",path:"I:/handoff.usda"});
 const policy={profile:{id:"blender",revision:2},editScope:[]},conversion={upAxis:"Y",scale:1};
 const prepared=recordNativeCreation(base,"app",{path:"I:/native.blend",baselinePath:"I:/baseline.usdc",handoffDependencyId:"handoff",policy,conversion,report:{}});
 const baseline=prepared.nodes[0].data.nativeImport.baselineDependencyId;
 const result={path:"I:/published.usda",editLayerPath:"I:/published.edits.usda",baselineDependencyId:baseline,handoffDependencyId:"handoff",policy,conversion,report:{applied:[]},sourceRevisions:[],correspondence:[]};
 const published=recordPublication(prepared,"app","I:/edited.usda",result);
 const view=expandProject(published).nodes[0].data;
 assert.equal(view.publishedPath,result.path);assert.equal(view.nativeProject,"I:/native.blend");assert.equal(view.publication.baselineDependencyId,baseline);
 assert.equal(prepared.nodes[0].data.publishedDependencyId,null);
 const changed=structuredClone(published);changed.nodes[0].data.publicationPolicy={...policy,editScope:["points"]};
 assert.throws(()=>recordPublication(changed,"app","I:/edited.usda",result),/previous output was preserved/);
 assert.equal(expandProject(changed).nodes[0].data.publishedPath,result.path);
 const reconciled=recordPublication(changed,"app","I:/edited.usda",{...result,path:"I:/reconciled.usda",editLayerPath:"I:/reconciled.edits.usda",replacesPublicationDependencyId:changed.nodes[0].data.publication.dependencyId,reconciliation:{inputPath:"I:/reconciled.input.usda",layerPaths:["I:/reconciled.input.layers/layer.usda"],graphRevision:"a".repeat(64)}});
 assert.deepEqual(reconciled.nodes[0].data.nativeImport,prepared.nodes[0].data.nativeImport);
 assert.equal(expandProject(reconciled).nodes[0].data.publishedPath,"I:/reconciled.usda");
 const input=reconciled.nodes[0].data.publication.reconciliation.inputDependencyId;
 assert.ok(referencedBy(reconciled,input).some(n=>n.id==="app"));
 const stored=storeProject(expandProject(reconciled).nodes,reconciled.edges,reconciled.dependencies);
 assert.deepEqual(stored.nodes[0].data.publication.reconciliation,reconciled.nodes[0].data.publication.reconciliation);
 assert.throws(()=>recordPublication(reconciled,"app","I:/edited.usda",{...result,reconciliation:{inputPath:"I:/stale.usda",layerPaths:[],graphRevision:"b".repeat(64)},replacesPublicationDependencyId:"old"}),/previous publication changed/);

});

test("application input identity excludes its own publication and includes upstream publications",async()=>{
 const {graphPreview}=await import("../src/fileRegistry.ts");
 const upstream=node("upstream"),app=node("app");upstream.data.operation="application";app.data.operation="application";
 const p=storeProject([upstream,app],[{id:"edge",source:"upstream",target:"app"}],[],id);
 const before=graphPreview(p,"app",true);
 p.nodes[1].data.publication={sourceRevisions:[{path:"I:/own.usda"}]};p.nodes[1].data.publishedDependencyId="own";
 assert.equal(graphPreview(p,"app",true),before);
 p.nodes[0].data.publication={sourceRevisions:[{path:"I:/upstream.usda"}]};
 assert.notEqual(graphPreview(p,"app",true),before);
});

test("MaterialX settings, texture dependencies, preview signatures and shared relinks persist",async()=>{
 const {defaultMaterialX,graphPreview}=await import("../src/fileRegistry.ts");
 const n=node("material");n.data.operation="materialx";n.data.materialX={...structuredClone(defaultMaterialX),baseColorTextureDependencyId:"tex"};
 const project=storeProject([n],[],[{id:"tex",kind:"asset",path:"I:/color.png"}],id);
 assert.ok(referencedBy(project,"tex").some(n=>n.id==="material"));
 assert.ok(overrideInputs(project,"material").some(d=>d.id==="tex"));
 const before=graphPreview(project,"material");assert.ok(JSON.parse(before).dependencies.some(d=>d.id==="tex"));
 const changed=structuredClone(project);changed.nodes[0].data.materialX.roughness=.9;assert.notEqual(graphPreview(changed,"material"),before);
 const restored=storeProject(expandProject(project).nodes,project.edges,project.dependencies);assert.deepEqual(restored.nodes[0].data.materialX,n.data.materialX);
});

test("Find Missing verbatim Windows paths normalize to ordinary shared file links",async()=>{
 const {fileLink}=await import("../src/fileRegistry.ts");
 const raw=String.raw`\\?\I:\CGPROJECTS\Uranus\textures\BreastPlate_basecolor.png`;
 const ordinary="I:/CGPROJECTS/Uranus/textures/BreastPlate_basecolor.png";
 assert.equal(fileLink(raw),ordinary);assert.equal(fileKey(raw),fileKey(ordinary));
 const result=registerFiles([{id:"texture",kind:"asset",path:raw}],[ordinary],"asset",id);assert.equal(result.dependencies.length,1);assert.equal(result.dependencies[0].path,ordinary);
 assert.equal(fileLink(String.raw`\\?\UNC\server\share\image.png`),"//server/share/image.png");
});

test("manual baseline is independent of immutable native-import provenance and survives JSON",async()=>{
 const {recordNativeCreation,recordManualBaseline}=await import("../src/fileRegistry.ts");
 const app=node("app");app.data.operation="application";
 const base=storeProject([app],[],[],id);base.dependencies.push({id:"handoff",kind:"usd",path:"I:/handoff.usda"});
 const policy={profile:{id:"blender",revision:2},editScope:["points"]},conversion={upAxis:"Z",scale:1};
 const prepared=recordNativeCreation(base,"app",{path:"I:/native.blend",baselinePath:"I:/captured.usdc",handoffDependencyId:"handoff",policy,conversion,report:{}});
 prepared.nodes[0].data.handoff={dependencyId:"handoff",policy,conversion,layerDependencyIds:[]};
 const original=structuredClone(prepared.nodes[0].data.nativeImport);
 const result={baselinePath:"I:/manual.usdc",nativeDependencyId:prepared.nodes[0].data.nativeProjectDependencyId,handoffDependencyId:"handoff",policy,conversion,report:{origin:"manual"}};
 const next=recordManualBaseline(prepared,"app",result);
 assert.deepEqual(next.nodes[0].data.nativeImport,original);
 const stored=storeProject(expandProject(next).nodes,next.edges,next.dependencies);
 assert.deepEqual(stored.nodes[0].data.manualBaseline,next.nodes[0].data.manualBaseline);
 assert.deepEqual(referencedBy(stored,stored.nodes[0].data.manualBaseline.baselineDependencyId).map(n=>n.id),["app"]);
 assert.throws(()=>recordManualBaseline({...prepared,nodes:[]},"app",result),/changed/);
});
test("native creation without automatic baseline records preparation only",async()=>{
 const {recordNativeCreation}=await import("../src/fileRegistry.ts");
 const app=node("app");app.data.operation="application";const base=storeProject([app],[],[],id);
 const next=recordNativeCreation(base,"app",{path:"I:/native.blend",baselinePath:null,handoffDependencyId:"handoff",policy:{},conversion:{},report:{baselineCaptured:false}});
 assert.ok(next.nodes[0].data.nativePreparation);assert.equal(next.nodes[0].data.nativeImport,undefined);
 assert.equal(next.dependencies.some(d=>d.path.endsWith(".usdc")),false);
 assert.equal(expandProject(next).nodes[0].data.nativeProject,"I:/native.blend");
});
