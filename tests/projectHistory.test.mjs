import test from "node:test";
import assert from "node:assert/strict";
import {ProjectHistory,historyShortcut} from "../src/projectHistory.ts";
const p=(x=0)=>({version:2,nodes:[{id:"a",type:"stage",position:{x,y:0},data:{operation:"source",label:"a"}}],edges:[],dependencies:[],viewport:{pinnedNodeId:null}});
test("undo/redo groups drag updates, restores deletion with edges and pin, and branches safely",()=>{
 const h=new ProjectHistory();h.record(p(),p(1),"Move","drag");h.record(p(1),p(2),"Move","drag");h.endGroup();assert.equal(h.past.length,1);
 assert.equal(h.undo(p(2)).project.nodes[0].position.x,0);assert.equal(h.redo(p()).project.nodes[0].position.x,2);
 const full=p(2);full.edges=[{id:"e",source:"a",target:"b"}];full.viewport.pinnedNodeId="a";
 const deleted={...p(),nodes:[],edges:[],viewport:{pinnedNodeId:null}};h.record(full,deleted,"Delete");assert.deepEqual(h.undo(deleted).project,full);
 h.record(full,p(9),"New edit");assert.equal(h.future.length,0);assert.equal(h.redo(p(9)),null);
});
test("history ignores selection and measurements, snapshots immutably, and bounds entries",()=>{
 const h=new ProjectHistory(2),a=p(),b=p();b.nodes[0].selected=true;b.nodes[0].measured={width:30,height:20};assert.equal(h.record(a,b),false);
 for(let i=0;i<4;i++)h.record(p(i),p(i+1));assert.equal(h.past.length,2);
 const restored=h.undo(p(4));restored.project.nodes[0].data.label="mutated";assert.equal(h.redo(restored.project).project.nodes[0].position.x,4);
 h.clear();assert.equal(h.past.length,0);assert.equal(h.future.length,0);
});
test("shortcuts preserve native text editing and support both redo variants",()=>{
 const event={key:"z",ctrlKey:true,metaKey:false,shiftKey:false,altKey:false};assert.equal(historyShortcut(event,false),"undo");assert.equal(historyShortcut(event,true),null);assert.equal(historyShortcut({...event,shiftKey:true},false),"redo");assert.equal(historyShortcut({...event,key:"y"},false),"redo");assert.equal(historyShortcut({...event,altKey:true},false),null);
});
