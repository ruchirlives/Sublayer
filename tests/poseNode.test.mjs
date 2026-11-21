import test from "node:test";
import assert from "node:assert/strict";
import {graphPreview, referencedBy, relinkDependency} from "../src/fileRegistry.ts";
import {toolbarOperations} from "../src/graphInsertion.ts";
test("Pose library mappings and dependencies persist, relink and invalidate previews",()=>{
 const p={version:2,nodes:[{id:"pose",type:"stage",position:{x:0,y:0},data:{label:"Pose",operation:"pose",pose:{entries:[{id:"a",name:"Walk",dependencyId:"clip",animationPath:"/Anim",mode:"clip",poseTime:0}],mappings:[{id:"body",skeletonPath:"/Scene/Body",entryId:"a"}]}}}],edges:[],dependencies:[{id:"clip",kind:"usd",path:"I:/walk.usda"},{id:"other",kind:"usd",path:"I:/idle.usda"}]};
 assert.ok(toolbarOperations.includes("pose"));
 const initial=graphPreview(p,"pose");
 assert.equal(JSON.parse(initial).dependencies[0].id,"clip");
 assert.ok(referencedBy(p,"clip").some(n=>n.id==="pose"));
 const restored=JSON.parse(JSON.stringify(p));
 assert.equal(restored.nodes[0].data.pose.mappings[0].entryId,"a");
 restored.nodes[0].data.pose.mappings[0].entryId=null;
 assert.notEqual(graphPreview(restored,"pose"),initial);
 const relinked=relinkDependency(p,"clip","I:/idle.usda");
 assert.equal(relinked.nodes[0].data.pose.entries[0].dependencyId,"other");
});
