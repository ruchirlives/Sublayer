import {InspectorHint} from "./InspectorHint";
import {OperationProgress} from "./OperationProgress";
import { useEffect, useState } from "react";
import { invoke, isTauri } from "@tauri-apps/api/core";
import {defaultEditScope} from "./profilePolicy";
import { NodeEditScope, useApplicationCatalog, findProfile } from "./applicationProfiles";
import { PublicationPanel } from "./PublicationPanel";
import { CoordinatePanel } from "./CoordinatePanel";
import type { Data, PublicationResult, NativeCreationResult, LiveAdjustmentResult, ManualBaselineResult, HandoffResult, StoredProject } from "./fileRegistry";

export function NativeProjectPanel({ project, nodeId, data, onChange, onHandoff, onBaseline, onNativeCreated, onPublished, onLive, onStatus, onConfigure, projectName }: {
  project: StoredProject;
  projectName:string;
  nodeId: string;
  data: Data;
  onChange: (patch: Partial<Data>) => void;
  onHandoff: (result:HandoffResult)=>void;
  onBaseline:(result:ManualBaselineResult)=>void;
  onPublished:(edited:string,result:PublicationResult)=>void;
  onLive:(edited:string,result:LiveAdjustmentResult)=>void;
  onNativeCreated: (result:NativeCreationResult)=>StoredProject;
  onStatus: (message: string) => void;
  onConfigure:()=>void;
}) {
  const {catalog}=useApplicationCatalog();
  const profile=findProfile(catalog,data);
  const inheritedExecutable=profile?.executable || "";
  const effectiveExecutable=data.appExecutable || inheritedExecutable;
  const [busy, setBusy] = useState(false);
  const [creationMessage,setCreationMessage]=useState("");
  const [availability, setAvailability] = useState<Record<string, string>>({});
  const [revision, setRevision] = useState(0);
  const commonReasons=[
    ...(!isTauri()?["Use the desktop app."]:[]),
    ...(busy?["Wait for the current operation to finish."]:[]),
    ...(data.app!=="Blender"?[data.app?`Native creation/opening is not implemented for ${data.app}; its profile is currently a template.`:"Choose the Blender application profile."]:[]),
    ...(!effectiveExecutable?["Set the executable in Applications."]:[]),
  ];
  const createReasons=[...commonReasons,
    ...(data.app==="Blender" && profile?.adapterVersion!==2?["In Applications, set this Blender profile's adapter version to 2 and Save. Existing profiles are not upgraded automatically."]:[]),
    ...(!data.handoff?["Create a handoff first."]:[]),
    ...(data.nativeProject?["A native project is already linked."]:[]),
  ];
  const openReasons=[...commonReasons,...(!data.nativeProject?["Create or link a Blender project first."]:[])];

  useEffect(() => {
    setAvailability({});
    if (!isTauri()) return;
    let active = true;
    const timer = setTimeout(() => {
      void invoke<{id: string; status: string}[]>("dependency_statuses", {
        dependencies: [
          {id: "native", kind: "nativeProject", path: data.nativeProject || ""},
          {id: "exe", kind: "executable", path: effectiveExecutable},
        ],
      }).then(rows => {if (active) setAvailability(Object.fromEntries(rows.map(r => [r.id,r.status])));})
        .catch(error => {if (active) onStatus(String(error));});
    }, 250);
    return () => {active = false; clearTimeout(timer);};
  }, [data.nativeProject, effectiveExecutable, revision, onStatus]);

  async function run(task: () => Promise<void>) {
    setBusy(true);
    try {await task();} catch (error) {onStatus(String(error));} finally {setBusy(false);}
  }
  async function browse(field: "nativeProject") {
    const { open } = await import("@tauri-apps/plugin-dialog");
    const path = await open({multiple:false,
      title: "Choose an existing native project",
      filters: data.app === "Blender" ? [{name:"Blender project",extensions:["blend"]}] : undefined,
    });
    if (!path) return;
    const existingId = await invoke<string | null>("find_dependency", {path, dependencies: project.dependencies});
    const existing = project.dependencies.find(d => d.id === existingId);
    onChange({[field]: existing?.path || path});
    setRevision(r => r+1);
    onStatus(existing ? "Warning: file already registered. Reused its existing link for this node." : "Linked existing file. No import or publication was performed.");
  }
  return <div className="native-project-panel compact-native">
    <section className="app-profile">
      <div className="app-section-heading"><h3>Application</h3><button className="app-text-action" onClick={onConfigure}>Settings</button></div>
      <label><span className="field-label">Profile<InspectorHint text="Choose the application profile. Executable paths and shared capabilities are managed in Applications."/></span><select value={data.applicationProfileId || ""} onChange={e=>{const chosen=catalog.applications.find(p=>p.id===e.target.value);onChange({app:chosen?.app || "",applicationProfileId:chosen?.id || null,editScope:defaultEditScope(chosen)});}}>
        <option value="">Choose application</option>{catalog.applications.map(p=><option key={p.id} value={p.id}>{p.app} · {p.applicationVersion}</option>)}
        {data.applicationProfileId&&!profile&&<option value={data.applicationProfileId} disabled>Profile unavailable</option>}
      </select></label>
      {!effectiveExecutable&&data.app==="Blender"&&<p className="app-notice">Set the executable in Applications.</p>}
      {!!data.appExecutable&&<p className="hint">Using a saved node executable override.</p>}
    </section>
    <section className="coordinate-panel app-workflow-step">
      <div className="app-section-heading"><h3><span className="app-step-number">01</span> Handoff<InspectorHint text="Create one binary USD handoff, with the selected objects, materials and coordinate conversion. Textures remain linked. Recreate it explicitly after upstream changes; this does not create a native project."/></h3><span className={"app-state "+(data.handoff?"ready":"")}>{data.handoff?"Prepared":"Not prepared"}</span></div>
      {data.handoff&&<div className="app-file" title={project.dependencies.find(d=>d.id===data.handoff?.dependencyId)?.path}><span className="app-file-kind">USD input</span><strong>{project.dependencies.find(d=>d.id===data.handoff?.dependencyId)?.path.split(/[\\/]/).pop()}</strong></div>}
      <CoordinatePanel compact projectName={projectName} project={project} nodeId={nodeId} data={data} onChange={onChange} onHandoff={onHandoff} onStatus={onStatus}/>
      <CoordinatePanel projectName={projectName} project={project} nodeId={nodeId} data={data} onChange={onChange} onHandoff={onHandoff} onStatus={onStatus}/>
    </section>
    <section className="coordinate-panel app-workflow-step">
      {busy&&<OperationProgress label={data.nativeProject?"Opening or linking native project...":"Preparing native project..."}/>}
      <div className="app-section-heading"><h3><span className="app-step-number">02</span> Native project<InspectorHint text="Keep the editable application project here. Creating a Blender project is separate from handoff export. Existing projects reopen without importing upstream again. Other apps currently support manual project linking."/></h3><span className={"app-state "+(data.nativeProject?"ready":"")}>{data.nativeProject?"Linked":"Not linked"}</span></div>
    {data.nativeProject?<>
      <div className="app-file" title={data.nativeProject}><span className="app-file-kind">Editable source · {availability.native || "Checking..."}</span><strong>{data.nativeProject.split(/[\\/]/).pop()}</strong></div>
    <button className="primary" disabled={!!openReasons.length} title={openReasons.join(" ") || "Reopen the linked Blender project"}
      onClick={() => run(async () => {
        const pid = await invoke<number>("open_native_project", {project, nodeId});
        onStatus(`Launch requested for the existing native project (process ${pid}). Native save and USD publication remain separate actions.`);
      })}>Open in {data.app || "App"}</button>
      {!!openReasons.length&&<p className="app-notice">{openReasons[0]}</p>}
      <button className="app-secondary" disabled={busy || !isTauri()} onClick={()=>run(()=>browse("nativeProject"))}>Relink project…</button>
    </>:<>
      {data.app==="Blender"?<>
    <button className="primary" disabled={!!createReasons.length} title={createReasons.join(" ") || "Create a new .blend from the recorded handoff; baseline is exported manually"} onClick={()=>run(async()=>{
      setCreationMessage("Choose the Blender project destination. Replacement requires confirmation.");
      try {
      const {save,confirm}=await import("@tauri-apps/plugin-dialog");
      const path=await save({title:"Create new Blender project from handoff",defaultPath:"project.blend",filters:[{name:"Blender project",extensions:["blend"]}]});
      if(!path){setCreationMessage("Cancelled. No Blender project or baseline was created.");return;}
      const overwrite=await confirm("Create this Blender project, replacing the selected .blend if it exists? This discards their current edits at these paths. Backup files will be retained. Close the target project in Blender first.",{title:"Create or replace Blender project",kind:"warning"});
      if(!overwrite){setCreationMessage("Cancelled. Existing files unchanged.");return;}
      setCreationMessage("Importing handoff and saving Blender project. This may take several minutes for large scenes...");
      onStatus("Importing the recorded handoff in Blender. This may take several minutes for large scenes…");
      const created=await invoke<NativeCreationResult>("create_blender_project",{project,nodeId,path,overwrite});
      const next=onNativeCreated(created);
      setCreationMessage("Created native Blender project. Export an unedited baseline manually before making edits.");
      setRevision(r=>r+1);
      onStatus("Created and verified the native Blender project. Opening it now…");
      try {
        await invoke("open_native_project",{project:next,nodeId});
        onStatus("Created Blender project and requested opening. Save OrchFlow to retain its native link and import snapshot. Published USD is unchanged.");
      }catch(error){onStatus(`Created and linked ${created.path}, but opening failed: ${error}. Use Open in App to retry.`);}
      }catch(error){setCreationMessage(String(error));throw error;}
    })}>{busy ? "Working…" : "Create Blender project…"}</button>
      {creationMessage&&<p className="hint" role="status">{creationMessage}</p>}
      {!!createReasons.length&&<p className="hint">{createReasons[0]}</p>}

      </>:<p className="hint">Link a project created in {data.app || "your application"}.</p>}
      <button disabled={busy || !isTauri()} onClick={()=>run(()=>browse("nativeProject"))}>Link project…</button>
    </>}
    </section>
    {<PublicationPanel onLive={onLive} onBaseline={onBaseline} project={project} nodeId={nodeId} onPublished={onPublished} onChange={onChange} onStatus={onStatus}/>}
    <NodeEditScope data={data} onChange={onChange} onConfigure={onConfigure}/>
    <details className="node-options app-advanced"><summary>Advanced</summary>
      {!!data.path&&<p className="hint">This node already has a direct USD link: {data.path}<button onClick={()=>onChange({path:""})}>Remove direct link and use connected inputs</button></p>}
      <label><span className="field-label">Project path<InspectorHint text="Editable application project path. Managed when creating or linking a project."/></span><input disabled={busy} value={data.nativeProject || ""} onChange={e=>onChange({nativeProject:e.target.value})}/></label>
      {!!data.appExecutable&&<p className="hint">This node has a previously saved executable override. <button onClick={()=>onChange({appExecutable:""})}>Use global application default</button></p>}
      <button disabled={busy || !isTauri()} onClick={()=>setRevision(r=>r+1)}>Check linked files</button>
      {data.nativeImport&&<p className="hint">Prepared with Blender {String(data.nativeImport.report.blenderVersion)}. Original conversion and baseline are retained.</p>}
    </details>
  </div>;
}
