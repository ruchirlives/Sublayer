import {useEffect,useMemo,useRef,useState} from "react";
import {invoke,isTauri} from "@tauri-apps/api/core";
import {MaterialInspector} from "./MaterialInspector";
import {sceneHierarchyKey} from "./previewTarget";
type Prim={path:string;name:string;type:string;instance:boolean;instanceProxy:boolean;hasChildren:boolean};
type Children={children:Prim[];nextOffset:number|null};
export function SceneOverview({path,graph,selected,onSelect,onAddToMaterialX,isolatedPrim,hiddenPrims,onHidePrim,onShowPrim,onIsolatePrim,onShowAll,onStatus}:{path:string;graph?:string;selected:string;onSelect:(path:string)=>void;onAddToMaterialX?:(primPath:string)=>void;isolatedPrim?:string|null;hiddenPrims?:string[];onHidePrim?:(primPath:string)=>void;onShowPrim?:(primPath:string)=>void;onIsolatePrim?:(primPath:string)=>void;onShowAll?:()=>void;onStatus:(message:string)=>void}){
 const [rows,setRows]=useState<Record<string,Children>>({}),[expanded,setExpanded]=useState<Set<string>>(new Set(["/"])),[error,setError]=useState(""),[busy,setBusy]=useState<Set<string>>(new Set()),[revision,setRevision]=useState(0),[collapsed,setCollapsed]=useState(false);
 const [contextMenu,setContextMenu]=useState<{x:number;y:number;prim:Prim}|null>(null);
 useEffect(()=>{
  if(!contextMenu)return;
  const close=(e:MouseEvent)=>{
   const target=e.target as HTMLElement|null;
   if(target&&target.closest(".scene-context-menu"))return;
   setContextMenu(null);
  };
  const onKey=(e:KeyboardEvent)=>{if(e.key==="Escape")setContextMenu(null);};
  window.addEventListener("pointerdown",close);
  window.addEventListener("keydown",onKey);
  return ()=>{window.removeEventListener("pointerdown",close);window.removeEventListener("keydown",onKey);};
 },[contextMenu]);
 const generation=useRef(0);
 const busyRef=useRef<Set<string>>(new Set());
 async function load(parent:string,offset=0,reload=false){const token=generation.current;busyRef.current.add(parent);setBusy(v=>new Set(v).add(parent));try{const result=await invoke<Children>("usd_request",{request:{operation:"scene_children",path,graph,parent,offset,reload}});if(token!==generation.current)return;setRows(v=>({...v,[parent]:{...result,children:offset?[...(v[parent]?.children || []),...result.children]:result.children}}));setError("");}catch(e){if(token===generation.current)setError(String(e));}finally{busyRef.current.delete(parent);if(token===generation.current)setBusy(v=>{const next=new Set(v);next.delete(parent);return next;});}}
 const previousRevision = useRef(0);
 const hierarchyKey = useMemo(() => sceneHierarchyKey(graph), [graph]);
 useEffect(()=>{generation.current++;busyRef.current.clear();setRows({});setExpanded(new Set(["/"]));setBusy(new Set());setError("");const isExplicitRefresh=revision>0 && revision!==previousRevision.current;previousRevision.current=revision;if(path && !collapsed && isTauri())void load("/",0,isExplicitRefresh);return ()=>{generation.current++;};},[path,hierarchyKey,revision]);
 useEffect(()=>{if(!collapsed && path && !rows["/"] && !busyRef.current.has("/") && isTauri())void load("/");},[collapsed,path]);

 // Auto-expand and scroll to selected prim whenever selection changes
 useEffect(()=>{
  if(!selected || !selected.startsWith("/"))return;
  const segments = selected.split("/").filter(Boolean);
  const ancestors = ["/"];
  let cur = "";
  for(let i=0; i<segments.length-1; i++){
    cur += "/" + segments[i];
    ancestors.push(cur);
  }
  setExpanded(prev => {
    let changed = false;
    const next = new Set(prev);
    for(const a of ancestors){
      if(!next.has(a)){ next.add(a); changed = true; }
    }
    return changed ? next : prev;
  });
  // Load any ancestor that hasn't loaded its children yet
  for(const a of ancestors){
    if(!rows[a] && !busyRef.current.has(a)) void load(a);
  }
  // Scroll into view
  const timer = setTimeout(()=>{
    const el = document.querySelector(`[data-prim-path="${CSS.escape(selected)}"]`);
    if(el) el.scrollIntoView({ block: "nearest", behavior: "smooth" });
  }, 100);
  return ()=>clearTimeout(timer);
 },[selected, rows]);

 function render(parent:string,depth:number):React.ReactNode{return <>{rows[parent]?.children.map(prim=><div key={prim.path} data-prim-path={prim.path}><div className="scene-tree-row" style={{paddingLeft:depth*12}}><button aria-label={`${expanded.has(prim.path)?"Collapse":"Expand"} ${prim.name}`} disabled={!prim.hasChildren} onClick={()=>{const next=new Set(expanded);if(next.has(prim.path))next.delete(prim.path);else{next.add(prim.path);if(!rows[prim.path])void load(prim.path);}setExpanded(next);}}>{prim.hasChildren?(expanded.has(prim.path)?"▾":"▸"):"·"}</button><div draggable={true} role="button" tabIndex={0} title={prim.path} aria-pressed={selected===prim.path} className={"scene-prim-item "+(selected===prim.path?(prim.type==="Material"?"chosen-material":"chosen"):"")} onDragStart={e=>{e.dataTransfer.setData("application/orchflow-prim-path",prim.path);e.dataTransfer.setData("text/plain",prim.path);e.dataTransfer.effectAllowed="copy";}} onClick={()=>onSelect(prim.path)} onContextMenu={e=>{e.preventDefault();e.stopPropagation();setContextMenu({x:e.clientX,y:e.clientY,prim});}} onKeyDown={e=>{if(e.key==="Enter"||e.key===" ")onSelect(prim.path);}}><span>{prim.name}</span><small>{prim.type}{prim.instance?" · instance":prim.instanceProxy?" · proxy":""}</small></div></div>{expanded.has(prim.path)&&render(prim.path,depth+1)}</div>)}{busy.has(parent)&&<p className="hint">Loading...</p>}{rows[parent]?.nextOffset!=null&&<button onClick={()=>load(parent,rows[parent].nextOffset!)}>Load more...</button>}</>;}
 return <aside className={"scene-overview "+(collapsed?"collapsed":"")} aria-label="USD scene overview"><button onClick={()=>setCollapsed(v=>!v)}>{collapsed?"Scene ▸":"▾ Scene overview"}</button>{!collapsed&&<><p className="hint">Current preview stage. Select an object to highlight it, or a material to highlight the meshes using it.</p><button onClick={()=>{onSelect("");setRevision(v=>v+1);}}>Refresh hierarchy</button><button disabled={!selected} onClick={()=>onSelect("")}>Clear selection</button>{selected&&<p className="scene-selection" title={selected}>{selected}</p>}{error&&<p role="alert">{error}</p>}{!path?<p className="hint">Choose a node output to inspect.</p>:render("/",0)}<details className="node-options inspector-materials"><summary>Material diagnostics</summary><MaterialInspector path={path} graph={graph} onStatus={onStatus}/></details></>}{contextMenu&&<div className="scene-context-menu" style={{position:"fixed",left:contextMenu.x,top:contextMenu.y,zIndex:50}} onContextMenu={e=>e.preventDefault()} onPointerDown={e=>e.stopPropagation()}><div className="scene-context-header">{contextMenu.prim.path}</div><button type="button" onClick={()=>{const p=contextMenu.prim.path;setContextMenu(null);onSelect(p);}}>Select</button><button type="button" disabled={!onAddToMaterialX} title={onAddToMaterialX?`Add ${contextMenu.prim.path} to selected MaterialX row`:"Open the MaterialX editor to add to the selected row"} onClick={()=>{const p=contextMenu.prim.path;setContextMenu(null);onAddToMaterialX?.(p);}}>{contextMenu.prim.type==="Material"?"Add material to MaterialX row":"Add prim to MaterialX row"}</button><button type="button" disabled={!selected} onClick={()=>{setContextMenu(null);onSelect("");}}>Clear selection</button>{hiddenPrims?.includes(contextMenu.prim.path)?<button type="button" disabled={!onShowPrim} onClick={()=>{const p=contextMenu.prim.path;setContextMenu(null);onShowPrim?.(p);}}>Show prim</button>:<button type="button" disabled={!onHidePrim} onClick={()=>{const p=contextMenu.prim.path;setContextMenu(null);onHidePrim?.(p);}}>Hide prim</button>}<button type="button" disabled={!onIsolatePrim} onClick={()=>{const p=contextMenu.prim.path;setContextMenu(null);onIsolatePrim?.(p);}}>{isolatedPrim===contextMenu.prim.path?"Exit isolate":"Isolate"}</button><button type="button" disabled={!onShowAll||(!isolatedPrim&&!(hiddenPrims?.length))} onClick={()=>{setContextMenu(null);onShowAll?.();}}>Show all</button><button type="button" onClick={()=>{const p=contextMenu.prim.path;setContextMenu(null);void navigator.clipboard?.writeText(p);onStatus(`Copied ${p}`);}}>Copy path</button></div>}</aside>;
}
