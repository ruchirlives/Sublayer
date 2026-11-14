import {defaultEditScope,preparationProfileSignature,addMissingProfiles,editCategories,scopeCapability,updateApplicationProfile,parseApplicationCatalog} from "./profilePolicy";
import {createContext,useContext,useEffect,useState,type ReactNode} from "react";
import {invoke,isTauri} from "@tauri-apps/api/core";
import type {Data} from "./fileRegistry";
import {NumberInput} from "./NumberInput";
import defaults from "../src-tauri/application-defaults.json";

export type Capability={status:"supported"|"unsupported"|"unverified";reason:string};
export type ApplicationProfile={id:string;revision:number;app:string;applicationVersion:string;adapterVersion:number;upAxis:"Y"|"Z";metersPerUnit:number;scale:number;executable:string;importBehavior:string;exportBehavior:string;capabilities:Record<string,Capability>};
export type ApplicationCatalog={version:number;applications:ApplicationProfile[]};
export type PolicySnapshot={profile:ApplicationProfile;editScope:string[]};
export function findProfile(catalog:ApplicationCatalog,data:Pick<Data,"app"|"applicationProfileId">){return catalog.applications.find(p=>p.id===data.applicationProfileId);}
const Context=createContext<{catalog:ApplicationCatalog;path:string;error:string;reload:()=>Promise<void>;save:(value:ApplicationCatalog)=>Promise<void>}>({catalog:{version:1,applications:[]},path:"",error:"",reload:async()=>{},save:async()=>{}});
export const useApplicationCatalog=()=>useContext(Context);
export function ApplicationCatalogProvider({children}:{children:ReactNode}){
 const [catalog,setCatalog]=useState<ApplicationCatalog>(isTauri()?{version:1,applications:[]}:defaults as ApplicationCatalog);
 const [path,setPath]=useState(""),[error,setError]=useState("");
 async function reload(){try{const value=await invoke<{catalog:ApplicationCatalog;path:string}>("load_application_profiles");setCatalog(value.catalog);setPath(value.path);setError("");}catch(e){setCatalog({version:1,applications:[]});setError(String(e));}}
 useEffect(()=>{if(isTauri())void reload();},[]);
 async function save(value:ApplicationCatalog){await invoke("save_application_profiles",{catalog:value});await reload();}
 return <Context.Provider value={{catalog,path,error,reload,save}}>{children}</Context.Provider>;
}
export function ApplicationSettings({focusProfileId,focusVisit}:{focusProfileId?:string;focusVisit?:number}){
 const state=useApplicationCatalog();
 const [text,setText]=useState(JSON.stringify(state.catalog,null,2)),[dirty,setDirty]=useState(false),[message,setMessage]=useState(""),[busy,setBusy]=useState(false),[selected,setSelected]=useState("");
 useEffect(()=>{if(!dirty)setText(JSON.stringify(state.catalog,null,2));},[state.catalog,dirty]);
 let draft:ApplicationCatalog|null=null,parseError="";
 try{draft=parseApplicationCatalog(text);}catch(e){parseError=String(e);}
 function edit(mutator:(value:ApplicationCatalog)=>void){if(!draft)return;const next=structuredClone(draft);mutator(next);setText(JSON.stringify(next,null,2));setDirty(true);}
 function update(id:string,patch:Partial<ApplicationProfile>){if(!draft)return;setText(JSON.stringify(updateApplicationProfile(draft,state.catalog,id,patch),null,2));setDirty(true);}
 useEffect(()=>{if(focusProfileId)setSelected(focusProfileId);},[focusProfileId,focusVisit]);
 const profile=draft?.applications.find(p=>p.id===selected)||draft?.applications[0];
 return <section className="applications-tab">
  <div className="dependency-tools"><div><h1>Applications</h1><p>Shared application-level JSON. Node edit scopes remain in each node; no project-level configuration.</p></div>
   <button disabled={busy||!isTauri()} onClick={async()=>{setBusy(true);try{await state.save(JSON.parse(text));setDirty(false);setMessage("Saved shared application JSON. Existing handoff/native snapshots are unchanged.");}catch(e){setMessage(String(e));}finally{setBusy(false);}}}>Save application JSON{dirty?" *":""}</button>
   <button disabled={busy} onClick={async()=>{setBusy(true);try{const value=await invoke<{catalog:ApplicationCatalog}>("load_application_profiles");setDirty(false);setText(JSON.stringify(value.catalog,null,2));await state.reload();setMessage("Reloaded application configuration; local edits discarded.");}catch(e){setMessage(String(e));}finally{setBusy(false);}}}>Reload / discard edits</button>
   <button onClick={()=>edit(value=>{let id="new-application",i=1;while(value.applications.some(p=>p.id===id))id=`new-application-${i++}`;const p=structuredClone(defaults.applications[0]) as ApplicationProfile;p.id=id;p.app="New application";p.revision=1;p.applicationVersion="Unverified";p.executable="";p.importBehavior="Not configured";p.exportBehavior="Not configured";value.applications.push(p);setSelected(id);})}>Add application</button>
   <button onClick={()=>{if(draft){setText(JSON.stringify(addMissingProfiles(draft,defaults as ApplicationCatalog),null,2));setDirty(true);setMessage("Added missing bundled profiles; existing profiles were preserved. Save to apply.");}}}>Add missing bundled profiles</button>
   <button onClick={()=>{setText(JSON.stringify(defaults,null,2));setDirty(true);setMessage("Bundled template loaded locally. Increase revisions before saving over existing profiles.");}}>Load bundled template</button>
  </div>
  <p className="file-location">File: {state.path || "Desktop application configuration directory / applications.json"}</p>
  <p className="hint">Table edits advance the profile revision once per saved revision. IDs are stable node references: changing or removing an ID makes nodes using it unavailable. Declaring support does not implement a missing adapter or publishing operation. Behavior notes document the adapter contract; changing notes does not change its executable import/export options.</p>
  {state.error && <p className="missing-file" role="alert">{state.error}</p>}
  {parseError && <p role="alert">{parseError}</p>}
  {draft && <>
   <div className="dependency-grid"><table><thead><tr>{["Profile ID","Application","App version","Revision","Adapter version","Up axis","Meters/unit","Default scale","Executable","Import behavior notes","Export behavior notes","Actions"].map(t=><th key={t}>{t}</th>)}</tr></thead><tbody>
    {draft.applications.map((p,index)=><tr key={index} className={profile?.id===p.id?"profile-active":""}>
     <td><input aria-label={`Profile ID ${index+1}`} value={p.id} onChange={e=>update(p.id,{id:e.target.value})}/></td>
     <td><input aria-label={`Application ${p.id}`} value={p.app} onChange={e=>update(p.id,{app:e.target.value})}/></td>
     <td><input title="Exact version or wildcard, e.g. 5.2.x" aria-label={`App version ${p.id}`} value={p.applicationVersion} onChange={e=>update(p.id,{applicationVersion:e.target.value})}/></td>
     <td><NumberInput aria-label={`Revision ${p.id}`} min="1" step={1} value={p.revision} onChange={e=>update(p.id,{revision:Number(e.target.value)})}/></td>
     <td><NumberInput aria-label={`Adapter version ${p.id}`} min="1" step={1} value={p.adapterVersion} onChange={e=>update(p.id,{adapterVersion:Number(e.target.value)})}/></td>
     <td><select aria-label={`Up axis ${p.id}`} value={p.upAxis} onChange={e=>update(p.id,{upAxis:e.target.value as "Y"|"Z"})}><option>Y</option><option>Z</option></select></td>
     <td><NumberInput aria-label={`Meters per unit ${p.id}`} min="0.000001" step="0.001" value={p.metersPerUnit} onChange={e=>update(p.id,{metersPerUnit:Number(e.target.value)})}/></td>
     <td><NumberInput aria-label={`Default scale ${p.id}`} min="0.000001" step="0.1" value={p.scale} onChange={e=>update(p.id,{scale:Number(e.target.value)})}/></td>
     <td><input aria-label={`Executable ${p.id}`} value={p.executable} onChange={e=>update(p.id,{executable:e.target.value})}/><button disabled={!isTauri()} onClick={async()=>{try{const {open}=await import("@tauri-apps/plugin-dialog");const path=await open({multiple:false,filters:[{name:"Executable",extensions:["exe"]}]});if(path)update(p.id,{executable:path});}catch(e){setMessage(String(e));}}}>Browse…</button></td>
     <td><textarea aria-label={`Import behavior ${p.id}`} value={p.importBehavior} onChange={e=>update(p.id,{importBehavior:e.target.value})}/></td>
     <td><textarea aria-label={`Export behavior ${p.id}`} value={p.exportBehavior} onChange={e=>update(p.id,{exportBehavior:e.target.value})}/></td>
     <td><button onClick={()=>setSelected(p.id)}>Edit capabilities</button><button onClick={()=>edit(v=>{v.applications.splice(index,1);})}>Remove</button></td>
    </tr>)}
   </tbody></table></div>
   {profile && <><h2>{profile.app} — USD edit capabilities</h2><div className="dependency-grid"><table><thead><tr><th>Category</th><th>Status</th><th>Reason / verification evidence</th></tr></thead><tbody>
    {editCategories.map(key=><tr key={key}><td>{names[key]}</td><td><select aria-label={`${key} capability status`} value={profile.capabilities?.[key]?.status || "unverified"} onChange={e=>update(profile.id,{capabilities:{...profile.capabilities,[key]:{status:e.target.value as Capability["status"],reason:profile.capabilities?.[key]?.reason || "Verification required"}}})}><option>supported</option><option>unsupported</option><option>unverified</option></select></td><td><input aria-label={`${key} capability reason`} value={profile.capabilities?.[key]?.reason || ""} onChange={e=>update(profile.id,{capabilities:{...profile.capabilities,[key]:{status:profile.capabilities?.[key]?.status || "unverified",reason:e.target.value}}})}/></td></tr>)}
   </tbody></table></div></>}
  </>}
  <details><summary>Full JSON editor</summary><textarea className="catalog-json" spellCheck={false} aria-label="Application profile JSON" value={text} onChange={e=>{setText(e.target.value);setDirty(true);}}/></details>
  <p role="status">{message}</p>
 </section>;
}
const names:Record<string,string>={transforms:"Transforms",points:"Geometry points",topology:"Topology",uvs:"UVs",materials:"Materials and bindings",animation:"Animation",skeletons:"Skeletons and skinning",deletions:"Explicit deletions"};
export function NodeEditScope({data,onChange,onConfigure}:{data:Data;onChange:(patch:Partial<Data>)=>void;onConfigure:()=>void}){
 const {catalog}=useApplicationCatalog(),profile=findProfile(catalog,data),chosen=data.editScope || [];
 const snapshot=data.publicationPolicy || data.manualBaseline?.policy || data.nativeImport?.policy || data.nativePreparation?.policy || data.handoff?.policy;
 const changed=snapshot && (preparationProfileSignature(snapshot.profile)!==preparationProfileSignature(profile)||JSON.stringify(snapshot.editScope)!==JSON.stringify(chosen));
 return <details className="node-options"><summary>Allowed edits ({chosen.length})</summary>
  <div className="compact-scopes">{editCategories.filter(key=>scopeCapability(profile,key).enabled || chosen.includes(key)).map(key=>{const enabled=scopeCapability(profile,key).enabled;return <label key={key} title={profile?.capabilities[key]?.reason}><input type="checkbox" checked={chosen.includes(key)} disabled={!enabled} onChange={e=>onChange({editScope:e.target.checked?[...chosen,key]:chosen.filter(s=>s!==key)})}/>{names[key]}</label>;})}</div>
  {!editCategories.some(key=>scopeCapability(profile,key).enabled)&&<p className="hint">No verified edit capabilities are enabled for this application.</p>}
  <button disabled={!defaultEditScope(profile).length} onClick={()=>onChange({editScope:defaultEditScope(profile)})}>Allow supported</button>
  <button onClick={onConfigure}>Application settings</button>
  {!!chosen.length&&<button onClick={()=>onChange({editScope:[]})}>Preserve all</button>}
  {changed&&<p className="hint">Apply updated permissions before reviewing.</p>}
 </details>;
}
