import {useEffect,useId,useRef,useState,type ReactNode} from "react";
import {invoke,isTauri} from "@tauri-apps/api/core";
type Prim={path:string;name:string;type:string;instance:boolean;instanceProxy:boolean;hasChildren:boolean};
type Children={children:Prim[];nextOffset:number|null};
export function HandoffSelection({graph,selection,onChange,disabled}:{graph:string;selection?:string[]|null;onChange:(value:string[]|null)=>void;disabled:boolean}){
 const [open,setOpen]=useState(false),[search,setSearch]=useState(""),[rows,setRows]=useState<Record<string,Children>>({}),[expanded,setExpanded]=useState<Set<string>>(new Set(["/"])),[busy,setBusy]=useState(false),[error,setError]=useState("");
 const generation=useRef(0);
 const modeName=useId();
 async function load(parent:string,offset=0,token=generation.current){
  setBusy(true);
  try{
   const value=await invoke<Children>("usd_request",{request:{operation:"scene_children",graph,parent,offset,search}});
   if(token!==generation.current)return;
   setRows(old=>({...old,[parent]:{...value,children:offset?[...(old[parent]?.children || []),...value.children]:value.children}}));setError("");
  }catch(e){if(token===generation.current)setError(String(e));}finally{if(token===generation.current)setBusy(false);}
 }
 useEffect(()=>{
  generation.current++;setRows({});setExpanded(new Set(["/"]));setError("");
  if(!open){setBusy(false);return;}
  const token=generation.current;const timer=setTimeout(()=>void load("/",0,token),search?300:0);
  return ()=>{clearTimeout(timer);generation.current++;};
 },[graph,search,open]);
 function toggle(path:string){
  const chosen=selection || [];
  onChange(chosen.includes(path)?chosen.filter(p=>p!==path):[...chosen.filter(p=>!p.startsWith(path+"/")),path]);
 }
 function tree(parent:string,depth:number):ReactNode{return <ul className="handoff-tree-list">{rows[parent]?.children.map(prim=>{
  const chosen=selection || [];
  const ancestor=chosen.find(p=>prim.path.startsWith(p+"/"));
  const direct=chosen.includes(prim.path);
  const partial=!ancestor&&!direct&&chosen.some(p=>p.startsWith(prim.path+"/"));
  const isExpanded=expanded.has(prim.path);
  const detail=ancestor?"Included by parent":prim.instanceProxy?"Select its containing instance":prim.instance?"Whole instance":partial?"Contains selections":"";
  return <li key={prim.path}>
   <div className={"handoff-object-row "+(direct||ancestor?"included":"")} style={{paddingLeft:search?8:8+depth*18}}>
    {!search&&prim.hasChildren?<button className="handoff-expand" type="button" aria-label={`${isExpanded?"Collapse":"Expand"} ${prim.name}`} aria-expanded={isExpanded} disabled={busy} onClick={()=>{
     setExpanded(old=>{const next=new Set(old);next.has(prim.path)?next.delete(prim.path):next.add(prim.path);return next;});
     if(!isExpanded&&!rows[prim.path])void load(prim.path);
    }}>{isExpanded?"▾":"▸"}</button>:<span className="handoff-expand-space" aria-hidden="true"/>}
    <label className="handoff-object-label" title={ancestor?`Included by ${ancestor}. Remove that selection to choose its children individually.`:prim.instanceProxy?`${prim.path} · Select the containing instance instead.`:prim.path}>
     <input type="checkbox" ref={input=>{if(input)input.indeterminate=partial;}} checked={!!ancestor||direct} disabled={disabled||!!ancestor||prim.instanceProxy} onChange={()=>toggle(prim.path)}/>
     <span className="handoff-object-text"><span className="handoff-object-name">{prim.name}</span>{search&&<small className="handoff-object-path">{prim.path}</small>}</span>
     <span className="handoff-object-type">{prim.type}</span>
    </label>
    {detail&&<small className="handoff-object-state">{detail}</small>}
   </div>
   {!search&&isExpanded&&tree(prim.path,depth+1)}
  </li>;
 })}{rows[parent]?.nextOffset!=null&&<li><button className="handoff-more" disabled={busy} onClick={()=>void load(parent,rows[parent].nextOffset!)}>Show more objects…</button></li>}</ul>;}
 const chosen=selection || [];
 return <details className="node-options handoff-selection"><summary>Contents · {selection==null?"Whole scene":`${selection.length} selections`}</summary>
  <div className="handoff-modes">
   <label><input type="radio" name={modeName} checked={selection==null} disabled={disabled} onChange={()=>onChange(null)}/> Whole scene</label>
   <label><input type="radio" name={modeName} checked={selection!=null} disabled={disabled} onChange={()=>onChange(selection || [])}/> Selected objects</label>
  </div>
  {selection!=null&&<><button disabled={disabled||!isTauri()} onClick={()=>setOpen(true)} title="Selections include children and required materials, rigs and parent transforms. Unselected objects remain in the live output.">Choose objects…</button>{!selection.length&&<p className="hint">Choose at least one object.</p>}
   {selection.map(path=><div className="handoff-selection-chip" key={path} title={path}><span>{path.split("/").pop()}</span><button type="button" disabled={disabled} aria-label={`Remove ${path}`} onClick={()=>toggle(path)}>×</button></div>)}
  </>}

  {open&&<div className="override-backdrop" onKeyDown={event=>{if(event.key==="Escape")setOpen(false);}}><section className="override-modal handoff-picker" role="dialog" aria-modal="true" aria-labelledby={`${modeName}-title`}>
   <div className="handoff-picker-heading"><h2 id={`${modeName}-title`}>Choose handoff objects</h2><button type="button" onClick={()=>setOpen(false)} aria-label="Close object picker">×</button></div>
   <p className="hint">Tick an object to include it and all its children. Use the arrows to explore the scene.</p>
   <div className="handoff-picker-body">
    <section className="handoff-object-browser" aria-label="Available objects">
     <div className="handoff-search"><input autoFocus aria-label="Search handoff objects" placeholder="Search objects by name or path…" value={search} onChange={e=>setSearch(e.target.value)}/>{search&&<button type="button" onClick={()=>setSearch("")}>Clear search</button>}</div>
     <div className="handoff-tree-heading"><strong>{search?"Search results":"Scene hierarchy"}</strong>{!search&&<button type="button" onClick={()=>setExpanded(new Set(["/"]))}>Collapse all</button>}</div>
     <div className="handoff-object-tree" aria-busy={busy}>
      {tree("/",0)}
      {busy&&<p className="hint" role="status">Loading objects…</p>}
      {!busy&&rows["/"]&&!rows["/"].children.length&&<p className="hint">{search?"No matching objects. Try another name.":"No objects available."}</p>}
     </div>
    </section>
    <section className="handoff-selected" aria-label="Selected objects">
     <div className="handoff-selected-heading"><strong>Selected ({chosen.length})</strong><button type="button" disabled={disabled||!chosen.length} onClick={()=>onChange([])}>Clear all</button></div>
     <p className="hint">Each selection includes its entire subtree.</p>
     {!chosen.length?<p className="handoff-selection-empty">Nothing selected yet.<br/>Tick objects in the scene list.</p>:<ul className="handoff-selected-list">{chosen.map(path=><li key={path}><div><strong>{path.split("/").pop()}</strong><small>{path}</small></div><button type="button" disabled={disabled} aria-label={`Remove ${path}`} onClick={()=>toggle(path)}>×</button></li>)}</ul>}
    </section>
   </div>
   {error&&<p role="alert">{error}</p>}
   <div className="handoff-picker-footer"><span>{chosen.length?`${chosen.length} selections · children included`:"No objects selected"}</span><button className="primary" type="button" onClick={()=>setOpen(false)}>Done</button></div>
  </section></div>}
 </details>;
}
