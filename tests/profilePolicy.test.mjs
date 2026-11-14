import test from "node:test";
import assert from "node:assert/strict";
import {readFileSync} from "node:fs";
import {scopeCapability,updateApplicationProfile,parseApplicationCatalog} from "../src/profilePolicy.ts";
const defaults=()=>JSON.parse(readFileSync(new URL("../src-tauri/application-defaults.json",import.meta.url),"utf8"));
test("unverified and unsupported capabilities are disabled with reasons",()=>{
 const profile=defaults().applications[0];
 assert.equal(scopeCapability(profile,"transforms").enabled,false);
 profile.capabilities.transforms={status:"supported",reason:"Verified fixture"};
 assert.equal(scopeCapability(profile,"transforms").enabled,true);
 profile.capabilities.transforms.status="unsupported";
 assert.equal(scopeCapability(profile,"transforms").enabled,false);
 assert.equal(scopeCapability(undefined,"transforms").status,"unknown");
});
test("table editing advances revision without mutating defaults or snapshots",()=>{
 const base=defaults(),snapshot=structuredClone(base.applications[0]);
 const edited=updateApplicationProfile(base,base,"blender",{metersPerUnit:0.01});
 assert.equal(edited.applications[0].revision,base.applications[0].revision+1);
 const second=updateApplicationProfile(edited,base,"blender",{scale:2});
 assert.equal(second.applications[0].revision,base.applications[0].revision+1);
 assert.deepEqual(base.applications[0],snapshot);
 assert.equal(base.applications[0].metersPerUnit,1);
});
test("changing app version invalidates previous capability assertions",()=>{
 const base=defaults();base.applications[0].capabilities.transforms.status="supported";
 const next=updateApplicationProfile(base,base,"blender",{applicationVersion:"5.3.x"});
 assert.equal(next.applications[0].capabilities.transforms.status,"unverified");
 assert.equal(base.applications[0].capabilities.transforms.status,"supported");
});
test("JSON table view rejects malformed records without crashing",()=>{
 assert.equal(parseApplicationCatalog(JSON.stringify(defaults())).applications.length,4);
 assert.throws(()=>parseApplicationCatalog('{"version":1,"applications":[null]}'));
 assert.throws(()=>parseApplicationCatalog('{"version":2,"applications":[]}'));
 const bad=defaults();delete bad.applications[0].capabilities.points;
 assert.throws(()=>parseApplicationCatalog(JSON.stringify(bad)));
});

test("bundled application templates are fail closed and added without overwriting custom profiles",async()=>{
 const {readFileSync}=await import("node:fs"),{addMissingProfiles}=await import("../src/profilePolicy.ts");
 const bundled=JSON.parse(readFileSync(new URL("../src-tauri/application-defaults.json",import.meta.url),"utf8"));
 assert.deepEqual(bundled.applications.map(p=>p.id),["blender","zbrush","marvelous-designer","substance-painter"]);
 const saved={version:1,applications:[{...bundled.applications[1],executable:"I:/custom/zbrush.exe",revision:7}]};
 const merged=addMissingProfiles(saved,bundled);assert.equal(merged.applications.length,4);assert.equal(merged.applications[0].revision,7);assert.equal(merged.applications[0].executable,saved.applications[0].executable);
 for(const p of bundled.applications.slice(1))for(const c of Object.values(p.capabilities))assert.notEqual(c.status,"supported");
});

test("saving an executable default keeps capabilities and advances only current profile revision",()=>{
 const catalog=defaults(),old=structuredClone(catalog),profile=catalog.applications[0];
 const next=updateApplicationProfile(catalog,catalog,profile.id,{executable:"C:/Apps/Blender/blender.exe"});
 assert.equal(next.applications[0].revision,profile.revision+1);assert.deepEqual(next.applications[0].capabilities,profile.capabilities);
 assert.deepEqual(catalog,old);assert.deepEqual(next.applications.slice(1),catalog.applications.slice(1));
});

test("new node scopes default to supported capabilities only, without mutating explicit scope or snapshots",async()=>{
 const {defaultEditScope}=await import("../src/profilePolicy.ts"),catalog=defaults(),profile=catalog.applications[0];
 assert.deepEqual(defaultEditScope(profile),[]);profile.capabilities.points.status="supported";profile.capabilities.materials.status="supported";
 assert.deepEqual(defaultEditScope(profile),["points","materials"]);assert.deepEqual(defaultEditScope(undefined),[]);
 const existing={editScope:[],nativeImport:{policy:{editScope:[]}}};defaultEditScope(profile);assert.deepEqual(existing.editScope,[]);assert.deepEqual(existing.nativeImport.policy.editScope,[]);
});
