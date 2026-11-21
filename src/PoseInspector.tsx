import { useRef, useState } from "react";
import { invoke } from "@tauri-apps/api/core";
import { registerFiles, type PoseEntry, type PoseJoint, type PoseSettings, type Dependency } from "./fileRegistry";
import { NumberInput } from "./NumberInput";
import type { PoseEditing } from "./PoseViewportEditor";
import "./PoseInspector.css";

type Skeleton = {path:string; joints:string[]; poseJoints:PoseJoint[];blendShapes:string[];blendShapeWeights:number[]};
type Scan = {skeletons:Skeleton[]; animations:{path:string;startTime:number}[]};
const leaf = (path:string) => path.split(/[\\/]/).filter(Boolean).pop() || path;
const readable = (name:string) => name.replace(/_/g," ");
const animationLabel = (path:string) => `${readable(leaf(path.substring(0,path.lastIndexOf("/"))))} · ${readable(leaf(path))}`;

export function PoseInspector({value,graph,dependencies,onChange,onStatus,currentFrame,editing,onEdit}:{
 value?:PoseSettings|null; graph:string; dependencies:Dependency[];
 onChange:(v:PoseSettings,d?:Dependency[])=>void; onStatus:(s:string)=>void;
 currentFrame:number; editing:PoseEditing|null; onEdit:(edit:Omit<PoseEditing,"nodeId">|null)=>void;
}) {
 const cfg=value || {entries:[],mappings:[]};
 const latest=useRef({cfg,dependencies,onChange}); latest.current={cfg,dependencies,onChange};
 const [scan,setScan]=useState<Scan|null>(null);
 const [busy,setBusy]=useState<string|null>(null);
 const [sourcePath,setSourcePath]=useState("");
 const [captureSkeleton,setCaptureSkeleton]=useState("");
 const skeletons=scan?.skeletons || [];
 const available=(scan?.animations || []).filter(a=>!cfg.entries.some(e=>!e.dependencyId && e.animationPath===a.path));
 const selectedSource=available.some(a=>a.path===sourcePath)?sourcePath:available[0]?.path || "";
 const selectedSkeleton=skeletons.some(s=>s.path===captureSkeleton)?captureSkeleton:skeletons[0]?.path || "";
 const updateEntry=(id:string,patch:Partial<PoseEntry>)=>onChange({...cfg,entries:cfg.entries.map(e=>e.id===id?{...e,...patch}:e)});
 async function inspect(path?:string,capture=false):Promise<Scan> {
  const upstream=JSON.parse(graph);
  if(!capture)upstream.nodes=upstream.nodes.map((n:Record<string,unknown>)=>n.id===upstream.targetNodeId?{...n,pose:{entries:[],mappings:[]}}:n);
  return invoke<Scan>("usd_request",{request:{operation:"inspect_stage_skeletons",frame:currentFrame,...(path?{path}:{graph:upstream})}});
 }
 async function scanInput(){setBusy("Scanning input…");try{setScan(await inspect());}catch(e){onStatus(String(e));}finally{setBusy(null);}}
 async function load(){setBusy("Loading USD…");try{
  const {open}=await import("@tauri-apps/plugin-dialog");
  const file=await open({multiple:false,filters:[{name:"USD animations and poses",extensions:["usd","usda","usdc"]}]});
  if(typeof file!=="string")return;
  const found=await inspect(file);
  if(!found.animations.length)throw new Error("This USD has no SkelAnimation prims.");
  const current=latest.current;
  const registered=registerFiles(current.dependencies,[file],"usd");
  const entries=found.animations.map(a=>({id:crypto.randomUUID(),name:`${leaf(file)} · ${readable(leaf(a.path))}`,dependencyId:registered.ids[0],animationPath:a.path,mode:"clip" as const,poseTime:a.startTime}));
  current.onChange({...current.cfg,entries:[...current.cfg.entries,...entries]},registered.dependencies);
 }catch(e){onStatus(String(e));}finally{setBusy(null);}}
 function addUpstream(){const anim=available.find(a=>a.path===selectedSource);if(anim)onChange({...cfg,entries:[...cfg.entries,{id:crypto.randomUUID(),name:animationLabel(anim.path),animationPath:anim.path,mode:"clip",poseTime:anim.startTime}]});}
 async function createPose(){setBusy("Capturing current pose…");try{
  const result=await inspect(undefined,true);
  const skeleton=result.skeletons.find(s=>s.path===selectedSkeleton);
  if(!skeleton?.poseJoints.length)throw new Error("Select a valid skeleton and scan the input first.");
  const entry:PoseEntry={id:crypto.randomUUID(),name:`Pose ${latest.current.cfg.entries.filter(e=>e.joints).length+1}`,animationPath:"",mode:"pose",poseTime:currentFrame,joints:skeleton.poseJoints,blendShapes:skeleton.blendShapes,blendShapeWeights:skeleton.blendShapeWeights};
  const current=latest.current;const existing=current.cfg.mappings.find(r=>r.skeletonPath===skeleton.path);
  const mappings=existing?current.cfg.mappings.map(r=>r.id===existing.id?{...r,entryId:entry.id,enabled:true}:r):[...current.cfg.mappings,{id:crypto.randomUUID(),skeletonPath:skeleton.path,entryId:entry.id,enabled:true}];
  current.onChange({...current.cfg,entries:[...current.cfg.entries,entry],mappings});
  onEdit({entryId:entry.id,skeletonPath:skeleton.path,jointIndex:0});
 }catch(e){onStatus(String(e));}finally{setBusy(null);}}
 function editPose(entry:PoseEntry){const row=cfg.mappings.find(r=>r.entryId===entry.id);if(!row){onStatus("Map this pose to a skeleton before editing bones.");return;}onEdit({entryId:entry.id,skeletonPath:row.skeletonPath,jointIndex:0});}
 const unusedSkeleton=skeletons.find(s=>!cfg.mappings.some(r=>r.skeletonPath===s.path));
 return <div className="node-options pose-inspector">
  <div className="pose-toolbar"><button disabled={!!busy} onClick={scanInput}>Scan input</button><button disabled={!!busy} onClick={load}>Load USD…</button></div>
  {busy && <p className="pose-note" role="status">{busy}</p>}
  <section className="pose-section">
   <header><strong>Skeleton mappings</strong><span className="pose-count">{cfg.mappings.length}</span><button className="pose-text-button" disabled={!unusedSkeleton || !!busy} onClick={()=>unusedSkeleton && onChange({...cfg,mappings:[...cfg.mappings,{id:crypto.randomUUID(),skeletonPath:unusedSkeleton.path,entryId:null}]})}>+ Add row</button></header>
   {!cfg.mappings.length?<p className="pose-empty">{scan?"Add a row to assign an animation or pose.":"Scan the input to find its skeletons."}</p>:
   <table className="pose-mappings"><colgroup><col className="pose-check-col"/><col/><col/><col className="pose-action-col"/></colgroup><thead><tr><th aria-label="Enabled"/><th>Skeleton</th><th>Animation / pose</th><th/></tr></thead><tbody>
    {cfg.mappings.map(row=><tr key={row.id} className={row.enabled===false?"pose-muted-row":""}>
     <td><input type="checkbox" aria-label="Enable mapping" title="Enable this mapping" checked={row.enabled!==false} onChange={e=>onChange({...cfg,mappings:cfg.mappings.map(r=>r.id===row.id?{...r,enabled:e.target.checked}:r)})}/></td>
     <td><select aria-label="Target skeleton" title={row.skeletonPath} value={row.skeletonPath} onChange={e=>onChange({...cfg,mappings:cfg.mappings.map(r=>r.id===row.id?{...r,skeletonPath:e.target.value}:r)})}>
      {!skeletons.some(s=>s.path===row.skeletonPath)&&<option value={row.skeletonPath}>{readable(leaf(row.skeletonPath))}</option>}{skeletons.map(s=><option key={s.path} value={s.path}>{readable(leaf(s.path))}</option>)}
     </select></td>
     <td><select aria-label="Mapped animation or pose" title={cfg.entries.find(e=>e.id===row.entryId)?.name || "Keep upstream pose"} value={row.entryId || ""} onChange={e=>onChange({...cfg,mappings:cfg.mappings.map(r=>r.id===row.id?{...r,entryId:e.target.value || null}:r)})}><option value="">Upstream</option>{cfg.entries.map(e=><option key={e.id} value={e.id}>{e.name}</option>)}</select></td>
     <td><button className="pose-icon-button" aria-label="Remove mapping" title="Remove mapping" onClick={()=>onChange({...cfg,mappings:cfg.mappings.filter(r=>r.id!==row.id)})}>×</button></td>
    </tr>)}
   </tbody></table>}
   <p className="pose-note">Upstream keeps the original pose. Uncheck a row to bypass it.</p>
  </section>
  <section className="pose-section">
   <header><strong>Create pose</strong><span className="pose-count">Frame {currentFrame}</span></header>
   <div className="pose-source-row"><select aria-label="Capture skeleton" value={selectedSkeleton} onChange={e=>setCaptureSkeleton(e.target.value)} disabled={!skeletons.length}><option value="" disabled>Select skeleton</option>{skeletons.map(s=><option key={s.path} value={s.path}>{readable(leaf(s.path))}</option>)}</select><button disabled={!selectedSkeleton || !!busy} onClick={createPose}>Create pose</button></div>
   <p className="pose-note">Capture the current pose, then edit its bones in the viewport.</p>
  </section>
  <section className="pose-section">
   <header><strong>Pose & animation library</strong><span className="pose-count">{cfg.entries.length}</span></header>
   {!!available.length && <div className="pose-source-row"><select aria-label="Upstream animation" value={selectedSource} onChange={e=>setSourcePath(e.target.value)}>{available.map(a=><option key={a.path} value={a.path}>{animationLabel(a.path)}</option>)}</select><button onClick={addUpstream}>+ Add</button></div>}
   {!cfg.entries.length && <p className="pose-empty">Load a USD file, add an upstream animation, or create a pose.</p>}
   {cfg.entries.map(entry=><div className="pose-library-item" key={entry.id}><details open={editing?.entryId===entry.id || undefined}>
    <summary><span className="pose-entry-title" title={entry.name}>{entry.name}</span><span className="pose-badge">{entry.joints?"Editable pose":entry.mode==="clip"?"Clip":"Sampled pose"}</span></summary>
    <div className="pose-entry-body"><label>Name<input aria-label="Entry name" value={entry.name} onChange={e=>updateEntry(entry.id,{name:e.target.value})}/></label>
     {!entry.joints && <div className="pose-fields"><label>Playback<select value={entry.mode} onChange={e=>updateEntry(entry.id,{mode:e.target.value as "pose"|"clip"})}><option value="clip">Animation clip</option><option value="pose">Pose at frame</option></select></label>{entry.mode==="pose"&&<label>Frame<NumberInput value={entry.poseTime} onChange={e=>updateEntry(entry.id,{poseTime:Number(e.target.value)})}/></label>}</div>}
     {entry.joints && <button onClick={()=>editing?.entryId===entry.id?onEdit(null):editPose(entry)}>{editing?.entryId===entry.id?"Finish editing":"Edit bones in viewport"}</button>}
     {entry.joints && editing?.entryId===entry.id && (()=>{const joint=entry.joints[editing.jointIndex];return joint?<div className="pose-joint-editor"><label>Joint<select value={editing.jointIndex} onChange={e=>onEdit({...editing,jointIndex:Number(e.target.value)})}>{entry.joints.map((j,i)=><option key={j.name} value={i}>{leaf(j.name)}</option>)}</select></label>{(["rotationOffset","translationOffset"] as const).map(key=><div key={key}><span className="pose-note">{key==="rotationOffset"?"Local rotation offset (°)":"Local translation offset"}</span><div className="pose-axis-fields">{["X","Y","Z"].map((axis,index)=><label key={axis}>{axis}<NumberInput step={key==="rotationOffset"?1:0.01} value={joint[key][index]} onChange={e=>{const v=[...joint[key]] as [number,number,number];v[index]=Number(e.target.value);updateEntry(entry.id,{joints:entry.joints!.map((j,i)=>i===editing.jointIndex?{...j,[key]:v}:j)});}}/></label>)}</div></div>)}<button onClick={()=>updateEntry(entry.id,{joints:entry.joints!.map((j,i)=>i===editing.jointIndex?{...j,rotationOffset:[0,0,0],translationOffset:[0,0,0]}:j)})}>Reset joint offsets</button></div>:null;})()}
     <div className="pose-entry-actions"><button onClick={()=>onChange({...cfg,entries:[...cfg.entries,{...structuredClone(entry),id:crypto.randomUUID(),name:`${entry.name} copy`}]})}>Duplicate</button><button onClick={()=>{onChange({...cfg,entries:cfg.entries.filter(e=>e.id!==entry.id),mappings:cfg.mappings.map(r=>r.entryId===entry.id?{...r,entryId:null}:r)});if(editing?.entryId===entry.id)onEdit(null);}}>Delete</button></div>
     {!entry.joints && <details className="pose-source-details"><summary>Source path</summary><code>{entry.dependencyId?dependencies.find(d=>d.id===entry.dependencyId)?.path:"Upstream stage"}<br/>{entry.animationPath}</code></details>}
    </div>
   </details></div>)}
   <p className="pose-note">Loaded animations must use matching joint names.</p>
  </section>
 </div>;
}
