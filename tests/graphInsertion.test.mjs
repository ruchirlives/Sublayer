import test from "node:test";
import assert from "node:assert/strict";
import {detachNodeFromEdges, insertExistingNodeOnEdge, insertToolbarNode} from "../src/graphInsertion.ts";
const node=(id,operation,x,y=0)=>({id,type:"stage",position:{x,y},data:{operation}});
const project=()=>({version:2,viewport:{pinnedNodeId:"d"},dependencies:[],nodes:[node("s","source",0),node("a","transform",350),node("b","output",700,50),node("d","output",1050),node("other","source",0,400)],edges:[{id:"sa",source:"s",target:"a",sourceHandle:"out",targetHandle:"in"},{id:"ab",source:"a",target:"b"},{id:"ad",source:"a",target:"d"},{id:"bd",source:"b",target:"d"}]});
test("edge insertion preserves downstream edge identity, shifts all descendants once, and keeps unrelated nodes",()=>{
 const p=project(),before=structuredClone(p),added=node("new","transform",99);
 const next=insertToolbarNode(p,added,{kind:"edge",id:"sa"},()=>"newedge");
 assert.deepEqual(p,before);
 assert.deepEqual(next.nodes.find(n=>n.id==="new").position,{x:350,y:0});
 for(const id of ["a","b","d"]){const old=p.nodes.find(n=>n.id===id),moved=next.nodes.find(n=>n.id===id);assert.equal(moved.position.x-old.position.x,346);assert.equal(moved.position.y,old.position.y);}
 assert.equal(next.nodes.find(n=>n.id==="other").position.x,0);
 assert.equal(next.edges.find(e=>e.id==="sa").source,"new");
 assert.equal(next.edges.find(e=>e.id==="newedge").source,"s");
 assert.equal(next.edges.find(e=>e.id==="newedge").sourceHandle,"out");
 assert.equal(next.edges.find(e=>e.id==="sa").targetHandle,"in");
 assert.equal(next.viewport.pinnedNodeId,"d");
});
test("Non-Output targets connect downstream; Output targets connect upstream",()=>{
 const p=project(),added=node("new","application",99);
 const afterSource=insertToolbarNode(p,added,{kind:"node",id:"s"},()=>"newedge");
 assert.deepEqual(afterSource.edges.at(-1),{id:"newedge",source:"s",target:"new"});
 assert.equal(afterSource.nodes.find(n=>n.id==="s").position.x,0);
 for(const operation of ["transform","application","assetOverride","materialx"]){
  const variant=structuredClone(p);variant.nodes.find(n=>n.id==="a").data.operation=operation;
  const next=insertToolbarNode(variant,added,{kind:"node",id:"a"},()=>"newedge");
  assert.deepEqual(next.edges.at(-1),{id:"newedge",source:"a",target:"new"});
  assert.deepEqual(next.nodes.find(n=>n.id==="new").position,{x:696,y:0});
  assert.equal(next.nodes.find(n=>n.id==="a").position.x,350);
  for(const id of ["b","d"])assert.equal(next.nodes.find(n=>n.id===id).position.x,variant.nodes.find(n=>n.id===id).position.x+346);
  assert.equal(next.nodes.find(n=>n.id==="other").position.x,0);
  assert.deepEqual(next.edges.slice(0,p.edges.length),p.edges);
 }
 const beforeOutput=insertToolbarNode(p,added,{kind:"node",id:"b"},()=>"newedge");
 assert.deepEqual(beforeOutput.edges.at(-1),{id:"newedge",source:"new",target:"b"});
 assert.equal(beforeOutput.nodes.find(n=>n.id==="a").position.x,350);
 assert.equal(beforeOutput.nodes.find(n=>n.id==="d").position.x,1396);
});
test("empty-canvas drop preserves exact position and missing targets leave project untouched",()=>{
 const p=project(),added=node("new","source",100,200);
 const next=insertToolbarNode(p,added,null);
 assert.deepEqual(next.nodes.at(-1).position,{x:100,y:200});
 assert.equal(next.edges.length,p.edges.length);
 assert.throws(()=>insertToolbarNode(p,added,{kind:"edge",id:"deleted"}));
 assert.throws(()=>insertToolbarNode(p,node("a","source",0),null));
});
test("detachNodeFromEdges removes node links and reconnects upstream to downstream",()=>{
 const p=project();
 let n=0;
 const next=detachNodeFromEdges(p,"a",()=>`re-${++n}`);
 assert.equal(next.edges.some(e=>e.source==="a"||e.target==="a"),false);
 assert.equal(next.edges.some(e=>e.source==="s"&&e.target==="b"),true);
 assert.equal(next.edges.some(e=>e.source==="s"&&e.target==="d"),true);
 assert.equal(next.edges.some(e=>e.id==="bd"),true);
});
test("insertExistingNodeOnEdge splits target edge and rejects cycles",()=>{
 const p=project();
 const detached=detachNodeFromEdges(p,"a",()=>"sb");
 let n=0;
 const inserted=insertExistingNodeOnEdge(detached,"a","bd",()=>`ins-${++n}`);
 assert.equal(inserted.edges.some(e=>e.id==="bd"),false);
 assert.equal(inserted.edges.some(e=>e.source==="b"&&e.target==="a"),true);
 assert.equal(inserted.edges.some(e=>e.source==="a"&&e.target==="d"),true);
 const incidentAttempt=insertExistingNodeOnEdge(p,"b","bd",()=>"inc");
 assert.deepEqual(incidentAttempt.edges,p.edges);
});
