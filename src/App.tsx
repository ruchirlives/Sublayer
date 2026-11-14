import {InspectorHint} from "./InspectorHint";
import {HdriInspector} from "./HdriInspector";
import {NumberInput} from "./NumberInput";
import {OperationProgress} from "./OperationProgress";
import {
  createContext,
  useContext,
  useEffect,
  useCallback,
  useMemo,
  useRef,
  useState,
} from "react";
import {ProjectHistory,historyShortcut} from "./projectHistory";
import {SceneOverview} from "./SceneOverview";
import { DependencyTable } from "./DependencyTable";
import {
  recordHandoff,
  recordNativeCreation,
  recordManualBaseline,
  recordPublication,
  recordLiveAdjustment,
  mergeUsdDiscovery,
  fileKey,
  graphPreview,
  identityTransform,
  type Transform,
  editNodeData,
  expandProject,
  storeProject,
  type Data,
  type StageNode,
  type Dependency,
  type ProceduralGraph,
  type StoredProject,
  type ViewportSettings,
} from "./fileRegistry";
import { insertToolbarNode, toolbarOperations, type DropTarget } from "./graphInsertion";
import { previewTarget } from "./previewTarget";
import { NativeViewport } from "./NativeViewport";
import { ApplicationCatalogProvider, ApplicationSettings } from "./applicationProfiles";
import { NativeProjectPanel } from "./NativeProjectPanel";
import {MaterialXPanel} from "./MaterialXPanel";
import { MaterialBindingInspector } from "./MaterialBindingInspector";
import { LightInspector } from "./LightInspector";
import { HairInspector } from "./HairInspector";
import { BlenderInspector } from "./BlenderInspector";
import { invoke, isTauri } from "@tauri-apps/api/core";
import {
  ReactFlow,
  Background,
  Controls,
  Handle,
  Position,
  addEdge,
  applyNodeChanges,
  applyEdgeChanges,
  useNodesState,
  useEdgesState,
  type NodeProps,
  type Edge,
  type ReactFlowInstance,
} from "@xyflow/react";
import "@xyflow/react/dist/style.css";
import "./App.css";
const seed: StageNode[] = [
  {
    id: "source",
    type: "stage",
    position: { x: 80, y: 140 },
    data: {
      label: "Source stage",
      path: "",
      operation: "source",
      app: "",
      axis: "preserve",
      scale: 1,
      nativeProject: "",
      publishedPath: "",
    },
  },
];
function CameraNumberInput({
  value,
  step,
  min,
  max,
  onCommit,
  style,
}: {
  value: number;
  step: number;
  min?: number;
  max?: number;
  onCommit: (value: number) => void;
  style: React.CSSProperties;
}) {
  const [draft, setDraft] = useState<string | null>(null);
  const commit = (raw: string) => {
    const parsed = Number(raw);
    if (raw.trim() !== "" && Number.isFinite(parsed)) {
      let next = parsed;
      if (min !== undefined) next = Math.max(min, next);
      if (max !== undefined) next = Math.min(max, next);
      onCommit(next);
    }
    setDraft(null);
  };
  return (
    <input
      type="number"
      step={step}
      value={draft !== null ? draft : Number.isFinite(value) ? String(value) : "0"}
      onFocus={() => setDraft(Number.isFinite(value) ? String(value) : "0")}
      onChange={(e) => {
        const raw = e.target.value;
        setDraft(raw);
        const parsed = Number(raw);
        if (
          raw.trim() !== "" &&
          Number.isFinite(parsed) &&
          (min === undefined || parsed >= min) &&
          (max === undefined || parsed <= max)
        ) {
          onCommit(parsed);
        }
      }}
      onBlur={(e) => commit(e.target.value)}
      onKeyDown={(e) => {
        if (e.key === "Enter") {
          commit(e.currentTarget.value);
          e.currentTarget.blur();
        } else if (e.key === "Escape") {
          setDraft(null);
          e.currentTarget.blur();
        }
      }}
      style={style}
    />
  );
}

const PinContext = createContext<{
  pinnedId: string | null;
  toggle: (id: string) => void;
}>({ pinnedId: null, toggle: () => {} });
function Card({ id, data, selected }: NodeProps<StageNode>) {
  const pin = useContext(PinContext);
  return (
    <div data-node-type={data.operation} className={"card " + (selected ? "selected" : "")}>
      <Handle type="target" position={Position.Left} />
      <button
        className="pin-toggle nodrag nopan"
        aria-label={`${pin.pinnedId === id ? "Unpin" : "Pin"} ${data.label} output`}
        aria-pressed={pin.pinnedId === id}
        title="Pin background preview to this node"
        onClick={(e) => {
          e.stopPropagation();
          pin.toggle(id);
        }}
      >
        {pin.pinnedId === id ? "●" : "◇"}
      </button>
      <small>USD / {data.operation.toUpperCase()}</small>
      <strong>{data.label}</strong>
      <p>
        {["delete", "transform", "output", "assetOverride", "materialx", "material_bind", "light", "hdri", "hair"].includes(data.operation)
          ? "Combined USD inputs"
          : data.operation === "blender"
            ? (data.nativeProject?.split(/[\\/]/).pop() || "No .blend linked")
            : data.operation === "application"
            ? data.nativeProject.split(/[\\/]/).pop() || "No native project linked"
            : data.path.split(/[\\/]/).pop() || "No stage assigned"}
      </p>
      <footer>
        {data.operation === "blender"
          ? (data.nativeProject
              ? (() => {
                  const selSet = new Set(data.blenderSelections || []);
                  const objs = data.blenderObjects || [];
                  const cols = new Set<string>();
                  for (const o of objs) {
                    if (selSet.has(o.name)) {
                      for (const c of o.collections && o.collections.length > 0 ? o.collections : ["Scene Collection"]) {
                        cols.add(c);
                      }
                    }
                  }
                  return objs.length > 0
                    ? `${cols.size} collection(s) · ${selSet.size} obj`
                    : `${selSet.size} objects · in memory`;
                })()
              : "No .blend linked")
          : data.operation === "application"
          ? data.liveAdjustment ? "Live output · reviewed edits" : "No saved live adjustment"
          : data.operation === "transform"
          ? `XYZ scale ${(data.transform || identityTransform).scale.join(", ")}`
          : data.operation === "output"
            ? "In-memory USD output"
            : `${data.axis} up · scale ${data.scale}`}
      </footer>
      <Handle type="source" position={Position.Right} />
    </div>
  );
}
const nodeTypes = { stage: Card };
function Workspace() {
  const [settingsTarget,setSettingsTarget]=useState({id:"",visit:0});
  const [activeTab,setActiveTab]=useState<"canvas"|"applications">("canvas");
  const [nodes, setNodes] = useNodesState<StageNode>(seed);
  const [edges, setEdges] = useEdgesState<Edge>([]);
  const [selected, select] = useState("source"),
    [status, setStatus] = useState("Ready"),
    [runtime, setRuntime] = useState("Checking OpenUSD…"),
    [busy, setBusy] = useState(false),
    [inspection, setInspection] = useState<unknown>(null);
  const [sceneMode, setSceneMode] = useState(false);
  const [graphModeRequest,setGraphModeRequest]=useState(0);
  const [pinnedNodeId, setPinnedNodeId] = useState<string | null>(null);
  const [viewportPrefs, setViewportPrefs] = useState<Omit<ViewportSettings, "pinnedNodeId">>({});
  const [proceduralGraph, setProceduralGraph] = useState<ProceduralGraph | null>(null);
  const [backendOutdated, setBackendOutdated] = useState(false);
  const reportStatus = useCallback((message: string) => {
    if (/command .*not found/i.test(message)) {
      setBackendOutdated(true);
      setStatus(
        "Warning: the running Rust backend is outdated. Copy project JSON if needed, then close the old window and relaunch the updated app.",
      );
    } else setStatus(message);
  }, []);
  const [operationProgress,setOperationProgress]=useState("");
  const [projectName,setProjectName]=useState("Untitled");
  const projectPathRef = useRef<string | null>(null);
  const [dependencies, setDependencies] = useState<Dependency[]>([]);
  const [materialXEditorId,setMaterialXEditorId]=useState<string|null>(null);
  const [overrideNodeId, setOverrideNodeId] = useState<string | null>(null);
  const [inspectorWidth, setInspectorWidth] = useState(310);
  const [resizingInspector, setResizingInspector] = useState(false);
  function startInspectorResize(e: React.MouseEvent) {
    e.preventDefault();
    const startX = e.clientX;
    const startWidth = inspectorWidth;
    setResizingInspector(true);
    document.body.style.cursor = "col-resize";
    function onMove(ev: MouseEvent) {
      const max = Math.max(320, Math.min(820, window.innerWidth * 0.65));
      setInspectorWidth(Math.max(240, Math.min(max, startWidth + (startX - ev.clientX))));
    }
    function onUp() {
      setResizingInspector(false);
      document.body.style.cursor = "";
      window.removeEventListener("mousemove", onMove);
      window.removeEventListener("mouseup", onUp);
    }
    window.addEventListener("mousemove", onMove);
    window.addEventListener("mouseup", onUp);
  }
  const projectModel = useMemo<StoredProject>(
    () => ({
      ...storeProject(nodes, edges, dependencies),
      ...(proceduralGraph ? { proceduralGraph } : {}),
      viewport: {
        pinnedNodeId: nodes.some((n) => n.id === pinnedNodeId)
          ? pinnedNodeId
          : null,
        ...viewportPrefs,
      },
    }),
    [nodes, edges, dependencies, proceduralGraph, pinnedNodeId, viewportPrefs],
  );
  const projectRef = useRef(projectModel);
  projectRef.current = projectModel;
  const scanPromise = useRef<Promise<void> | null>(null);
  const projectSession=useRef(0);
  const currentProjectSession=projectSession.current;
  const [clearScenePrompt,setClearScenePrompt]=useState(false);
  const clearSceneDialog=useRef<HTMLDialogElement>(null);
  useEffect(()=>{
    if(clearScenePrompt)clearSceneDialog.current?.showModal();
    else clearSceneDialog.current?.close();
  },[clearScenePrompt]);
  const history=useRef(new ProjectHistory());
  const [,historyChanged]=useState(0);
  function applyProject(project: StoredProject,label="Edit project",group:string|null=null,record=true) {
    if(currentProjectSession!==projectSession.current)return;
    const nextViewport: ViewportSettings = {
      ...(project.viewport ?? { pinnedNodeId: null }),
      pinnedNodeId: project.nodes.some((n) => n.id === project.viewport?.pinnedNodeId)
        ? project.viewport!.pinnedNodeId
        : null,
    };
    project = { ...project, viewport: nextViewport };
    if(record && history.current.record(projectRef.current,project,label,group))historyChanged(v=>v+1);
    projectRef.current = project;
    setPinnedNodeId(nextViewport.pinnedNodeId || null);
    setViewportPrefs({
      ...(nextViewport.renderer !== undefined ? { renderer: nextViewport.renderer } : {}),
      ...(nextViewport.ovrtxMode !== undefined ? { ovrtxMode: nextViewport.ovrtxMode } : {}),
      ...(nextViewport.upAxis !== undefined ? { upAxis: nextViewport.upAxis } : {}),
      ...(nextViewport.materials !== undefined ? { materials: nextViewport.materials } : {}),
      ...(nextViewport.camera !== undefined ? { camera: nextViewport.camera } : {}),
      ...(nextViewport.activeCameraViewId !== undefined ? { activeCameraViewId: nextViewport.activeCameraViewId } : {}),
    });
    const expanded = expandProject(project);
    setNodes(expanded.nodes);
    setEdges(expanded.edges);
    setDependencies(project.dependencies);
    setProceduralGraph(project.proceduralGraph ?? null);
    setInspection(null);
  }
  function clearScene() {
    applyProject({...storeProject([],[],[]),viewport:{pinnedNodeId:null}},"New project",null,false);
    projectSession.current++;
    history.current.clear();historyChanged(v=>v+1);
    select("");setProjectName("Untitled");projectPathRef.current = null;
    setMaterialXEditorId(null);setOverrideNodeId(null);
    setApplicationPreviews({});setPrimSelection({key:"",path:""});
    setDropTarget(null);setSceneMode(false);setGraphModeRequest(v=>v+1);
    setActiveTab("canvas");setOperationProgress("");
    void flowInstance.current?.setViewport({x:0,y:0,zoom:1});
    setClearScenePrompt(false);
    setStatus("Scene cleared. Add a Source node to start a new project.");
  }
  function travel(direction:"undo"|"redo") {
    const entry=history.current[direction](projectRef.current);
    if(!entry)return;
    applyProject(entry.project,entry.label,null,false);historyChanged(v=>v+1);
    if(!entry.project.nodes.some(n=>n.id===selected))select(entry.project.nodes[0]?.id || "");
    setOverrideNodeId(null);setStatus(`${direction==="undo"?"Undid":"Redid"}: ${entry.label}. External files were not changed.`);
  }
  useEffect(()=>{
    const key=(event:KeyboardEvent)=>{const editing=event.composedPath().some(target=>target instanceof HTMLElement && (target.matches("input,textarea,select") || target.isContentEditable));const direction=historyShortcut(event,editing);if(direction && !busy && activeTab==="canvas"){event.preventDefault();travel(direction);}};
    const blur=()=>history.current.endGroup();
    window.addEventListener("keydown",key);window.addEventListener("focusout",blur);
    return ()=>{window.removeEventListener("keydown",key);window.removeEventListener("focusout",blur);};
  });
  function pin(id:string|null){applyProject({...projectRef.current,viewport:{...projectRef.current.viewport,pinnedNodeId:id}},"Pin preview");}
  const node = nodes.find((n) => n.id === selected);
  const pinnedNode = nodes.find((n) => n.id === pinnedNodeId);
  const previewNode = pinnedNode || node;
  const previewId = previewNode?.id || "";
  const [applicationPreviews, setApplicationPreviews] = useState<Record<string,"handoff"|"published"|"input">>({});
  const previewMode = applicationPreviews[previewId] || (previewNode?.data.liveAdjustment ? "published" : "handoff");
  const activePreviewMode = pinnedNode && previewMode === "input" ? "handoff" : previewMode;
  const target = previewTarget(projectModel,previewId,activePreviewMode);
  const comparisonHandoff=previewNode?.data.operation==="application" ? previewNode.data.handoff : null;
  const comparisonPath=projectModel.dependencies.find(d=>d.id===comparisonHandoff?.dependencyId)?.path;
  const comparison=comparisonHandoff&&comparisonPath ? JSON.stringify({key:previewId,path:comparisonPath,inverse:comparisonHandoff.conversion.inverse,handoff:activePreviewMode==="handoff"}) : undefined;
  const sceneKey=JSON.stringify([target.path,target.graph]);
  const [primSelection,setPrimSelection]=useState({key:"",path:""});
  const selectedPrim=primSelection.key===sceneKey?primSelection.path:"";
  const flowInstance=useRef<ReactFlowInstance<StageNode,Edge>|null>(null);
  const [dropTarget,setDropTarget]=useState<DropTarget>(null);
  function targetAt(element:EventTarget|null):DropTarget {
    if(!(element instanceof Element))return null;
    const node=element.closest(".react-flow__node");
    if(node)return {kind:"node",id:node.getAttribute("data-id")!};
    const edge=element.closest(".react-flow__edge");
    return edge?{kind:"edge",id:edge.getAttribute("data-id")!}:null;
  }
  function dropNode(event:React.DragEvent) {
    const operation=event.dataTransfer.getData("application/orchflow-node");
    if(!toolbarOperations.includes(operation))return;
    event.preventDefault();setDropTarget(null);
    const position=flowInstance.current?.screenToFlowPosition({x:event.clientX,y:event.clientY});
    if(!position)return;
    const id=crypto.randomUUID();
    const label=operation === "materialx"?"MaterialX":operation === "material_bind"?"Material Bind":operation === "light"?"Light":operation === "hdri"?"HDRI":operation === "hair"?"Hair":operation === "camera"?"Camera":operation === "blender"?"Blender":operation === "assetOverride"?"Asset Override":operation === "source"?"Source stage":operation[0].toUpperCase()+operation.slice(1);
    const defaultCamera = { yaw: 35, pitch: 20, zoom: 1, panX: 0, panY: 0, fov: 45, dofEnabled: false, focusDistance: 5, fStop: 5.6 };
    const initialCamera = { ...defaultCamera, ...(projectRef.current.viewport?.camera ?? {}) };
    const added=storeProject([{...seed[0],id,position,data:{...seed[0].data,label,operation,transform:structuredClone(identityTransform),...(operation === "camera" ? { cameraViews: [{ id: crypto.randomUUID(), name: "Camera 1", camera: initialCamera }] } : {})}}],[],[]).nodes[0];
    try {
      const target=targetAt(event.target);
      applyProject(insertToolbarNode(projectRef.current,added,target),"Insert node");select(id);
      setStatus(target?.kind === "edge"?"Inserted node into edge and shifted downstream nodes.":target?"Connected node and made space for the new branch.":"Added node at drop location.");
    }catch(error){reportStatus(String(error));}
  }
  function addNode(operation: string) {
    const id = crypto.randomUUID();
    const ns=nodes;
    const added:StageNode[] = [
      ...ns,
      {
        ...seed[0],
        id,
        position: { x: 160 + ns.length * 80, y: 180 + ns.length * 60 },
        data: {
          ...seed[0].data,
          label:
            operation === "materialx" ? "MaterialX" : operation === "material_bind" ? "Material Bind" : operation === "light" ? "Light" : operation === "hdri" ? "HDRI" : operation === "hair" ? "Hair" : operation === "camera" ? "Camera" : operation === "blender" ? "Blender" : operation === "source"
              ? "Source stage"
              : operation === "transform"
                ? "Transform"
                : operation === "assetOverride"
                  ? "Asset Override"
                  : operation === "output"
                  ? "Output"
                  : "Application",
          operation,
          transform: structuredClone(identityTransform),
          ...(operation === "camera"
            ? {
                cameraViews: [
                  {
                    id: crypto.randomUUID(),
                    name: "Camera 1",
                    camera: {
                      yaw: 35,
                      pitch: 20,
                      zoom: 1,
                      panX: 0,
                      panY: 0,
                      fov: 45,
                      dofEnabled: false,
                      focusDistance: 5,
                      fStop: 5.6,
                      ...(projectRef.current.viewport?.camera ?? {}),
                    },
                  },
                ],
              }
            : {}),
        },
      },
    ];
    applyProject({...projectRef.current,...storeProject(added,edges,dependencies)},"Add node");
    select(id);
  }
  function transformValue(
    field: keyof Transform,
    index: number,
    value: number,
  ) {
    if (!node || !Number.isFinite(value)) return;
    const transform = structuredClone(node.data.transform || identityTransform);
    transform[field][index] = value;
    update({ transform });
  }

  function update(patch: Partial<Data>) {
    applyProject(editNodeData(projectRef.current, selected, patch),"Edit node",`field:${selected}:${Object.keys(patch).join()}`);
  }
  async function run(task: () => Promise<void>) {
    setBusy(true);
    try {
      await task();
    } catch (e) {
      reportStatus(String(e));
    } finally {
      setOperationProgress("");
      setBusy(false);
    }
  }
  function rescanUsdDependencies(): Promise<void> {
    if (scanPromise.current) return scanPromise.current;
    const task = (async () => {
      const initial = projectRef.current;
      const rootIds = new Set(
        initial.nodes
          .flatMap((n) => [
            n.data.inputDependencyId,
            n.data.publishedDependencyId,
          ])
          .filter((id): id is string => !!id),
      );
      for (const dependency of initial.dependencies) {
        if (
          !dependency.usdReferences?.length &&
          (dependency.kind === "usd" ||
            /\.(usd|usda|usdc|usdz)$/i.test(dependency.path))
        )
          rootIds.add(dependency.id);
      }
      let scanned = 0;
      const failures: string[] = [];
      for (const sourceId of rootIds) {
        const source = initial.dependencies.find((d) => d.id === sourceId);
        if (!source) continue;
        try {
          const result = await invoke<{
            files: { path: string; kind: "usd" | "asset" }[];
          }>("usd_request", {
            request: { operation: "dependencies", path: source.path },
          });
          const current = projectRef.current;
          if (
            current.dependencies.some(
              (d) =>
                d.id === sourceId && fileKey(d.path) === fileKey(source.path),
            )
          ) {
            applyProject(mergeUsdDiscovery(current, sourceId, result.files),"Discover dependencies",null,false);
            scanned++;
          }
        } catch (error) {
          failures.push(`${source.path}: ${error}`);
        }
      }
      reportStatus(
        failures.length
          ? `Warning: scanned ${scanned} USD source(s); ${failures.length} could not be scanned. ${failures.join("; ")}`
          : `Discovered dependencies from ${scanned} USD source(s), including missing references. No files were modified.`,
      );
    })();
    scanPromise.current = task;
    void task.finally(() => {
      if (scanPromise.current === task) scanPromise.current = null;
    });
    return task;
  }
  async function browse() {
    const { open } = await import("@tauri-apps/plugin-dialog");
    const path = await open({
      multiple: false,
      filters: [
        { name: "USD stage", extensions: ["usd", "usda", "usdc", "usdz"] },
      ],
    });
    if (path) {
      const id = await invoke<string | null>("find_dependency", {
        path,
        dependencies: projectModel.dependencies,
      });
      const existing = projectModel.dependencies.find((d) => d.id === id);
      update({ path: existing?.path || path });
      await rescanUsdDependencies();
      setStatus(
        existing
          ? `Warning: already registered as ${existing.id}. Reused the existing dependency; this node now uses that original file link.`
          : "Linked the original USD file.",
      );
    }
  }
  async function openProjectAtPath(path: string) {
    setOperationProgress("Loading project...");
    const p = await invoke<StoredProject>("load_project", { path });
    history.current.clear();historyChanged(v=>v+1);setMaterialXEditorId(null);
    applyProject(p,"Open project",null,false);
    select(p.nodes[0]?.id || "");
    setInspection(null);
    setOperationProgress("Discovering USD dependencies...");
    await rescanUsdDependencies();
    projectPathRef.current = path;
    setProjectName(path.split(/[\\/]/).pop()?.replace(/\.(orchflow|json)$/i,"") || "Untitled");
    setStatus(`Opened ${path}`);
  }
  const bridgeStateRef = useRef({
    selected,
    activeTab,
    status,
    projectName,
    openProjectAtPath,
    applyProject,
    pin,
    select,
    setActiveTab,
  });
  bridgeStateRef.current = {
    selected,
    activeTab,
    status,
    projectName,
    openProjectAtPath,
    applyProject,
    pin,
    select,
    setActiveTab,
  };
  useEffect(() => {
    if (!isTauri()) return;
    let disposed = false;
    let unlisten: (() => void) | undefined;
    void import("@tauri-apps/api/event").then(async ({ listen }) => {
      const stop = await listen<{
        requestId: string;
        action: string;
        args?: Record<string, unknown>;
      }>("orchflow://mcp-command", async ({ payload }) => {
        const { requestId, action, args = {} } = payload;
        const reply = async (ok: boolean, result?: unknown, error?: string) => {
          try {
            await invoke("mcp_bridge_reply", {
              requestId,
              ok,
              result: ok ? (result ?? null) : null,
              error: ok ? null : (error ?? "Unknown error"),
            });
          } catch {}
        };
        try {
          const ctx = bridgeStateRef.current;
          const current = projectRef.current;
          if (action === "get_state") {
            await reply(true, {
              projectName: ctx.projectName,
              projectPath: projectPathRef.current,
              activeTab: ctx.activeTab,
              selectedNodeId: ctx.selected,
              pinnedNodeId: current.viewport?.pinnedNodeId ?? null,
              viewport: current.viewport ?? null,
              status: ctx.status,
              error: (ctx as Record<string, unknown>).error ?? null,
              viewportError: (ctx as Record<string, unknown>).viewportError ?? null,
              nodes: current.nodes.map((n) => ({
                id: n.id,
                operation: n.data.operation,
                label: n.data.label,
              })),
              edges: current.edges,
            });
          } else if (action === "close") {
            await reply(true, { closing: true });
            window.close();
          } else if (action === "open_project") {
            const path = String(args.path ?? "");
            if (!path) throw new Error("path is required");
            await ctx.openProjectAtPath(path);
            await reply(true, { opened: path, nodes: projectRef.current.nodes.length });
          } else if (action === "select_node") {
            const nodeId = String(args.nodeId ?? "");
            if (!current.nodes.some((n) => n.id === nodeId)) {
              throw new Error(`Node not found: ${nodeId}`);
            }
            ctx.select(nodeId);
            await reply(true, { selectedNodeId: nodeId });
          } else if (action === "pin_node") {
            const nodeId = args.nodeId == null ? null : String(args.nodeId);
            if (nodeId !== null && !current.nodes.some((n) => n.id === nodeId)) {
              throw new Error(`Node not found: ${nodeId}`);
            }
            ctx.pin(nodeId);
            await reply(true, { pinnedNodeId: nodeId });
          } else if (action === "set_viewport") {
            const nextViewport: ViewportSettings = {
              ...(current.viewport ?? { pinnedNodeId: null }),
            };
            if (typeof args.renderer === "string") nextViewport.renderer = args.renderer as ViewportSettings["renderer"];
            if (typeof args.ovrtxMode === "string") nextViewport.ovrtxMode = args.ovrtxMode as ViewportSettings["ovrtxMode"];
            if (typeof args.upAxis === "string") nextViewport.upAxis = args.upAxis as ViewportSettings["upAxis"];
            if (typeof args.materials === "string") nextViewport.materials = args.materials as ViewportSettings["materials"];
            ctx.applyProject({ ...current, viewport: nextViewport }, "MCP viewport update");
            if (args.activeTab === "canvas" || args.activeTab === "applications") {
              ctx.setActiveTab(args.activeTab);
            }
            await reply(true, { viewport: nextViewport });
          } else if (action === "apply_project") {
            const nextProject = args.project as StoredProject | undefined;
            if (!nextProject || !Array.isArray(nextProject.nodes)) {
              throw new Error("Invalid project payload");
            }
            const label = typeof args.label === "string" ? args.label : "MCP apply project";
            ctx.applyProject(nextProject, label, null, true);
            const selectId = typeof args.selectNodeId === "string" ? args.selectNodeId : "";
            if (selectId && nextProject.nodes.some((n) => n.id === selectId)) {
              ctx.select(selectId);
            } else if (!nextProject.nodes.some((n) => n.id === ctx.selected)) {
              ctx.select(nextProject.nodes[0]?.id || "");
            }
            await reply(true, { applied: true, nodes: nextProject.nodes.length });
          } else {
            await reply(false, undefined, `Unknown desktop action: ${action}`);
          }
        } catch (err) {
          await reply(false, undefined, err instanceof Error ? err.message : String(err));
        }
      });
      if (disposed) stop();
      else unlisten = stop;
    });
    return () => {
      disposed = true;
      unlisten?.();
    };
  }, []);
  async function project(saveProject: boolean) {
    const { open, save } = await import("@tauri-apps/plugin-dialog");
    const options = {
      filters: [{ name: "OrchFlow", extensions: ["orchflow"] }],
    };
    const path = saveProject
      ? await save(options)
      : await open({ ...options, multiple: false });
    if (!path) return;
    if (saveProject) {
      setOperationProgress("Saving project and live adjustments...");
      const latestProject=projectRef.current;
      try {
        await invoke("save_project", {path,project:latestProject});
      }catch(error){
        const {message}=await import("@tauri-apps/plugin-dialog");
        await message(String(error),{title:"Project could not be saved",kind:"error"});
        throw error;
      }
      setDependencies(latestProject.dependencies);
      projectPathRef.current = path;
      setProjectName(path.split(/[\\/]/).pop()?.replace(/\.(orchflow|json)$/i,"") || "Untitled");
      setStatus(`Saved ${path}`);
    } else {
      await openProjectAtPath(path);
    }
  }
  return (
    <main>
      <dialog ref={clearSceneDialog} className="clear-scene-dialog"
        aria-labelledby="clear-scene-title" aria-describedby="clear-scene-description"
        onCancel={()=>setClearScenePrompt(false)}>
        <h2 id="clear-scene-title">Clear scene?</h2>
        <p id="clear-scene-description">All nodes and connections will be removed and unsaved changes discarded. This cannot be undone.</p>
        <p className="hint">Your saved project and linked files stay on disk.</p>
        <div className="clear-scene-actions">
          <button autoFocus onClick={()=>setClearScenePrompt(false)}>Cancel</button>
          <button className="clear-scene-confirm" disabled={busy} onClick={clearScene}>Clear scene</button>
        </div>
      </dialog>
      <header>
        <div className="brand">
          ◈{" "}
          <div>
            OrchFlow<small>USD WORKSPACE</small>
          </div>
        </div>
        <nav>
          <button disabled={!history.current.past.length || busy || activeTab!=="canvas"} title="Undo project edit (Ctrl+Z)" onClick={()=>travel("undo")}>Undo</button>
          <button disabled={!history.current.future.length || busy || activeTab!=="canvas"} title="Redo project edit (Ctrl+Shift+Z / Ctrl+Y)" onClick={()=>travel("redo")}>Redo</button>
          <button className={activeTab==="canvas"?"chosen":""} onClick={()=>setActiveTab("canvas")}>Canvas</button>
          <button className={activeTab==="applications"?"chosen":""} onClick={()=>setActiveTab("applications")}>Applications</button>
          {backendOutdated && (
            <button
              onClick={() =>
                run(async () => {
                  await navigator.clipboard.writeText(
                    JSON.stringify(projectModel, null, 2),
                  );
                  setStatus(
                    "Project JSON copied. Paste it into a .orchflow file before restarting.",
                  );
                })
              }
            >
              Copy project JSON
            </button>
          )}
          {toolbarOperations.map(operation=><button key={operation} draggable title="Click to add, or drag onto an edge or node to connect and make space"
            onDragStart={e=>{setGraphModeRequest(n=>n+1);e.dataTransfer.setData("application/orchflow-node",operation);e.dataTransfer.effectAllowed="copy";}}
            onDragEnd={()=>setDropTarget(null)} onClick={()=>addNode(operation)}>
            ＋ {operation === "materialx"?"MaterialX":operation === "material_bind"?"Material Bind":operation === "light"?"Light":operation === "hdri"?"HDRI":operation === "hair"?"Hair":operation === "camera"?"Camera":operation === "blender"?"Blender":operation === "assetOverride"?"Asset Override":operation[0].toUpperCase()+operation.slice(1)}
          </button>)}
          <button disabled={busy || (!nodes.length && !dependencies.length)}
            title="Clear the scene and start a new project" onClick={()=>setClearScenePrompt(true)}>
            Clear scene
          </button>
          <button disabled={busy} onClick={() => run(() => project(false))}>
            Open project
          </button>
          <button disabled={busy} onClick={() => run(() => project(true))}>
            Save project
          </button>
        </nav>
        <small className="badge">HYDRA / 02</small>
      </header>
      {operationProgress&&<div className="project-progress"><OperationProgress label={operationProgress}/></div>}
      {status.includes("Warning:") && (
        <div className="project-warning" role="alert">
          <span>{status}</span>
          <button onClick={() => setStatus("Ready")}>Dismiss</button>
        </div>
      )}
      {materialXEditorId && projectModel.nodes.some(n=>n.id===materialXEditorId && n.data.operation==="materialx") && <MaterialXPanel key={materialXEditorId} project={projectModel} nodeId={materialXEditorId} onProjectChange={applyProject} onClose={()=>setMaterialXEditorId(null)}/>}
      {overrideNodeId && projectModel.nodes.some(n => n.id === overrideNodeId) && (
        <div className="override-backdrop" onKeyDown={e => {
          if(e.key === "Escape") setOverrideNodeId(null);
          if(e.key === "Tab") {
            const controls = Array.from(e.currentTarget.querySelectorAll<HTMLElement>('button:not(:disabled),input:not(:disabled),select:not(:disabled),[tabindex="0"]'));
            const first = controls[0], last = controls[controls.length-1];
            if(e.shiftKey && document.activeElement === first) {e.preventDefault(); last?.focus();}
            else if(!e.shiftKey && document.activeElement === last) {e.preventDefault(); first?.focus();}
          }
        }}>
          <div className="override-modal" role="dialog" aria-modal="true" aria-label="Asset overrides">
            <button autoFocus onClick={() => setOverrideNodeId(null)}>Done</button>
            <DependencyTable key={overrideNodeId} nodeId={overrideNodeId} project={projectModel} onChange={applyProject} onStatus={reportStatus} onRescan={rescanUsdDependencies}/>
          </div>
        </div>
      )}
        <div className="application-tab-host" hidden={activeTab!=="applications"}><ApplicationSettings focusProfileId={settingsTarget.id} focusVisit={settingsTarget.visit}/></div>
        <section className="workspace" style={activeTab==="applications"?{display:"none"}:undefined}>
          <SceneOverview path={target.path} graph={target.graph} selected={selectedPrim} onSelect={path=>setPrimSelection({key:sceneKey,path})} onStatus={reportStatus}/>
          <div className={"canvas " + (sceneMode ? "scene-mode" : "")} onDrop={dropNode} onDragOver={e=>{
            if(e.dataTransfer.types.includes("application/orchflow-node")){e.preventDefault();e.dataTransfer.dropEffect="copy";const target=targetAt(e.target);setDropTarget(previous=>JSON.stringify(previous)===JSON.stringify(target)?previous:target);}
          }} onDragLeave={e=>{if(!e.currentTarget.contains(e.relatedTarget as Node))setDropTarget(null);}}>
            <NativeViewport key={currentProjectSession}
              onViewportError={(err) => { (bridgeStateRef.current as Record<string, unknown>).viewportError = err || null; }}
              cameraKey={comparison ? previewId : undefined}
              comparison={comparison}
              selectedPrim={selectedPrim}
              path={target.path}
              graph={target.graph}
              emptyTitle={target.title}
              emptyMessage={target.message}
              previewControls={previewNode?.data.operation === "application" ? <div className="application-preview-controls">
                <span>Handoff USD</span>
                <input type="range" min="0" max="1" step="1" aria-label="Application preview: handoff or published USD" aria-valuetext={activePreviewMode === "published" ? "Published USD" : "Handoff USD"} value={activePreviewMode === "published" ? 1 : 0}
                  onChange={e=>setApplicationPreviews(p=>({...p,[previewId]:e.target.value === "1" ? "published" : "handoff"}))}/>
                <span>Published USD</span>
              </div> : undefined}
              onRuntime={setRuntime}
              onMode={setSceneMode}
              graphModeRequest={graphModeRequest}
              viewportSettings={projectModel.viewport}
              onViewportSettingsChange={(patch) =>
                applyProject(
                  {
                    ...projectRef.current,
                    viewport: {
                      pinnedNodeId: projectRef.current.viewport?.pinnedNodeId ?? null,
                      ...projectRef.current.viewport,
                      ...patch,
                    },
                  },
                  "Viewport settings",
                  "viewport",
                  false,
                )
              }
              onSelectPrim={path=>setPrimSelection({key:sceneKey,path})}
            />
            {pinnedNode && (
              <div className="viewport-pin-badge">
                Pinned: {pinnedNode.data.label}
                <button onClick={() => pin(null)}>Unpin</button>
              </div>
            )}
            <PinContext.Provider
              value={{
                pinnedId: pinnedNode?.id || null,
                toggle: (id) =>
                  pin(pinnedNodeId === id ? null : id),
              }}
            >
              <ReactFlow
                onInit={instance=>{flowInstance.current=instance;}}
                nodes={nodes.map(n=>dropTarget?.kind === "node" && dropTarget.id===n.id?{...n,className:[n.className,"toolbar-drop-target"].filter(Boolean).join(" ")}:n)}
                edges={edges.map(e=>dropTarget?.kind === "edge" && dropTarget.id===e.id?{...e,style:{...e.style,stroke:"#a4e4bd",strokeWidth:4}}:e)}
                defaultEdgeOptions={{interactionWidth:32}}
                nodeTypes={nodeTypes}
                onNodesChange={changes=>{
                  const current=projectRef.current,expanded=expandProject(current);
                  const next=applyNodeChanges(changes,expanded.nodes);
                  if(changes.every(c=>c.type==="select" || c.type==="dimensions")){setNodes(next);return;}
                  const ids=new Set(next.map(n=>n.id));
                  applyProject({...current,...storeProject(next,expanded.edges.filter(e=>ids.has(e.source)&&ids.has(e.target)),current.dependencies)},changes.some(c=>c.type==="remove")?"Delete nodes":"Move nodes",changes.some(c=>c.type==="remove")?"delete":"drag");
                  if(changes.some(c=>c.type==="position" && !c.dragging))history.current.endGroup();
                  if(changes.some(c=>c.type==="remove"))queueMicrotask(()=>history.current.endGroup());
                }}
                onEdgesChange={changes=>{
                  if(changes.every(c=>c.type==="select")){setEdges(es=>applyEdgeChanges(changes,es));return;}
                  applyProject({...projectRef.current,edges:applyEdgeChanges(changes,projectRef.current.edges)},"Delete edges","delete");
                  queueMicrotask(()=>history.current.endGroup());
                }}
                onNodeClick={(_, n) => {
                  select(n.id);
                  setInspection(null);
                }}
                onConnect={(c) => {
                  const reaches = (
                    id: string,
                    seen = new Set<string>(),
                  ): boolean => {
                    if (id === c.source) return true;
                    if (seen.has(id)) return false;
                    seen.add(id);
                    return edges
                      .filter((e) => e.source === id)
                      .some((e) => reaches(e.target, seen));
                  };
                  if (reaches(c.target)) {
                    setStatus("Connection rejected: cycle detected");
                    return;
                  }
                  applyProject({...projectRef.current,edges:addEdge(c,projectRef.current.edges)},"Connect nodes");
                }}
                fitView
                fitViewOptions={{ maxZoom: 1 }}
                colorMode="dark"
                deleteKeyCode={["Backspace", "Delete"]}
              >
                <Background gap={28} color="#34483f" />
                <Controls />
              </ReactFlow>
            </PinContext.Provider>
          </div>
          <div
            className={"inspector-resizer" + (resizingInspector ? " dragging" : "")}
            role="separator"
            aria-orientation="vertical"
            aria-label="Resize node inspector"
            onMouseDown={startInspectorResize}
          />
          <aside className="node-inspector" style={{ width: inspectorWidth }}>
            <small className="eyebrow">NODE INSPECTOR</small>
            {node ? (
              <>
                <h2>{node.data.label}</h2>
                <button
                  aria-pressed={pinnedNodeId === node.id}
                  onClick={() =>
                    pin(pinnedNodeId === node.id ? null : node.id)
                  }
                >
                  {pinnedNodeId === node.id
                    ? "Unpin preview"
                    : "Pin preview"}
                </button>
                <label>
                  Name
                  <input
                    value={node.data.label}
                    onChange={(e) => update({ label: e.target.value })}
                  />
                </label>
                <label>
                  Function
                  <select
                    value={node.data.operation}
                    onChange={(e) => update({ operation: e.target.value })}
                  >
                    <option value="source">Source stage</option>
                    <option value="blender">Blender</option>
                    <option value="application">
                      Application
                    </option>
                    <option value="transform">Transform</option>
                    <option value="assetOverride">Asset Override</option>
                    <option value="materialx">MaterialX</option>
                    <option value="material_bind">Material Bind</option>
                    <option value="light">Light</option>
                    <option value="hdri">HDRI</option>
                    <option value="hair">Hair</option>
                    <option value="delete">Delete prims</option>
                    <option value="output">Output</option>
                  </select>
                </label>
                {node.data.operation === "assetOverride" && <button onClick={() => setOverrideNodeId(node.id)}>Edit Asset Overrides… ({node.data.assetOverrides?.length || 0})</button>}
                {node.data.operation === "delete" && <label><span className="field-label">Prim paths<InspectorHint text="One absolute path per line. Deactivates these prims on this branch; original files are unchanged. Instance-proxy children cannot be deleted separately."/></span><textarea value={(node.data.deletePaths || []).join("\n")} onChange={e=>update({deletePaths:e.target.value.split("\n").map(v=>v.trim()).filter(Boolean)})}/></label>}
                {node.data.operation === "transform" && (
                  <div className="transform-settings">
                    <h3 className="field-label">Transform<InspectorHint text="Applied after merging incoming stages. Translation is in metres; rotation uses XYZ order in degrees."/></h3>
                    {(["translation", "rotation", "scale"] as const).map(
                      (field) => (
                        <div key={field}>
                          <span className="vector-label">{field==="translation"?"Translation · m":field==="rotation"?"Rotation · °":"Scale"}</span>
                          <div className="vector-input">
                            {["X", "Y", "Z"].map((axis, index) => (
                              <label key={axis}>
                                {axis}
                                <NumberInput
                                  aria-label={`${field} ${axis}`}
                                  step={field === "rotation" ? 1 : 0.1}
                                  value={
                                    (node.data.transform || identityTransform)[
                                      field
                                    ][index]
                                  }
                                  onChange={(e) =>
                                    transformValue(
                                      field,
                                      index,
                                      Number(e.target.value),
                                    )
                                  }
                                />
                              </label>
                            ))}
                          </div>
                        </div>
                      ),
                    )}
                    <button
                      onClick={() =>
                        update({
                          transform: structuredClone(identityTransform),
                        })
                      }
                    >
                      Reset transform
                    </button>
                  </div>
                )}
                {node.data.operation === "materialx" && <button onClick={()=>setMaterialXEditorId(node.id)}>Edit MaterialX shading rules...</button>}
                {node.data.operation === "output" && (
                  <p className="field-label">Combined output<InspectorHint text="Combines incoming stages in memory. Select this node to preview the result; it does not write a USD file."/></p>
                )}
                {["source", "application"].includes(node.data.operation) && (
                  <>
                    {node.data.operation==="source"&&<>
                      <label>USD root layer<input placeholder="C:\\assets\\scene.usda" value={node.data.path} onChange={e=>update({path:e.target.value})}/></label>
                      <button disabled={busy || !isTauri()} onClick={()=>run(browse)}>Browse stage...</button>
                      <label className="checkbox-field" style={{marginTop: "8px", display: "flex", alignItems: "center", gap: "8px", cursor: "pointer"}}>
                        <input
                          type="checkbox"
                          checked={Boolean(node.data.instanced)}
                          onChange={e=>update({instanced: e.target.checked})}
                        />
                        <span>Native instancing <InspectorHint text="When enabled, OpenUSD treats this asset as a sealed prototype to save memory on heavy duplication, but child meshes become read-only proxies. Leave unchecked to allow component styling on sub-meshes (Dress, Kimono, etc.)."/></span>
                      </label>
                    </>}
                    {node.data.operation === "application" && <NativeProjectPanel projectName={projectName} onConfigure={()=>{setSettingsTarget(v=>({id:node.data.applicationProfileId || "",visit:v.visit+1}));setActiveTab("applications");}} key={node.id} project={projectModel} nodeId={node.id} data={node.data}
                      onChange={patch => applyProject(editNodeData(projectRef.current, node.id, patch),"Edit node",`field:${node.id}:${Object.keys(patch).join()}`)} onLive={(edited,result)=>{applyProject(recordLiveAdjustment(projectRef.current,node.id,edited,result));setApplicationPreviews(p=>({...p,[node.id]:"published"}));}} onPublished={(edited,result)=>{applyProject(recordPublication(projectRef.current,node.id,edited,result));setApplicationPreviews(p=>({...p,[node.id]:"published"}));}} onBaseline={result=>applyProject(recordManualBaseline(projectRef.current,node.id,result))} onNativeCreated={created=>{const next=recordNativeCreation(projectRef.current,node.id,created);applyProject(next);return next;}} onHandoff={created=>{applyProject(recordHandoff(projectRef.current,node.id,created));setApplicationPreviews(p=>({...p,[node.id]:"handoff"}));}} onStatus={reportStatus}/>}

                    {node.data.operation !== "application" && <div className="pair">
                      <label>
                        Up axis
                        <select
                          value={node.data.axis}
                          onChange={(e) => update({ axis: e.target.value })}
                        >
                          <option value="preserve">Preserve source</option>
                          <option>Y</option>
                          <option>Z</option>
                        </select>
                      </label>
                      <label>
                        Scale
                        <NumberInput
                          min="0.000001"
                          step="0.1"
                          value={node.data.scale}
                          onChange={(e) => {
                            const scale = Number(e.target.value);
                            if (Number.isFinite(scale) && scale > 0)
                              update({ scale });
                          }}
                        />
                      </label>
                    </div>}

                    {node.data.operation === "source" && <>
                    <hr />
                    <button
                      className="primary"
                      disabled={busy || !node.data.path || !isTauri()}
                      onClick={() =>
                        run(async () => {
                          setInspection(null);
                          setInspection(
                            await invoke("usd_request", {
                              request: {
                                operation: "inspect",
                                path: node.data.path,
                              },
                            }),
                          );
                          setStatus("Stage inspected using upstream OpenUSD");
                        })
                      }
                    >
                      {busy ? "Working…" : "Inspect USD stage"}
                    </button>
                    {inspection !== null && (
                      <pre>{JSON.stringify(inspection, null, 2)}</pre>
                    )}
                    </>}
                  </>
                )}

                {node.data.operation === "material_bind" && (
                  <MaterialBindingInspector
                    project={projectModel}
                    nodeId={node.id}
                    bindings={node.data.materialBindings}
                    onChange={(materialBindings) => update({ materialBindings })}
                  />
                )}

                {node.data.operation === "light" && (
                  <LightInspector
                    project={projectModel}
                    nodeId={node.id}
                    overrides={node.data.lightOverrides}
                    onChange={(lightOverrides) => update({ lightOverrides })}
                  />
                )}

                {node.data.operation === "hdri" && (
                  <HdriInspector
                    value={node.data.hdri}
                    dependencies={dependencies}
                    onChange={(hdri, nextDeps) => {
                      if (nextDeps) {
                        applyProject(
                          {
                            ...projectModel,
                            dependencies: nextDeps,
                            nodes: projectModel.nodes.map((n) =>
                              n.id !== node.id ? n : { ...n, data: { ...n.data, hdri } },
                            ),
                          },
                          "Update HDRI",
                        );
                      } else {
                        update({ hdri });
                      }
                    }}
                    onStatus={reportStatus}
                  />
                )}

                {node.data.operation === "camera" && (() => {
                  const defaultCam = {
                    yaw: 35,
                    pitch: 20,
                    zoom: 1,
                    panX: 0,
                    panY: 0,
                    fov: 45,
                    dofEnabled: false,
                    focusDistance: 5,
                    fStop: 5.6,
                  };
                  const views = node.data.cameraViews ?? (node.data.camera ? [{ id: "default", name: "Camera 1", camera: { ...defaultCam, ...node.data.camera } }] : []);
                  const activeViewId = projectModel.viewport?.activeCameraViewId ?? (views.length === 1 ? views[0].id : null);
                  const setViews = (nextViews: typeof views, nextViewportPatch?: { camera?: typeof defaultCam; activeCameraViewId?: string | null }) => {
                    const nextProject = editNodeData(projectRef.current, node.id, { cameraViews: nextViews, camera: undefined });
                    if (nextViewportPatch) {
                      applyProject(
                        {
                          ...nextProject,
                          viewport: {
                            pinnedNodeId: nextProject.viewport?.pinnedNodeId ?? null,
                            ...nextProject.viewport,
                            ...nextViewportPatch,
                          },
                        },
                        "Update camera view",
                        `field:${node.id}:cameraViews`,
                      );
                    } else {
                      applyProject(nextProject, "Update camera view", `field:${node.id}:cameraViews`);
                    }
                  };
                  const patchRow = (rowId: string, camPatch: Partial<typeof defaultCam>) => {
                    let updatedCam: typeof defaultCam | null = null;
                    const nextViews = views.map((v) => {
                      if (v.id !== rowId) return v;
                      updatedCam = { ...defaultCam, ...v.camera, ...camPatch };
                      return { ...v, camera: updatedCam };
                    });
                    const isRowActive = rowId === activeViewId;
                    setViews(
                      nextViews,
                      isRowActive && updatedCam
                        ? { camera: updatedCam, activeCameraViewId: rowId }
                        : undefined,
                    );
                  };
                  const cellInputStyle = {
                    width: 58,
                    boxSizing: "border-box" as const,
                    padding: "2px 4px",
                    fontSize: 11,
                    background: "rgba(255,255,255,0.05)",
                    color: "inherit",
                    border: "1px solid rgba(255,255,255,0.12)",
                    borderRadius: 4,
                  };
                  const numCell = (
                    value: number,
                    step: number,
                    onCommit: (v: number) => void,
                    min?: number,
                    max?: number,
                  ) => (
                    <CameraNumberInput
                      value={value}
                      step={step}
                      min={min}
                      max={max}
                      onCommit={onCommit}
                      style={cellInputStyle}
                    />
                  );
                  return (
                    <div className="field" style={{ display: "grid", gap: 8 }}>
                      <div style={{ display: "flex", gap: 6, alignItems: "center", justifyContent: "space-between", flexWrap: "wrap" }}>
                        <button
                          type="button"
                          onClick={() => {
                            const vpCam = projectRef.current.viewport?.camera;
                            const next = {
                              id: crypto.randomUUID(),
                              name: `Camera ${views.length + 1}`,
                              camera: { ...defaultCam, ...(vpCam ?? {}) },
                            };
                            setViews([...views, next], { activeCameraViewId: next.id, camera: next.camera });
                            reportStatus(`Created ${next.name} from current viewport`);
                          }}
                        >
                          + New from current view
                        </button>
                        <small style={{ opacity: 0.7 }}>{views.length} view{views.length === 1 ? "" : "s"}</small>
                      </div>
                      <div style={{ overflowX: "auto" }}>
                        <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 11, whiteSpace: "nowrap" }}>
                          <thead>
                            <tr style={{ textAlign: "left", opacity: 0.75 }}>
                              <th style={{ padding: "4px 6px", borderBottom: "1px solid rgba(255,255,255,0.12)" }}>Name</th>
                              <th style={{ padding: "4px 6px", borderBottom: "1px solid rgba(255,255,255,0.12)" }}>FoV</th>
                              <th style={{ padding: "4px 6px", borderBottom: "1px solid rgba(255,255,255,0.12)" }}>DoF</th>
                              <th style={{ padding: "4px 6px", borderBottom: "1px solid rgba(255,255,255,0.12)" }}>Focus</th>
                              <th style={{ padding: "4px 6px", borderBottom: "1px solid rgba(255,255,255,0.12)" }}>f-Stop</th>
                              <th style={{ padding: "4px 6px", borderBottom: "1px solid rgba(255,255,255,0.12)" }}>Yaw</th>
                              <th style={{ padding: "4px 6px", borderBottom: "1px solid rgba(255,255,255,0.12)" }}>Pitch</th>
                              <th style={{ padding: "4px 6px", borderBottom: "1px solid rgba(255,255,255,0.12)" }}>Zoom</th>
                              <th style={{ padding: "4px 6px", borderBottom: "1px solid rgba(255,255,255,0.12)" }}>Pan X</th>
                              <th style={{ padding: "4px 6px", borderBottom: "1px solid rgba(255,255,255,0.12)" }}>Pan Y</th>
                              <th style={{ padding: "4px 6px", borderBottom: "1px solid rgba(255,255,255,0.12)" }}>Actions</th>
                            </tr>
                          </thead>
                          <tbody>
                            {views.length === 0 ? (
                              <tr>
                                <td colSpan={11} style={{ padding: "8px 6px", opacity: 0.7 }}>
                                  No camera views yet. Click "+ New from current view" to add one.
                                </td>
                              </tr>
                            ) : (
                              views.map((row) => {
                                const cam = { ...defaultCam, ...row.camera };
                                const isActive = row.id === activeViewId;
                                return (
                                  <tr key={row.id} style={isActive ? { background: "rgba(90, 170, 255, 0.1)" } : undefined}>
                                    <td style={{ padding: "4px 6px", borderBottom: "1px solid rgba(255,255,255,0.06)" }}>
                                      <input
                                        type="text"
                                        value={row.name}
                                        onChange={(e) =>
                                          setViews(views.map((v) => (v.id === row.id ? { ...v, name: e.target.value } : v)))
                                        }
                                        style={{ ...cellInputStyle, width: 96 }}
                                      />
                                    </td>
                                    <td style={{ padding: "4px 6px", borderBottom: "1px solid rgba(255,255,255,0.06)" }}>
                                      {numCell(cam.fov, 1, (fov) => patchRow(row.id, { fov }), 5, 170)}
                                    </td>
                                    <td style={{ padding: "4px 6px", borderBottom: "1px solid rgba(255,255,255,0.06)", textAlign: "center" }}>
                                      <input
                                        type="checkbox"
                                        checked={Boolean(cam.dofEnabled)}
                                        onChange={(e) => patchRow(row.id, { dofEnabled: e.target.checked })}
                                      />
                                    </td>
                                    <td style={{ padding: "4px 6px", borderBottom: "1px solid rgba(255,255,255,0.06)" }}>
                                      {numCell(cam.focusDistance, 0.1, (focusDistance) => patchRow(row.id, { focusDistance }), 0.01)}
                                    </td>
                                    <td style={{ padding: "4px 6px", borderBottom: "1px solid rgba(255,255,255,0.06)" }}>
                                      {numCell(cam.fStop, 0.1, (fStop) => patchRow(row.id, { fStop }), 0.1)}
                                    </td>
                                    <td style={{ padding: "4px 6px", borderBottom: "1px solid rgba(255,255,255,0.06)" }}>
                                      {numCell(cam.yaw, 1, (yaw) => patchRow(row.id, { yaw }))}
                                    </td>
                                    <td style={{ padding: "4px 6px", borderBottom: "1px solid rgba(255,255,255,0.06)" }}>
                                      {numCell(cam.pitch, 1, (pitch) => patchRow(row.id, { pitch }))}
                                    </td>
                                    <td style={{ padding: "4px 6px", borderBottom: "1px solid rgba(255,255,255,0.06)" }}>
                                      {numCell(cam.zoom, 0.05, (zoom) => patchRow(row.id, { zoom }), 0.05)}
                                    </td>
                                    <td style={{ padding: "4px 6px", borderBottom: "1px solid rgba(255,255,255,0.06)" }}>
                                      {numCell(cam.panX, 0.05, (panX) => patchRow(row.id, { panX }))}
                                    </td>
                                    <td style={{ padding: "4px 6px", borderBottom: "1px solid rgba(255,255,255,0.06)" }}>
                                      {numCell(cam.panY, 0.05, (panY) => patchRow(row.id, { panY }))}
                                    </td>
                                    <td style={{ padding: "4px 6px", borderBottom: "1px solid rgba(255,255,255,0.06)" }}>
                                      <div style={{ display: "inline-flex", gap: 4 }}>
                                        <button
                                          type="button"
                                          title={isActive ? "Active viewport camera (edits apply live)" : "Apply this camera view to the viewport"}
                                          disabled={isActive}
                                          onClick={() => {
                                            applyProject(
                                              {
                                                ...projectRef.current,
                                                viewport: {
                                                  pinnedNodeId: projectRef.current.viewport?.pinnedNodeId ?? null,
                                                  ...projectRef.current.viewport,
                                                  activeCameraViewId: row.id,
                                                  camera: cam,
                                                },
                                              },
                                              `Apply ${row.name} to viewport`,
                                            );
                                            reportStatus(`Applied ${row.name} to viewport`);
                                          }}
                                        >
                                          {isActive ? "Active" : "Apply"}
                                        </button>
                                        <button
                                          type="button"
                                          title="Update this camera view from the current viewport"
                                          onClick={() => {
                                            const vpCam = projectRef.current.viewport?.camera;
                                            if (!vpCam) {
                                              reportStatus("No active viewport camera to capture");
                                              return;
                                            }
                                            if (!window.confirm(`Update "${row.name}" from the current viewport camera?`)) {
                                              return;
                                            }
                                            setViews(
                                              views.map((v) => (v.id !== row.id ? v : { ...v, camera: { ...defaultCam, ...v.camera, ...vpCam } })),
                                              { activeCameraViewId: row.id },
                                            );
                                            reportStatus(`Updated ${row.name} from viewport`);
                                          }}
                                        >
                                          Update
                                        </button>
                                        <button
                                          type="button"
                                          title="Delete this camera view"
                                          onClick={() =>
                                            setViews(
                                              views.filter((v) => v.id !== row.id),
                                              isActive ? { activeCameraViewId: null } : undefined,
                                            )
                                          }
                                        >
                                          ×
                                        </button>
                                      </div>
                                    </td>
                                  </tr>
                                );
                              })
                            )}
                          </tbody>
                        </table>
                      </div>
                    </div>
                  );
                })()}

                {node.data.operation === "hair" && (
                  <HairInspector
                    value={node.data.hair}
                    graph={edges.some((e) => e.target === node.id) ? graphPreview(projectModel, node.id) : undefined}
                    onChange={(hair) => update({ hair })}
                  />
                )}

                {node.data.operation === "blender" && (
                  <BlenderInspector
                    project={projectModel}
                    nodeId={node.id}
                    onProjectChange={(p) => applyProject(p, "Update Blender node")}
                    onStatus={reportStatus}
                  />
                )}
                <button
                  className="danger"
                  onClick={() => {
                    const current=projectRef.current;
                    applyProject({...current,nodes:current.nodes.filter(n=>n.id!==selected),edges:current.edges.filter(e=>e.source!==selected && e.target!==selected)},"Delete node");
                    setInspection(null);
                  }}
                >
                  Delete node
                </button>
              </>
            ) : (
              <p className="hint">Select or add a node to begin.</p>
            )}
          </aside>
        </section>
      <footer className="status">
        <span>{status}</span>
        <span>{runtime}</span>
      </footer>
    </main>
  );
}

export default function App(){return <ApplicationCatalogProvider><Workspace/></ApplicationCatalogProvider>;}
