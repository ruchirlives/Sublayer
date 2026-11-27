import type { PoseEditing } from "./PoseViewportEditor";
import {InspectorHint} from "./InspectorHint";
import {HdriInspector} from "./HdriInspector";
import {PostFxInspector} from "./PostFxInspector";
import {NumberInput} from "./NumberInput";
import {OperationProgress} from "./OperationProgress";
import {
  createContext,
  lazy,
  memo,
  Suspense,
  useContext,
  useEffect,
  useCallback,
  useMemo,
  useRef,
  useState,
} from "react";
import {ProjectHistory,historyShortcut} from "./projectHistory";
import {SceneOverview} from "./SceneOverview";
const DependencyTable = lazy(() => import("./DependencyTable").then((m) => ({ default: m.DependencyTable })));
import {
  recordHandoff,
  recordNativeCreation,
  recordManualBaseline,
  recordPublication,
  recordLiveAdjustment,
  mergeDiscoveredInputs,
  mergeUsdDiscovery,
  fileKey,
  graphPreview,
  defaultCacheEditSettings,
  defaultHairSettings,
  defaultCharacterSettings,
  defaultMaterialX,
  identityTransform,
  defaultScatter,
  defaultPostFx,
  registerFiles,
  editNodeData,
  expandProject,
  storeProject,
  type Data,
  type StageNode,
  type Dependency,
  type ProceduralGraph,
  type StoredProject,
  type ViewportCamera,
  type ViewportSettings,
} from "./fileRegistry";
import type { FabDropOutcome, MegascansAssetSummary } from "./MegascansPanel";
const MegascansPanel = lazy(() => import("./MegascansPanel").then((m) => ({ default: m.MegascansPanel })));
import { detachNodeFromEdges, insertExistingNodeOnEdge, insertToolbarNode, toolbarOperations, type DropTarget } from "./graphInsertion";
import { previewTarget } from "./previewTarget";
import { NativeViewport } from "./NativeViewport";
import { ApplicationCatalogProvider, ApplicationSettings, useApplicationCatalog } from "./applicationProfiles";
import { defaultEditScope } from "./profilePolicy";
import { NativeProjectPanel } from "./NativeProjectPanel";
const MaterialXPanel = lazy(() => import("./MaterialXPanel").then((m) => ({ default: m.MaterialXPanel })));
import { MaterialBindingInspector } from "./MaterialBindingInspector";
import { LightInspector } from "./LightInspector";
const HairInspector = lazy(() => import("./HairInspector").then((m) => ({ default: m.HairInspector })));
const CacheEditInspector = lazy(() => import("./CacheEditInspector").then((m) => ({ default: m.CacheEditInspector })));
const PoseInspector = lazy(() => import("./PoseInspector").then((m) => ({ default: m.PoseInspector })));
const MetahumanInspector = lazy(() => import("./MetahumanInspector").then(m => ({default:m.MetahumanInspector})));
const CharacterInspector = lazy(() => import("./CharacterInspector").then((m) => ({ default: m.CharacterInspector })));
const BlenderInspector = lazy(() => import("./BlenderInspector").then((m) => ({ default: m.BlenderInspector })));
import { TransformInspector } from "./TransformInspector";
import { GroupInspector } from "./GroupInspector";
import { InstancerInspector } from "./InstancerInspector";
import { defaultInstancer, type InstancerSettings } from "./fileRegistry";
import { ScatterInspector } from "./ScatterInspector";
import { invoke, isTauri } from "@tauri-apps/api/core";
import { listen } from "@tauri-apps/api/event";
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
const LAST_PROJECT_STORAGE_KEY = "sublayer.lastProjectPath";
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
      frontAxis: "preserve",
      scale: 1,
      nativeProject: "",
      publishedPath: "",
    },
  },
];
const THEME_STORAGE_KEY = "sublayer-theme";
const THEMES: Record<string, { label: string; vars: Record<string, string> }> = {
  forest: {
    label: "Forest Slate",
    vars: {
      "--bg-app": "#101719",
      "--bg-header": "#141e22",
      "--bg-sidebar": "#152025",
      "--bg-control": "#1b292e",
      "--bg-control-muted": "#162024",
      "--bg-card": "#19282deb",
      "--bg-canvas-center": "#263638",
      "--bg-canvas-mid": "#142024",
      "--bg-canvas-edge": "#10191d",
      "--border-subtle": "#2b383c",
      "--border-strong": "#34454b",
      "--accent": "#9fe2b6",
      "--accent-soft": "#213a34",
      "--accent-border": "#66a587",
      "--accent-text": "#ccf5d9",
    },
  },
  obsidian: {
    label: "Obsidian",
    vars: {
      "--bg-app": "#111214",
      "--bg-header": "#16181b",
      "--bg-sidebar": "#17191d",
      "--bg-control": "#1e2126",
      "--bg-control-muted": "#191b1f",
      "--bg-card": "#1c1f24eb",
      "--bg-canvas-center": "#282c34",
      "--bg-canvas-mid": "#17191e",
      "--bg-canvas-edge": "#111317",
      "--border-subtle": "#2a2e35",
      "--border-strong": "#373c45",
      "--accent": "#93c5fd",
      "--accent-soft": "#1e2c3f",
      "--accent-border": "#5b89c4",
      "--accent-text": "#dbeafe",
    },
  },
  indigo: {
    label: "Midnight Indigo",
    vars: {
      "--bg-app": "#0e121b",
      "--bg-header": "#131926",
      "--bg-sidebar": "#141b29",
      "--bg-control": "#1a2335",
      "--bg-control-muted": "#151d2b",
      "--bg-card": "#182133eb",
      "--bg-canvas-center": "#24304a",
      "--bg-canvas-mid": "#141b2b",
      "--bg-canvas-edge": "#0e131e",
      "--border-subtle": "#27324a",
      "--border-strong": "#334261",
      "--accent": "#7dd3fc",
      "--accent-soft": "#1b2f45",
      "--accent-border": "#4f8eb8",
      "--accent-text": "#d6f0ff",
    },
  },
  graphite: {
    label: "Warm Graphite",
    vars: {
      "--bg-app": "#141210",
      "--bg-header": "#1b1815",
      "--bg-sidebar": "#1d1916",
      "--bg-control": "#26211d",
      "--bg-control-muted": "#1f1b18",
      "--bg-card": "#241f1beb",
      "--bg-canvas-center": "#342c26",
      "--bg-canvas-mid": "#1d1815",
      "--bg-canvas-edge": "#14110f",
      "--border-subtle": "#362f29",
      "--border-strong": "#473e36",
      "--accent": "#f6c478",
      "--accent-soft": "#382c1d",
      "--accent-border": "#b5894b",
      "--accent-text": "#fde6bd",
    },
  },
  plum: {
    label: "Deep Plum",
    vars: {
      "--bg-app": "#131018",
      "--bg-header": "#191521",
      "--bg-sidebar": "#1b1624",
      "--bg-control": "#241d30",
      "--bg-control-muted": "#1d1826",
      "--bg-card": "#221b2eeb",
      "--bg-canvas-center": "#322742",
      "--bg-canvas-mid": "#1b1525",
      "--bg-canvas-edge": "#120e19",
      "--border-subtle": "#342a45",
      "--border-strong": "#433659",
      "--accent": "#c4b5fd",
      "--accent-soft": "#2d2345",
      "--accent-border": "#7f69b8",
      "--accent-text": "#e9e2ff",
    },
  },
  nordic: {
    label: "Nordic Teal",
    vars: {
      "--bg-app": "#0d1619",
      "--bg-header": "#111e23",
      "--bg-sidebar": "#122026",
      "--bg-control": "#172a31",
      "--bg-control-muted": "#132228",
      "--bg-card": "#15272eeb",
      "--bg-canvas-center": "#1d3840",
      "--bg-canvas-mid": "#112026",
      "--bg-canvas-edge": "#0c161a",
      "--border-subtle": "#243b42",
      "--border-strong": "#2f4d57",
      "--accent": "#6ee7d8",
      "--accent-soft": "#16383b",
      "--accent-border": "#469c97",
      "--accent-text": "#ccfbf4",
    },
  },
};

function applyTheme(id: string) {
  const theme = THEMES[id] || THEMES.indigo;
  const root = document.documentElement.style;
  for (const [k, v] of Object.entries(theme.vars)) {
    root.setProperty(k, v);
  }
}

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
const Card = memo(function Card({ id, data, selected }: NodeProps<StageNode>) {
  const pin = useContext(PinContext);
  const pinned = pin.pinnedId === id;
  return (
    <div
      data-node-type={data.operation}
      data-pinned={pinned ? "true" : undefined}
      className={"card " + (selected ? "selected" : "")}
    >
      <Handle type="target" position={Position.Left} />
      <div className="card-header">
        <span className="card-kind">
          <span className="card-dot" aria-hidden="true" />
          USD / {data.operation.toUpperCase()}
        </span>
        <button
          className="pin-toggle nodrag nopan"
          aria-label={`${pinned ? "Unpin" : "Pin"} ${data.label} output`}
          aria-pressed={pinned}
          title={pinned ? "Pinned to viewport (click to unpin)" : "Pin background preview to this node"}
          onClick={(e) => {
            e.stopPropagation();
            pin.toggle(id);
          }}
        >
          {pinned ? "●" : "○"}
        </button>
      </div>
      <div className="card-body">
        <strong>{data.label}</strong>
        <p>
          {["delete", "scatter", "instancer", "transform", "group", "output", "assetOverride", "materialx", "material_bind", "light", "hdri", "postfx", "hair", "cache_edit", "pose", "character"].includes(data.operation)
            ? "Combined USD inputs"
            : data.operation === "blender"
              ? (data.nativeProject?.split(/[\\/]/).pop() || "No .blend linked")
              : data.operation === "application"
              ? data.nativeProject?.split(/[\\/]/).pop() || data.app || "No profile selected"
              : data.path.split(/[\\/]/).pop() || "No stage assigned"}
        </p>
      </div>
      <footer className="card-footer">
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
          ? data.liveAdjustment
            ? `Applied · ${Array.isArray(data.liveAdjustment.report?.applied) ? data.liveAdjustment.report.applied.length : 0} edit(s)`
            : data.manualBaseline || data.nativeImport
              ? "Baseline ready · awaiting export"
              : data.handoff
                ? "Handoff ready"
                : data.applicationProfileId
                  ? "Needs handoff"
                  : "Choose profile"
          : data.operation === "transform"
          ? `XYZ scale ${(data.transform || identityTransform).scale.join(", ")}`
          : data.operation === "group"
          ? `/${(data.groupName || "Group").trim() || "Group"} · scale ${(data.transform || identityTransform).scale.join(", ")}`
          : data.operation === "instancer"
          ? `${data.instancer?.instances.length || 0} manual instances`
          : data.operation === "scatter"
          ? `${(data.scatter || defaultScatter).count} instances · ${(data.scatter || defaultScatter).distribution}`
          : data.operation === "pose"
          ? `${(data.pose?.mappings || []).filter(r => r.enabled !== false && r.entryId).length} skeleton mappings`
          : data.operation === "character"
          ? `${(data.character?.animationOverrides || []).filter((r) => r.enabled).length} anim · ${(data.character?.skeletonBindings || []).filter((r) => r.enabled).length} skel · ${(data.character?.jointAttachments || []).filter((r) => r.enabled).length} joint`
          : data.operation === "postfx"
          ? (() => {
              const fx = { ...defaultPostFx, ...(data.postFx || {}) };
              const active = [
                fx.gradeEnabled && "Grade",
                fx.autoExposureEnabled && "AutoExp",
                fx.bloomEnabled && "Bloom",
                fx.chromabEnabled && "ChromAb",
                fx.vignetteEnabled && "Vignette",
                fx.filmGrainEnabled && "Grain",
              ].filter(Boolean);
              return active.length > 0 ? `${fx.tonemapOperator} · ${active.join(", ")}` : `${fx.tonemapOperator} · RTX PostFX`;
            })()
          : data.operation === "output"
            ? "In-memory USD output"
            : `${data.axis} up · scale ${data.scale}`}
      </footer>
      <Handle type="source" position={Position.Right} />
    </div>
  );
});
const nodeTypes = { stage: Card };
function Workspace() {
  const { catalog } = useApplicationCatalog();
  const [settingsTarget,setSettingsTarget]=useState({id:"",visit:0});
  const [nodeContextMenu, setNodeContextMenu] = useState<{ nodeId: string; x: number; y: number } | null>(null);
  const [edgeContextMenu, setEdgeContextMenu] = useState<{ edgeId: string; x: number; y: number } | null>(null);
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
  const [materialXPrimDrop,setMaterialXPrimDrop]=useState<{primPath:string;seq:number}|null>(null);
  const [megascansOpen, setMegascansOpen] = useState(false);
  const [pendingFabDrops, setPendingFabDrops] = useState<Record<string, { nodeId: string; url: string; title: string }>>({});
  const pendingFabDropsRef = useRef(pendingFabDrops);
  pendingFabDropsRef.current = pendingFabDrops;
  const [overrideNodeId, setOverrideNodeId] = useState<string | null>(null);
  const [inspectorWidth, setInspectorWidth] = useState(310);
  const [resizingInspector, setResizingInspector] = useState(false);
  const [viewportFrame, setViewportFrame] = useState(0);
  const [poseEditing,setPoseEditing] = useState<PoseEditing|null>(null);
  const [seekFrameRequest, setSeekFrameRequest] = useState<{ frame: number; seq: number } | null>(null);
  const viewportFrameRef = useRef(0);
  viewportFrameRef.current = viewportFrame;
  const viewportCameraTransformRef = useRef<{ translate: [number, number, number]; rotate: [number, number, number] } | null>(null);
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
  useEffect(()=>setPoseEditing(null),[currentProjectSession]);
  const [clearScenePrompt,setClearScenePrompt]=useState(false);
  const clearSceneDialog=useRef<HTMLDialogElement>(null);
  useEffect(()=>{
    if(clearScenePrompt)clearSceneDialog.current?.showModal();
    else clearSceneDialog.current?.close();
  },[clearScenePrompt]);
  const [instancerEditing, setInstancerEditing] = useState<string | null>(null);
  const [instancerSelection, setInstancerSelection] = useState<{nodeId: string; id: string | null} | null>(null);
  const history=useRef(new ProjectHistory());
  const [,historyChanged]=useState(0);
  function commitQuickstart(nodeId: string, original: StoredProject, prepared: StoredProject) {
    if (projectRef.current !== original) {
      throw new Error("Quick start wrote the new files, but the project changed during preparation. Its current links were preserved.");
    }
    applyProject(prepared, "Prepare Blender round trip");
    setApplicationPreviews(previous => ({ ...previous, [nodeId]: "handoff" }));
    return prepared;
  }

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
  const keyHandlerRef = useRef<(event: KeyboardEvent) => void>(() => {});
  keyHandlerRef.current = (event: KeyboardEvent) => {
    const editing = event
      .composedPath()
      .some(
        (target) =>
          target instanceof HTMLElement &&
          (target.matches("input,textarea,select") || target.isContentEditable),
      );
    const direction = historyShortcut(event, editing);
    if (direction && !busy && activeTab === "canvas") {
      event.preventDefault();
      travel(direction);
    }
  };
  useEffect(() => {
    const key = (event: KeyboardEvent) => keyHandlerRef.current(event);
    const blur = () => history.current.endGroup();
    window.addEventListener("keydown", key);
    window.addEventListener("focusout", blur);
    return () => {
      window.removeEventListener("keydown", key);
      window.removeEventListener("focusout", blur);
    };
  }, []);
  function pin(id:string|null){applyProject({...projectRef.current,viewport:{...projectRef.current.viewport,pinnedNodeId:id}},"Pin preview");}
  const node = nodes.find((n) => n.id === selected);
  useEffect(() => { setInstancerEditing(node?.data.operation === "instancer" ? node.id : null); setInstancerSelection(null); }, [node?.id, currentProjectSession]);
  const pinnedNode = nodes.find((n) => n.id === pinnedNodeId);
  const previewNode = pinnedNode || node;
  const previewId = previewNode?.id || "";
  const [applicationPreviews, setApplicationPreviews] = useState<Record<string,"handoff"|"published"|"input">>({});
  const previewMode = applicationPreviews[previewId] || (previewNode?.data.liveAdjustment || previewNode?.data.mdBridgePath ? "published" : "handoff");
  const activePreviewMode = pinnedNode && previewMode === "input" ? "handoff" : previewMode;
  const target = useMemo(
    () => previewTarget(projectModel, previewId, activePreviewMode),
    [projectModel, previewId, activePreviewMode],
  );
  const comparisonHandoff=previewNode?.data.operation==="application" ? previewNode.data.handoff : null;
  const comparisonPath=projectModel.dependencies.find(d=>d.id===comparisonHandoff?.dependencyId)?.path;
  const comparison = useMemo(
    () =>
      comparisonHandoff && comparisonPath
        ? JSON.stringify({
            key: previewId,
            path: comparisonPath,
            inverse: comparisonHandoff.conversion.inverse,
            handoff: activePreviewMode === "handoff",
          })
        : undefined,
    [comparisonHandoff, comparisonPath, previewId, activePreviewMode],
  );
  const sceneKey = useMemo(
    () => JSON.stringify([target.path, target.graph]),
    [target.path, target.graph],
  );
  const [primSelection,setPrimSelection]=useState({key:"",path:""});
  const [isolatedPrim, setIsolatedPrim] = useState<string | null>(null);
  const [hiddenPrims, setHiddenPrims] = useState<string[]>([]);
  const [activeGroomGizmo, setActiveGroomGizmo] = useState<{ nodeId: string; groomId: string } | null>(null);
  const selectedPrim=primSelection.key===sceneKey?primSelection.path:"";
  const flowInstance=useRef<ReactFlowInstance<StageNode,Edge>|null>(null);
  const [dropTarget,setDropTarget]=useState<DropTarget>(null);
  const altDragRef = useRef<{ nodeId: string; detached: boolean } | null>(null);
  const [lastProjectPath, setLastProjectPath] = useState<string | null>(() => {
    try {
      return localStorage.getItem(LAST_PROJECT_STORAGE_KEY);
    } catch {
      return null;
    }
  });
  function recordLastProject(path: string) {
    setLastProjectPath(path);
    try {
      localStorage.setItem(LAST_PROJECT_STORAGE_KEY, path);
    } catch {
      /* ignore storage errors */
    }
  }
  const startupLaunchHandledRef = useRef(false);
  useEffect(() => {
    if (!isTauri() || startupLaunchHandledRef.current) return;
    startupLaunchHandledRef.current = true;
    void invoke<{ openLastProject: boolean; projectPath: string | null }>("startup_launch_options")
      .then((opts) => {
        const targetPath = opts.projectPath || (opts.openLastProject ? lastProjectPath : null);
        if (targetPath) {
          void run(() => bridgeStateRef.current.openProjectAtPath(targetPath));
        }
      })
      .catch(() => {});
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);
  const [themeId, setThemeId] = useState<string>(() => {
    try {
      const saved = localStorage.getItem(THEME_STORAGE_KEY);
      return saved && THEMES[saved] ? saved : "indigo";
    } catch {
      return "indigo";
    }
  });
  useEffect(() => {
    applyTheme(themeId);
    try {
      localStorage.setItem(THEME_STORAGE_KEY, themeId);
    } catch {
      /* ignore storage errors */
    }
  }, [themeId]);
  function pointerCoords(event: MouseEvent | TouchEvent | React.MouseEvent | React.TouchEvent): { x: number; y: number } | null {
    if ("clientX" in event) return { x: event.clientX, y: event.clientY };
    const touch = event.touches[0] || event.changedTouches[0];
    return touch ? { x: touch.clientX, y: touch.clientY } : null;
  }
  function edgeUnderPointer(event: MouseEvent | TouchEvent | React.MouseEvent | React.TouchEvent, excludeNodeId: string): DropTarget {
    const pt = pointerCoords(event);
    if (!pt) return null;
    const els = document.elementsFromPoint(pt.x, pt.y);
    for (const el of els) {
      const edgeEl = el instanceof Element ? el.closest(".react-flow__edge") : null;
      const edgeId = edgeEl?.getAttribute("data-id");
      if (!edgeId) continue;
      const edge = projectRef.current.edges.find((e) => e.id === edgeId);
      if (edge && edge.source !== excludeNodeId && edge.target !== excludeNodeId) {
        return { kind: "edge", id: edgeId };
      }
    }
    return null;
  }
  function targetAt(element:EventTarget|null):DropTarget {
    if(!(element instanceof Element))return null;
    const node=element.closest(".react-flow__node");
    if(node)return {kind:"node",id:node.getAttribute("data-id")!};
    const edge=element.closest(".react-flow__edge");
    return edge?{kind:"edge",id:edge.getAttribute("data-id")!}:null;
  }
  function defaultApplicationFields(): Partial<Data> {
    const chosen =
      catalog.applications.find((p) => p.app === "Blender" && p.adapterVersion === 2) ||
      catalog.applications.find((p) => p.app === "Blender") ||
      catalog.applications[0];
    if (!chosen) return {};
    return {
      app: chosen.app,
      applicationProfileId: chosen.id,
      editScope: defaultEditScope(chosen),
    };
  }
  function sanitizeUsdMaterialName(raw: string): string {
    const cleaned = raw.replace(/[^A-Za-z0-9_]+/g, "_").replace(/^_+/, "");
    const prefixed = /^[A-Za-z_]/.test(cleaned) ? cleaned : `M_${cleaned || "Megascans"}`;
    return prefixed.slice(0, 96);
  }

  function buildProjectWithMegascansAsset(
    base: StoredProject,
    asset: MegascansAssetSummary,
    position: { x: number; y: number },
    dropTargetArg: DropTarget,
    replaceNodeId?: string,
  ): { project: StoredProject; nodeId: string } {
    let deps = [...base.dependencies];
    const regOne = (filePath: string | undefined, kind: "usd" | "asset"): string | null => {
      if (!filePath || !filePath.trim()) return null;
      const res = registerFiles(deps, [filePath], kind);
      deps = res.dependencies;
      return res.ids[0] || null;
    };

    const nodeId = replaceNodeId || crypto.randomUUID();
    const isSurface = asset.assetType === "surface" || asset.assetType === "atlas";
    const isHdri = asset.assetType === "hdri";

    let storedNodeData: StoredProject["nodes"][number]["data"];
    let discoveredForSource: { sourceId: string; files: { path: string; kind: "usd" | "asset" }[] } | null = null;

    if (isSurface) {
      const baseColorId = regOne(asset.pbrMaps.albedo, "asset");
      const roughnessId = regOne(asset.pbrMaps.roughness || asset.pbrMaps.gloss, "asset");
      const metallicId = regOne(asset.pbrMaps.metalness, "asset");
      const normalId = regOne(asset.pbrMaps.normal || asset.pbrMaps.bump, "asset");
      const opacityId = regOne(asset.pbrMaps.opacity, "asset");
      const emissionId = regOne(asset.pbrMaps.emissive, "asset");
      const matSettings = {
        ...structuredClone(defaultMaterialX),
        name: sanitizeUsdMaterialName(asset.name),
        baseColor: [0.85, 0.85, 0.85] as [number, number, number],
        subsurface: asset.pbrMaps.translucency ? 0.42 : 0,
        baseColorTextureDependencyId: baseColorId,
        roughnessTextureDependencyId: roughnessId,
        metallicTextureDependencyId: metallicId,
        normalTextureDependencyId: normalId,
        opacityTextureDependencyId: opacityId,
        emissionTextureDependencyId: emissionId,
      };
      const baseSeed = storeProject([{ ...seed[0], id: nodeId, position }], [], deps).nodes[0].data;
      storedNodeData = {
        ...baseSeed,
        label: asset.name,
        operation: "materialx",
        transform: structuredClone(identityTransform),
        materialX: matSettings,
        materialXRules: [
          {
            id: crypto.randomUUID(),
            enabled: true,
            mode: "replace",
            searchType: "simple",
            query: "*",
            material: structuredClone(matSettings),
          },
        ],
      };
    } else if (isHdri) {
      const hdriId = regOne(asset.primaryPath, "asset");
      const baseSeed = storeProject([{ ...seed[0], id: nodeId, position }], [], deps).nodes[0].data;
      storedNodeData = {
        ...baseSeed,
        label: asset.name,
        operation: "hdri",
        transform: structuredClone(identityTransform),
        hdri: {
          textureDependencyId: hdriId,
          intensity: 1,
          exposure: 0,
          rotation: 0,
          color: [1, 1, 1],
          textureFormat: "latlong",
        },
      };
    } else {
      const srcId = regOne(asset.primaryPath, "usd");
      const baseSeed = storeProject([{ ...seed[0], id: nodeId, position }], [], deps).nodes[0].data;
      const isJson = /\.json$/i.test(asset.primaryPath);
      storedNodeData = {
        ...baseSeed,
        label: asset.name,
        operation: "source",
        inputDependencyId: srcId,
        instanced: asset.assetType !== "3dplant",
        transform: structuredClone(identityTransform),
        ...(isJson ? { megascansLod: 0, megascansResolution: "auto", megascansNormalConvention: "opengl" } : {}),
      };
      if (srcId) {
        const texFiles = Object.values(asset.pbrMaps).map((p) => ({ path: p, kind: "asset" as const }));
        if (texFiles.length > 0) {
          discoveredForSource = { sourceId: srcId, files: texFiles };
        }
      }
    }

    let nextProject: StoredProject;
    if (replaceNodeId && base.nodes.some((n) => n.id === replaceNodeId)) {
      nextProject = {
        ...base,
        dependencies: deps,
        nodes: base.nodes.map((n) => (n.id === replaceNodeId ? { ...n, data: storedNodeData } : n)),
      };
    } else {
      const newStoredNode: StoredProject["nodes"][number] = {
        id: nodeId,
        type: "stage",
        position,
        data: storedNodeData,
      };
      nextProject = insertToolbarNode({ ...base, dependencies: deps }, newStoredNode, dropTargetArg);
    }

    if (discoveredForSource) {
      nextProject = mergeUsdDiscovery(nextProject, discoveredForSource.sourceId, discoveredForSource.files);
    }
    return { project: nextProject, nodeId };
  }

  function addMegascansAsset(asset: MegascansAssetSummary, posOverride?: { x: number; y: number }, targetOverride: DropTarget = null) {
    const cur = projectRef.current;
    const position = posOverride || { x: 180 + cur.nodes.length * 70, y: 190 + cur.nodes.length * 55 };
    try {
      const { project: updated, nodeId } = buildProjectWithMegascansAsset(cur, asset, position, targetOverride);
      applyProject(updated, `Import ${asset.name}`);
      select(nodeId);
      setStatus(`Added "${asset.name}" (${asset.assetType.toUpperCase()}) linked from ${asset.folderPath}.`);
    } catch (error) {
      reportStatus(String(error));
    }
  }

  useEffect(() => {
    if (!isTauri()) return;
    let unlistenReady: (() => void) | null = null;
    let unlistenErr: (() => void) | null = null;
    let unlistenSocket: (() => void) | null = null;
    let disposed = false;

    void import("@tauri-apps/api/event").then(async ({ listen }) => {
      const u1 = await listen<{ dropId: string; asset: MegascansAssetSummary }>("fab-download-ready", (evt) => {
        const { dropId, asset } = evt.payload;
        const matches = Object.entries(pendingFabDropsRef.current).filter(([, pending]) =>
          (asset.listingUid && pending.url.includes(asset.listingUid)) || pending.title && asset.name.toLowerCase().replace(/[^a-z0-9]/g,"") === pending.title.toLowerCase().replace(/[^a-z0-9]/g,""));
        const matchedId = pendingFabDropsRef.current[dropId] ? dropId : matches.length === 1 ? matches[0][0] : dropId;
        const pending = pendingFabDropsRef.current[matchedId];
        if (pending) void invoke("cancel_fab_drop_watch", { dropId: matchedId });
        setPendingFabDrops((prev) => {
          const copy = { ...prev };
          delete copy[matchedId];
          return copy;
        });
        const cur = projectRef.current;
        const existingNode = pending ? cur.nodes.find((n) => n.id === pending.nodeId) : undefined;
        const pos = existingNode?.position || { x: 220, y: 200 };
        const { project: updated, nodeId } = buildProjectWithMegascansAsset(
          cur,
          asset,
          pos,
          null,
          existingNode?.id,
        );
        applyProject(updated, `Import Fab asset ${asset.name}`);
        select(nodeId);
        setStatus(`Fab download complete: "${asset.name}" unpacked into central library (${asset.folderPath}).`);
      });
      const u2 = await listen<{ dropId: string; error: string }>("fab-download-error", (evt) => {
        const { dropId, error } = evt.payload;
        setPendingFabDrops((prev) => {
          const copy = { ...prev };
          delete copy[dropId];
          return copy;
        });
        reportStatus(error);
      });
      const u3 = await listen<MegascansAssetSummary[]>("megascans-socket-import", (evt) => {
        const incomingAssets = evt.payload || [];
        if (!incomingAssets.length) return;
        let cur = projectRef.current;
        let lastId = "";
        incomingAssets.forEach((asset, idx) => {
          const pos = { x: 200 + (cur.nodes.length + idx) * 75, y: 190 + idx * 65 };
          const res = buildProjectWithMegascansAsset(cur, asset, pos, null);
          cur = res.project;
          lastId = res.nodeId;
        });
        applyProject(cur, `Socket import (${incomingAssets.length} asset${incomingAssets.length > 1 ? "s" : ""})`);
        if (lastId) select(lastId);
        setStatus(`Imported ${incomingAssets.length} asset(s) via Megascans / Fab socket.`);
      });
      if (disposed) {
        u1();
        u2();
        u3();
      } else {
        unlistenReady = u1;
        unlistenErr = u2;
        unlistenSocket = u3;
      }
    });

    return () => {
      disposed = true;
      unlistenReady?.();
      unlistenErr?.();
      unlistenSocket?.();
    };
  }, [reportStatus]);

  function extractDroppedWebUrl(dt: DataTransfer): { url: string; title: string } | null {
    const html = dt.getData("text/html");
    let parsedUrl = "";
    let parsedTitle = "";
    if (html) {
      try {
        const doc = new DOMParser().parseFromString(html, "text/html");
        const anchor = doc.querySelector("a[href]") as HTMLAnchorElement | null;
        if (anchor?.href && /^https?:\/\//i.test(anchor.href)) {
          parsedUrl = anchor.href.trim();
          parsedTitle = (anchor.textContent || anchor.getAttribute("aria-label") || anchor.title || "").trim();
        }
        if (!parsedTitle) {
          const img = doc.querySelector("img[alt]") as HTMLImageElement | null;
          if (img?.alt) parsedTitle = img.alt.trim();
        }
      } catch {
        /* ignore malformed html */
      }
    }
    if (!parsedUrl) {
      const uriList = dt.getData("text/uri-list") || dt.getData("text/plain");
      const firstLine = uriList
        .split(/\r?\n/)
        .map((l) => l.trim())
        .find((l) => l && !l.startsWith("#") && /^https?:\/\//i.test(l));
      if (firstLine) parsedUrl = firstLine;
    }
    if (!parsedUrl) return null;
    return { url: parsedUrl, title: parsedTitle };
  }

  function isExternalAssetDrag(dt: DataTransfer): boolean {
    const types = dt.types;
    return (
      types.includes("application/orchflow-node") ||
      types.includes("application/orchflow-megascans") ||
      types.includes("Files") ||
      types.includes("text/uri-list") ||
      types.includes("text/html")
    );
  }

  function dropNode(event:React.DragEvent) {
    const dt = event.dataTransfer;
    const operation = dt.getData("application/orchflow-node");
    if (toolbarOperations.includes(operation)) {
      event.preventDefault();setDropTarget(null);
      const position=flowInstance.current?.screenToFlowPosition({x:event.clientX,y:event.clientY});
      if(!position)return;
      const id=crypto.randomUUID();
      const label=operation === "materialx"?"MaterialX":operation === "material_bind"?"Material Bind":operation === "light"?"Light":operation === "hdri"?"HDRI":operation === "postfx"?"PostFX":operation === "hair"?"Hair":operation === "cache_edit"?"Cache Sculptor":operation === "character"?"Character":operation === "camera"?"Camera":operation === "metahuman"?"MetaHuman":operation === "blender"?"Blender":operation === "assetOverride"?"Asset Override":operation === "source"?"Source stage":operation[0].toUpperCase()+operation.slice(1);
      const defaultCamera = { yaw: 35, pitch: 20, zoom: 1, panX: 0, panY: 0, fov: 45, dofEnabled: false, focusDistance: 5, fStop: 5.6 };
      const initialCamera = { ...defaultCamera, ...(projectRef.current.viewport?.camera ?? {}) };
      const added=storeProject([{...seed[0],id,position,data:{...seed[0].data,label,operation,transform:structuredClone(identityTransform),...(operation === "application" ? defaultApplicationFields() : {}),...(operation === "postfx" ? { postFx: structuredClone(defaultPostFx) } : {}),...(operation === "cache_edit" ? { cacheEdit: structuredClone(defaultCacheEditSettings) } : {}),...(operation === "character" ? { character: structuredClone(defaultCharacterSettings) } : {}),...(operation === "camera" ? { cameraViews: [{ id: crypto.randomUUID(), name: "Camera 1", camera: initialCamera }] } : {})}}],[],[]).nodes[0];
      try {
        const target=targetAt(event.target);
        applyProject(insertToolbarNode(projectRef.current,added,target),"Insert node");select(id);
        setStatus(target?.kind === "edge"?"Inserted node into edge and shifted downstream nodes.":target?"Connected node and made space for the new branch.":"Added node at drop location.");
      }catch(error){reportStatus(String(error));}
      return;
    }

    // 1. Card dragged from Megascans / Fab Library panel
    const msPayload = dt.getData("application/orchflow-megascans");
    if (msPayload) {
      event.preventDefault();
      setDropTarget(null);
      const position = flowInstance.current?.screenToFlowPosition({ x: event.clientX, y: event.clientY });
      if (!position) return;
      try {
        const asset = JSON.parse(msPayload) as MegascansAssetSummary;
        const target = targetAt(event.target);
        addMegascansAsset(asset, position, target);
      } catch (error) {
        reportStatus(String(error));
      }
      return;
    }

    // 2. Local file(s) dragged from browser Downloads bar or Explorer
    if (dt.files && dt.files.length > 0 && isTauri()) {
      event.preventDefault();
      setDropTarget(null);
      const basePos = flowInstance.current?.screenToFlowPosition({ x: event.clientX, y: event.clientY });
      if (!basePos) return;
      const target = targetAt(event.target);
      const droppedFiles = Array.from(dt.files).map((f) => ({ name: f.name, size: f.size }));
      const projDir = projectPathRef.current
        ? projectPathRef.current.replace(/[\\/][^\\/]+$/, "")
        : null;
      void run(async () => {
        for (let i = 0; i < droppedFiles.length; i++) {
          const item = droppedFiles[i];
          setOperationProgress(`Importing ${item.name}…`);
          const summary = await invoke<MegascansAssetSummary>("ingest_dropped_local_file", {
            fileName: item.name,
            fileSize: item.size,
            projectDir: projDir,
          });
          addMegascansAsset(
            summary,
            { x: basePos.x + i * 70, y: basePos.y + i * 55 },
            i === 0 ? target : null,
          );
        }
      });
      return;
    }

    // 3. Literal webpage drag (e.g. fab.com listing card or direct .glb/.usdz/.hdr URL)
    const webDrop = extractDroppedWebUrl(dt);
    if (webDrop && isTauri()) {
      event.preventDefault();
      setDropTarget(null);
      const position = flowInstance.current?.screenToFlowPosition({ x: event.clientX, y: event.clientY });
      if (!position) return;
      const target = targetAt(event.target);
      const dropId = crypto.randomUUID();
      void (async () => {
        try {
          setStatus(`Checking central library for "${webDrop.title || webDrop.url}"…`);
          const outcome = await invoke<FabDropOutcome>("resolve_or_watch_fab_drop", {
            url: webDrop.url,
            titleHint: webDrop.title,
            dropId,
          });
          if (outcome.status === "ready" && outcome.asset) {
            addMegascansAsset(outcome.asset, position, target);
            setStatus(outcome.message);
            return;
          }
          // Place pending node right at drop location and wait for browser download
          const placeholderId = crypto.randomUUID();
          const placeholderNode = storeProject(
            [
              {
                ...seed[0],
                id: placeholderId,
                position,
                data: {
                  ...seed[0].data,
                  label: `⏳ ${outcome.title} (Download in Fab)`,
                  operation: "source",
                  transform: structuredClone(identityTransform),
                },
              },
            ],
            [],
            [],
          ).nodes[0];
          applyProject(
            insertToolbarNode(projectRef.current, placeholderNode, target),
            `Wait for Fab asset ${outcome.title}`,
          );
          select(placeholderId);
          setPendingFabDrops((prev) => ({
            ...prev,
            [dropId]: { nodeId: placeholderId, url: outcome.url, title: outcome.title },
          }));
          setStatus(outcome.message);
        } catch (error) {
          reportStatus(String(error));
        }
      })();
    }
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
            operation === "materialx" ? "MaterialX" : operation === "material_bind" ? "Material Bind" : operation === "light" ? "Light" : operation === "hdri" ? "HDRI" : operation === "postfx" ? "PostFX" : operation === "hair" ? "Hair" : operation === "cache_edit" ? "Cache Sculptor" : operation === "character" ? "Character" : operation === "camera" ? "Camera" : operation === "metahuman" ? "MetaHuman" : operation === "blender" ? "Blender" : operation === "source"
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
          ...(operation === "application" ? defaultApplicationFields() : {}),
          ...(operation === "postfx" ? { postFx: structuredClone(defaultPostFx) } : {}),
          ...(operation === "cache_edit" ? { cacheEdit: structuredClone(defaultCacheEditSettings) } : {}),
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

  function duplicateNodeById(targetId: string) {
    const current = projectRef.current;
    const expanded = expandProject(current);
    const sourceNode = expanded.nodes.find((n) => n.id === targetId);
    if (!sourceNode) return;
    const newId = crypto.randomUUID();
    const clonedData = structuredClone(sourceNode.data);
    clonedData.label = `${clonedData.label} (copy)`;
    const duplicated: StageNode = {
      ...structuredClone(sourceNode),
      id: newId,
      selected: false,
      position: {
        x: sourceNode.position.x + 40,
        y: sourceNode.position.y + 40,
      },
      data: clonedData,
    };
    const nextNodes = [...expanded.nodes, duplicated];
    applyProject(
      { ...current, ...storeProject(nextNodes, expanded.edges, current.dependencies) },
      "Duplicate node",
    );
    select(newId);
    setInspection(null);
  }
  function deleteNodeById(targetId: string) {
    const current = projectRef.current;
    const expanded = expandProject(current);
    const nextNodes = expanded.nodes.filter((n) => n.id !== targetId);
    const ids = new Set(nextNodes.map((n) => n.id));
    const nextEdges = expanded.edges.filter((e) => ids.has(e.source) && ids.has(e.target));
    const nextPinned =
      current.viewport?.pinnedNodeId === targetId
        ? null
        : current.viewport?.pinnedNodeId ?? null;
    applyProject(
      {
        ...current,
        ...storeProject(nextNodes, nextEdges, current.dependencies),
        viewport: current.viewport ? { ...current.viewport, pinnedNodeId: nextPinned } : current.viewport,
      },
      "Delete node",
    );
    if (selected === targetId) {
      select(nextNodes[0]?.id || "");
    }
    setInspection(null);
  }
  function changeNodeFunction(targetId: string, operation: string) {
    const current = projectRef.current;
    const targetNode = current.nodes.find((n) => n.id === targetId);
    if (!targetNode || targetNode.data.operation === operation) return;
    const patch: Partial<Data> = {
      operation,
      ...(operation === "application" && !targetNode.data.applicationProfileId
        ? defaultApplicationFields()
        : {}),
    };
    applyProject(editNodeData(current, targetId, patch), "Change node function");
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
  function rescanUsdDependencies(forceAll = true): Promise<void> {
    if (scanPromise.current) return scanPromise.current;
    const task = (async () => {
      const initial = projectRef.current;
      const rootIds = new Set<string>();
      for (const n of initial.nodes) {
        if (n.data.inputDependencyId) rootIds.add(n.data.inputDependencyId);
        if (n.data.publishedDependencyId) rootIds.add(n.data.publishedDependencyId);
      }
      for (const dependency of initial.dependencies) {
        if (
          !dependency.usdReferences?.length &&
          /\.(usd|usda|usdc|usdz|json|fbx|gltf|glb|obj|goz)$/i.test(dependency.path)
        )
          rootIds.add(dependency.id);
      }
      const sources = Array.from(rootIds)
        .map((id) => initial.dependencies.find((d) => d.id === id))
        .filter((d): d is Dependency => {
          if (!d || !/\.(usd|usda|usdc|usdz|json|fbx|gltf|glb|obj|goz)$/i.test(d.path)) return false;
          if (!forceAll && d.usdReferences && d.usdReferences.length > 0) return false;
          return true;
        });
      let scanned = 0;
      const failures: string[] = [];
      const discoveries: { sourceId: string; sourcePath: string; files: { path: string; kind: "usd" | "asset" }[] }[] = [];
      for (const source of sources) {
        try {
          const result = await invoke<{
            files: { path: string; kind: "usd" | "asset" }[];
          }>("usd_request", {
            request: { operation: "dependencies", path: source.path },
          });
          discoveries.push({ sourceId: source.id, sourcePath: source.path, files: result.files });
          scanned++;
        } catch (error) {
          failures.push(`${source.path}: ${error}`);
        }
      }
      if (discoveries.length > 0) {
        let updated = projectRef.current;
        let changed = false;
        for (const item of discoveries) {
          if (
            updated.dependencies.some(
              (d) => d.id === item.sourceId && fileKey(d.path) === fileKey(item.sourcePath),
            )
          ) {
            updated = mergeUsdDiscovery(updated, item.sourceId, item.files);
            changed = true;
          }
        }
        if (changed) {
          applyProject(updated, "Discover dependencies", null, false);
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
        {
          name: "Scene / Megascans / Fab / Volume source",
          extensions: ["usd", "usda", "usdc", "usdz", "abc", "fbx", "glb", "gltf", "json", "obj", "vdb", "goz", "sublayer", "orchflow"],
        },
        { name: "USD stage", extensions: ["usd", "usda", "usdc", "usdz"] },
        { name: "Megascans / Fab (JSON / GLB / FBX)", extensions: ["json", "glb", "gltf", "fbx"] },
        { name: "Sublayer project", extensions: ["sublayer", "orchflow"] },
        { name: "Alembic", extensions: ["abc"] },
        { name: "Wavefront OBJ", extensions: ["obj"] },
        { name: "OpenVDB", extensions: ["vdb"] },
        { name: "ZBrush GoZ", extensions: ["goz"] },
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
          : "Linked the original source file.",
      );
    }
  }

  async function convertSourceToUsd() {
    if (!node || !node.data.path.trim()) return;
    const { save } = await import("@tauri-apps/plugin-dialog");
    const defaultName =
      node.data.path.replace(/\.(abc|obj|vdb|goz|fbx|gltf|glb|json|sublayer|orchflow)$/i, "") + ".usdc";
    const destination = await save({
      defaultPath: defaultName,
      filters: [
        { name: "USD stage", extensions: ["usdc", "usda", "usd"] },
      ],
    });
    if (!destination) return;
    const result = await invoke<{
      destination: string;
      primCount: number;
      sourceFormat: string;
    }>("usd_request", {
      request: {
        operation: "convert_source_to_usd",
        path: node.data.path,
        destination,
      },
    });
    setStatus(
      `Converted ${result.sourceFormat.toUpperCase()} (${result.primCount} prims) to ${result.destination}.`,
    );
  }
  async function openProjectAtPath(path: string) {
    setOperationProgress("Loading project...");
    const p = await invoke<StoredProject>("load_project", { path });
    history.current.clear();historyChanged(v=>v+1);setMaterialXEditorId(null);
    applyProject(p,"Open project",null,false);
    select(p.nodes[0]?.id || "");
    setInspection(null);
    setOperationProgress("Discovering USD dependencies...");
    await rescanUsdDependencies(false);
    projectPathRef.current = path;
    recordLastProject(path);
    setProjectName(path.split(/[\\/]/).pop()?.replace(/\.(sublayer|orchflow|json)$/i,"") || "Untitled");
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
    void (async () => {
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
              nodes: current.nodes.map((n) => {
                const d = n.data;
                const op = d.operation;
                let settings: Record<string, unknown> | undefined;
                if (op === "light") settings = { lightOverrides: d.lightOverrides ?? [], scale: d.scale ?? 1, lightIntensityScale: d.lightIntensityScale ?? 1 };
                else if (op === "hdri") settings = { hdri: d.hdri ?? null };
                else if (op === "postfx") settings = { postFx: d.postFx ?? null };
                else if (op === "materialx") settings = d.materialXRules ? { materialXRules: d.materialXRules } : { materialX: d.materialX ?? null };
                else if (op === "transform") settings = { transform: d.transform ?? null, transformTargets: d.transformTargets ?? null };
                else if (op === "pose") settings = { pose: d.pose ?? null };
                else if (op === "instancer") settings = { instancer: d.instancer ?? defaultInstancer };
                else if (op === "delete") settings = { deletePaths: d.deletePaths ?? [] };
                else if (op === "camera") settings = { camera: d.camera ?? null, cameraViews: d.cameraViews ?? [] };
                else if (op === "blender") settings = { blenderScene: d.blenderScene ?? null, blenderSelections: d.blenderSelections ?? [], blenderMaterialMode: d.blenderMaterialMode ?? null, blenderExportSkeletons: d.blenderExportSkeletons ?? null, scale: d.scale ?? 1 };
                else if (op === "hair" && d.hair) {
                  const { groomDeltas: _g, ...hairSummary } = d.hair as Record<string, unknown>;
                  settings = { hair: hairSummary };
                }
                return {
                  id: n.id,
                  operation: op,
                  label: d.label,
                  ...(settings ? { settings } : {}),
                };
              }),
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
    })();
    return () => {
      disposed = true;
      unlisten?.();
    };
  }, []);
  async function project(saveProject: boolean) {
    const { open, save } = await import("@tauri-apps/plugin-dialog");
    const path = saveProject
      ? await save({ filters: [{ name: "Sublayer", extensions: ["sublayer"] }] })
      : await open({
          filters: [
            { name: "Sublayer", extensions: ["sublayer", "orchflow"] },
            { name: "Legacy OrchFlow", extensions: ["orchflow"] },
          ],
          multiple: false,
        });
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
      setProjectName(path.split(/[\\/]/).pop()?.replace(/\.(sublayer|orchflow|json)$/i,"") || "Untitled");
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
            Sublayer<small>USD WORKSPACE</small>
          </div>
        </div>
        <nav>
          <button className="nav-action" disabled={!history.current.past.length || busy || activeTab!=="canvas"} title="Undo project edit (Ctrl+Z)" onClick={()=>travel("undo")}>Undo</button>
          <button className="nav-action" disabled={!history.current.future.length || busy || activeTab!=="canvas"} title="Redo project edit (Ctrl+Shift+Z / Ctrl+Y)" onClick={()=>travel("redo")}>Redo</button>
          <span className="nav-divider" aria-hidden="true" />
          <button className={"nav-tab" + (activeTab==="canvas"?" chosen":"")} onClick={()=>setActiveTab("canvas")}>Canvas</button>
          <button className={"nav-tab" + (activeTab==="applications"?" chosen":"")} onClick={()=>setActiveTab("applications")}>Applications</button>
          <button
            className={"nav-action" + (megascansOpen ? " chosen" : "")}
            title="Browse I:\EpicGames\Megascans or import Fab assets"
            onClick={() => {
              setActiveTab("canvas");
              setMegascansOpen((v) => !v);
            }}
          >
            ◈ Megascans / Fab
          </button>
          <button className="nav-action" disabled={!isTauri()} onClick={() => void invoke("open_fab_browser").catch(e => reportStatus(String(e)))}>Fab Browser</button>
          {backendOutdated && (
            <button
              className="nav-action"
              onClick={() =>
                run(async () => {
                  await navigator.clipboard.writeText(
                    JSON.stringify(projectModel, null, 2),
                  );
                  setStatus(
                    "Project JSON copied. Paste it into a .sublayer file before restarting.",
                  );
                })
              }
            >
              Copy project JSON
            </button>
          )}
          <span className="nav-divider" aria-hidden="true" />
          {toolbarOperations.map(operation=><button key={operation} className="nav-draggable" draggable title="Click to add, or drag onto an edge or node to connect and make space"
            onDragStart={e=>{setGraphModeRequest(n=>n+1);e.dataTransfer.setData("application/orchflow-node",operation);e.dataTransfer.effectAllowed="copy";}}
            onDragEnd={()=>setDropTarget(null)} onClick={()=>addNode(operation)}>
            <span className="drag-grip" aria-hidden="true" />
            ＋ {operation === "materialx"?"MaterialX":operation === "material_bind"?"Material Bind":operation === "light"?"Light":operation === "hdri"?"HDRI":operation === "postfx"?"PostFX":operation === "hair"?"Hair":operation === "cache_edit"?"Cache Sculptor":operation === "character"?"Character":operation === "camera"?"Camera":operation === "metahuman"?"MetaHuman":operation === "blender"?"Blender":operation === "assetOverride"?"Asset Override":operation[0].toUpperCase()+operation.slice(1)}
          </button>)}
          <span className="nav-divider" aria-hidden="true" />
          <button className="nav-action nav-action-danger" disabled={busy || (!nodes.length && !dependencies.length)}
            title="Clear the scene and start a new project" onClick={()=>setClearScenePrompt(true)}>
            Clear scene
          </button>
          <button className="nav-action" disabled={busy} onClick={() => run(() => project(false))}>
            Open project
          </button>
          <button
            className="nav-action"
            disabled={busy || !lastProjectPath}
            title={lastProjectPath ? `Reopen ${lastProjectPath}` : "No recent project"}
            onClick={() => lastProjectPath && run(() => openProjectAtPath(lastProjectPath))}
          >
            Last project
          </button>
          <button className="nav-action" disabled={busy} onClick={() => run(() => project(true))}>
            Save project
          </button>
        </nav>
        <div className="header-right">
          <select
            className="theme-select"
            aria-label="Workspace theme"
            title="Workspace theme"
            value={themeId}
            onChange={(e) => setThemeId(e.target.value)}
          >
            {Object.entries(THEMES).map(([id, t]) => (
              <option key={id} value={id}>
                {t.label}
              </option>
            ))}
          </select>
          <small className="badge">HYDRA / 02</small>
        </div>
      </header>
      {operationProgress&&<div className="project-progress"><OperationProgress label={operationProgress}/></div>}
      {status.includes("Warning:") && (
        <div className="project-warning" role="alert">
          <span>{status}</span>
          <button onClick={() => setStatus("Ready")}>Dismiss</button>
        </div>
      )}
      {megascansOpen && activeTab === "canvas" && (
        <Suspense fallback={null}>
          <MegascansPanel
            onAddAsset={(asset) => addMegascansAsset(asset)}
            onDragStartCanvasMode={() => setGraphModeRequest((n) => n + 1)}
            onDragEndCanvas={() => setDropTarget(null)}
            onStatus={reportStatus}
            onSettings={() => { setMegascansOpen(false); setSettingsTarget(v => ({ id: "megascans-library", visit: v.visit + 1 })); setActiveTab("applications"); }}
            onClose={() => setMegascansOpen(false)}
          />
        </Suspense>
      )}
      {materialXEditorId && projectModel.nodes.some(n=>n.id===materialXEditorId && n.data.operation==="materialx") && <Suspense fallback={null}><MaterialXPanel key={materialXEditorId} project={projectModel} nodeId={materialXEditorId} pendingPrimDrop={materialXPrimDrop} onProjectChange={applyProject} onClose={()=>setMaterialXEditorId(null)}/></Suspense>}
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
            <Suspense fallback={<p className="hint">Loading dependencies...</p>}><DependencyTable key={overrideNodeId} nodeId={overrideNodeId} project={projectModel} onChange={applyProject} onStatus={reportStatus} onRescan={rescanUsdDependencies} onDiscoverFiles={(files) => applyProject(mergeDiscoveredInputs(projectRef.current, files), "Discover dependencies", null, false)}/></Suspense>
          </div>
        </div>
      )}
        <div className="application-tab-host" hidden={activeTab!=="applications"}><Suspense fallback={null}>{activeTab==="applications" && <ApplicationSettings focusProfileId={settingsTarget.id} focusVisit={settingsTarget.visit}/>}</Suspense></div>
        <section className="workspace" style={activeTab==="applications"?{display:"none"}:undefined}>
          <SceneOverview path={target.path} graph={target.graph} selected={selectedPrim} onSelect={path=>setPrimSelection({key:sceneKey,path})} onAddToMaterialX={materialXEditorId && projectModel.nodes.some(n=>n.id===materialXEditorId && n.data.operation==="materialx") ? (primPath)=>setMaterialXPrimDrop(c=>({primPath,seq:(c?.seq ?? 0)+1})) : undefined} isolatedPrim={isolatedPrim} hiddenPrims={hiddenPrims} onHidePrim={p=>setHiddenPrims(prev=>prev.includes(p)?prev:[...prev,p])} onShowPrim={p=>setHiddenPrims(prev=>prev.filter(x=>x!==p))} onIsolatePrim={p=>setIsolatedPrim(cur=>cur===p?null:p)} onShowAll={()=>{setHiddenPrims([]);setIsolatedPrim(null);}} onStatus={reportStatus}/>
          <div className={"canvas " + (sceneMode ? "scene-mode" : "")} onDrop={dropNode} onDragOver={e=>{
            if(isExternalAssetDrag(e.dataTransfer)){e.preventDefault();e.dataTransfer.dropEffect="copy";const target=targetAt(e.target);setDropTarget(previous=>JSON.stringify(previous)===JSON.stringify(target)?previous:target);}
          }} onDragLeave={e=>{if(!e.currentTarget.contains(e.relatedTarget as Node))setDropTarget(null);}}>
            <NativeViewport key={currentProjectSession}
              onViewportError={(err) => { (bridgeStateRef.current as Record<string, unknown>).viewportError = err || null; }}
              cameraKey={comparison ? previewId : undefined}
              comparison={comparison}
              selectedPrim={selectedPrim}
              selectedNodeId={node?.id ?? null}
              path={target.path}
              graph={target.graph}
              emptyTitle={target.title}
              emptyMessage={target.message}
              previewControls={previewNode?.data.operation === "application" ? (
                <div className="application-preview-controls" role="group" aria-label="Application preview mode">
                  <button
                    type="button"
                    className={activePreviewMode !== "published" ? "active" : undefined}
                    aria-pressed={activePreviewMode !== "published"}
                    onClick={() => setApplicationPreviews(p => ({ ...p, [previewId]: "handoff" }))}
                  >
                    Handoff USD
                  </button>
                  <button
                    type="button"
                    className={activePreviewMode === "published" ? "active" : undefined}
                    aria-pressed={activePreviewMode === "published"}
                    onClick={() => setApplicationPreviews(p => ({ ...p, [previewId]: "published" }))}
                  >
                    Published USD
                  </button>
                </div>
              ) : undefined}
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
              onTimeChange={setViewportFrame}
              onCameraTransformChange={(t) => {
                viewportCameraTransformRef.current = t;
              }}
              seekFrameRequest={seekFrameRequest}
              onCommitHairGroom={(nodeId, groomDeltas) => {
                const targetNode = projectRef.current.nodes.find((n) => n.id === nodeId);
                if (!targetNode || targetNode.data.operation !== "hair") return;
                const currentHair = targetNode.data.hair ?? defaultHairSettings;
                const nextHair = { ...currentHair };
                if (Object.keys(groomDeltas).length > 0) {
                  nextHair.groomDeltas = groomDeltas;
                } else {
                  delete nextHair.groomDeltas;
                }
                applyProject(
                  editNodeData(projectRef.current, nodeId, { hair: nextHair }),
                  Object.keys(groomDeltas).length > 0 ? "Hair groom" : "Reset hair groom",
                );
              }}
              onCommitCacheEdit={(nodeId, layerId, deltas, masks) => {
                const targetNode = projectRef.current.nodes.find((n) => n.id === nodeId);
                if (!targetNode || targetNode.data.operation !== "cache_edit") return;
                const current = targetNode.data.cacheEdit ?? structuredClone(defaultCacheEditSettings);
                const curLayers = current.layers?.length ? current.layers : structuredClone(defaultCacheEditSettings.layers);
                const curFrame = Math.round(viewportFrameRef.current);
                const active = curLayers.find((l) => l.id === layerId) || curLayers[0];
                const hasExistingDeltas = Boolean(active?.deltas && Object.keys(active.deltas).length > 0);
                const needsNewFrameLayer =
                  Boolean(active) &&
                  active.mode !== "allFrames" &&
                  Math.round(active.centerFrame) !== curFrame &&
                  hasExistingDeltas &&
                  Object.keys(deltas).length > 0;

                let nextActiveId = layerId || current.activeLayerId;
                let layers = curLayers;

                if (needsNewFrameLayer) {
                  const existingAtFrame = curLayers.find(
                    (l) => l.mode !== "allFrames" && Math.round(l.centerFrame) === curFrame,
                  );
                  if (existingAtFrame) {
                    nextActiveId = existingAtFrame.id;
                    layers = curLayers.map((l) =>
                      l.id === existingAtFrame.id ? { ...l, deltas } : l,
                    );
                  } else {
                    const newLayer = {
                      ...active,
                      id: `layer-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 6)}`,
                      name: `Frame ${curFrame}`,
                      centerFrame: curFrame,
                      deltas,
                    };
                    nextActiveId = newLayer.id;
                    layers = [...curLayers, newLayer];
                  }
                } else {
                  layers = curLayers.map((l) => {
                    if (l.id !== (active?.id ?? layerId)) return l;
                    const nextLayer = { ...l };
                    if (nextLayer.mode !== "allFrames" && !hasExistingDeltas) {
                      nextLayer.centerFrame = curFrame;
                    }
                    if (Object.keys(deltas).length > 0) {
                      nextLayer.deltas = deltas;
                    } else {
                      delete nextLayer.deltas;
                    }
                    return nextLayer;
                  });
                }

                const nextCacheEdit = {
                  ...current,
                  activeLayerId: nextActiveId,
                  layers,
                };
                if (Object.keys(masks).length > 0) {
                  nextCacheEdit.maskWeights = masks;
                } else {
                  delete nextCacheEdit.maskWeights;
                }
                applyProject(
                  editNodeData(projectRef.current, nodeId, { cacheEdit: nextCacheEdit }),
                  "Cache sculpt stroke",
                );
              }}
              poseEditor={poseEditing && node?.id===poseEditing.nodeId && node.data.pose?.entries.find(e=>e.id===poseEditing.entryId)?.joints ? {
                ...poseEditing,
                joints:node.data.pose.entries.find(e=>e.id===poseEditing.entryId)!.joints!,
                onSelectJoint:jointIndex=>setPoseEditing({...poseEditing,jointIndex}),
                onRotateJoint:(jointIndex,axis,value)=>{
                  const current=projectRef.current.nodes.find(n=>n.id===poseEditing.nodeId)?.data.pose;
                  if(!current)return;
                  const pose={...current,entries:current.entries.map(entry=>entry.id===poseEditing.entryId?{...entry,joints:entry.joints?.map((joint,index)=>{
                    if(index!==jointIndex)return joint;const rotationOffset=[...joint.rotationOffset] as [number,number,number];rotationOffset[axis]=value;return {...joint,rotationOffset};
                  })}:entry)};
                  applyProject(editNodeData(projectRef.current,poseEditing.nodeId,{pose}),"Rotate pose joint",`pose-drag:${poseEditing.nodeId}:${poseEditing.entryId}:${jointIndex}`);
                },
                onCommit:()=>history.current.endGroup(),
              }:null}
              instancerEditor={node?.data.operation === "instancer" ? {
                nodeId: node.id,
                settings: node.data.instancer || defaultInstancer,
                onPreviewNode: () => pin(node.id),
                enabled: instancerEditing === node.id,
                selectedId: instancerSelection?.nodeId === node.id ? instancerSelection.id : null,
                onSelect: id => setInstancerSelection({nodeId: node.id, id}),
                onChange: (instancer: InstancerSettings, group?: string) => applyProject(editNodeData(projectRef.current, node.id, {instancer}), "Edit instances", group || null),
                onCommit: () => history.current.endGroup(),
              } : null}
              transformGizmo={(() => {
                if (node?.data.operation === "transform") {
                  return {
                    nodeId: node.id,
                    transform: node.data.transform || identityTransform,
                    onChange: (nextTransform) =>
                      applyProject(
                        editNodeData(projectRef.current, node.id, {
                          transform: nextTransform,
                        }),
                        "Transform gizmo",
                        "transform-gizmo",
                      ),
                    onCommit: () => history.current.endGroup(),
                  };
                }
                if (activeGroomGizmo) {
                  const charNode = projectModel.nodes.find(
                    (n) => n.id === activeGroomGizmo.nodeId && n.data.operation === "character",
                  );
                  const groomRule = charNode?.data.character?.headGrooms?.find(
                    (hg) => hg.id === activeGroomGizmo.groomId && hg.enabled && hg.attachMode === "joint",
                  );
                  if (charNode && groomRule) {
                    return {
                      nodeId: `groom:${charNode.id}:${groomRule.id}`,
                      transform: {
                        translation: groomRule.offsetTranslation,
                        rotation: groomRule.offsetRotation,
                        scale: groomRule.offsetScale,
                      },
                      onChange: (nextTransform) => {
                        const curChar = projectRef.current.nodes.find((n) => n.id === charNode.id)?.data.character;
                        if (!curChar) return;
                        const prevScale = groomRule.offsetScale;
                        const sChanged =
                          Math.abs(nextTransform.scale[0] - prevScale[0]) > 1e-6
                            ? nextTransform.scale[0]
                            : Math.abs(nextTransform.scale[1] - prevScale[1]) > 1e-6
                              ? nextTransform.scale[1]
                              : Math.abs(nextTransform.scale[2] - prevScale[2]) > 1e-6
                                ? nextTransform.scale[2]
                                : nextTransform.scale[0];
                        const uniformScale: [number, number, number] = [
                          Math.max(0.0001, sChanged),
                          Math.max(0.0001, sChanged),
                          Math.max(0.0001, sChanged),
                        ];
                        const nextHeadGrooms = (curChar.headGrooms || []).map((hg) =>
                          hg.id === groomRule.id
                            ? {
                                ...hg,
                                offsetTranslation: nextTransform.translation,
                                offsetRotation: nextTransform.rotation,
                                offsetScale: uniformScale,
                              }
                            : hg,
                        );
                        applyProject(
                          editNodeData(projectRef.current, charNode.id, {
                            character: { ...curChar, headGrooms: nextHeadGrooms },
                          }),
                          "Move head groom",
                          `groom-gizmo:${charNode.id}:${groomRule.id}`,
                        );
                      },
                      onCommit: () => history.current.endGroup(),
                    };
                  }
                }
                return null;
              })()}
              isolatedPrim={isolatedPrim}
              onIsolatedPrimChange={setIsolatedPrim}
              hiddenPrims={hiddenPrims}
              onHiddenPrimsChange={setHiddenPrims}
              onSelectPrim={path=>setPrimSelection({key:sceneKey,path})}
              onAddToMaterialX={materialXEditorId && projectModel.nodes.some(n=>n.id===materialXEditorId && n.data.operation==="materialx") ? (primPath)=>setMaterialXPrimDrop(c=>({primPath,seq:(c?.seq ?? 0)+1})) : undefined}
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
                zoomOnDoubleClick={false}
                nodes={dropTarget?.kind === "node" ? nodes.map(n => dropTarget.id === n.id ? { ...n, className: [n.className, "toolbar-drop-target"].filter(Boolean).join(" ") } : n) : nodes}
                edges={dropTarget?.kind === "edge" ? edges.map(e => dropTarget.id === e.id ? { ...e, style: { ...e.style, stroke: "#a4e4bd", strokeWidth: 4 } } : e) : edges}
                defaultEdgeOptions={{interactionWidth:32}}
                nodeTypes={nodeTypes}
                onNodesChange={changes=>{
                  if (changes.every(c => c.type === "select" || c.type === "dimensions" || (c.type === "position" && c.dragging))) {
                    setNodes(ns => applyNodeChanges(changes, ns));
                    return;
                  }
                  const current=projectRef.current,expanded=expandProject(current);
                  const next=applyNodeChanges(changes,expanded.nodes);
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
                  setNodeContextMenu(null);
                  setEdgeContextMenu(null);
                  select(n.id);
                  setInspection(null);
                }}
                onNodeDragStart={(event, n) => {
                  altDragRef.current = event.altKey ? { nodeId: n.id, detached: false } : null;
                }}
                onNodeDrag={(event, n) => {
                  const activeAlt = event.altKey || altDragRef.current?.nodeId === n.id;
                  if (!activeAlt) {
                    if (dropTarget) setDropTarget(null);
                    return;
                  }
                  if (event.altKey && (!altDragRef.current || (altDragRef.current.nodeId === n.id && !altDragRef.current.detached))) {
                    const detached = detachNodeFromEdges(projectRef.current, n.id);
                    if (detached !== projectRef.current) {
                      applyProject(detached, "Detach node", `alt-drag:${n.id}`);
                      setStatus("Detached node and reconnected upstream/downstream links.");
                    }
                    altDragRef.current = { nodeId: n.id, detached: true };
                  }
                  if (event.altKey) {
                    const nextTarget = edgeUnderPointer(event, n.id);
                    setDropTarget((prev) =>
                      JSON.stringify(prev) === JSON.stringify(nextTarget) ? prev : nextTarget,
                    );
                  } else if (dropTarget) {
                    setDropTarget(null);
                  }
                }}
                onNodeDragStop={(event, n) => {
                  const targetEdge = event.altKey
                    ? edgeUnderPointer(event, n.id) || (dropTarget?.kind === "edge" ? dropTarget : null)
                    : dropTarget?.kind === "edge" && altDragRef.current?.nodeId === n.id
                      ? dropTarget
                      : null;
                  setDropTarget(null);
                  if (targetEdge?.kind === "edge" && (event.altKey || altDragRef.current?.nodeId === n.id)) {
                    const inserted = insertExistingNodeOnEdge(projectRef.current, n.id, targetEdge.id);
                    if (inserted !== projectRef.current) {
                      applyProject(inserted, "Insert node into edge");
                      setStatus("Inserted node into edge.");
                    }
                  }
                  altDragRef.current = null;
                }}
                onNodeContextMenu={(event, n) => {
                  event.preventDefault();
                  setEdgeContextMenu(null);
                  select(n.id);
                  setInspection(null);
                  setNodeContextMenu({ nodeId: n.id, x: event.clientX, y: event.clientY });
                }}
                onEdgeContextMenu={(event, edge) => {
                  event.preventDefault();
                  setNodeContextMenu(null);
                  setEdgeContextMenu({ edgeId: edge.id, x: event.clientX, y: event.clientY });
                }}
                onEdgeClick={() => {
                  setNodeContextMenu(null);
                  setEdgeContextMenu(null);
                }}
                onPaneClick={() => {
                  setNodeContextMenu(null);
                  setEdgeContextMenu(null);
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
            {edgeContextMenu && (() => {
              const targetEdge = edges.find((e) => e.id === edgeContextMenu.edgeId);
              if (!targetEdge) return null;
              const srcLabel = nodes.find((n) => n.id === targetEdge.source)?.data.label || targetEdge.source;
              const dstLabel = nodes.find((n) => n.id === targetEdge.target)?.data.label || targetEdge.target;
              return (
                <div
                  className="node-context-backdrop"
                  onClick={() => setEdgeContextMenu(null)}
                  onContextMenu={(e) => {
                    e.preventDefault();
                    setEdgeContextMenu(null);
                  }}
                  onKeyDown={(e) => {
                    if (e.key === "Escape") setEdgeContextMenu(null);
                  }}
                >
                  <div
                    className="node-context-menu"
                    role="menu"
                    aria-label={`Link actions for ${srcLabel} to ${dstLabel}`}
                    style={{
                      left: Math.min(edgeContextMenu.x, window.innerWidth - 220),
                      top: Math.min(edgeContextMenu.y, window.innerHeight - 120),
                    }}
                    onClick={(e) => e.stopPropagation()}
                  >
                    <div className="node-context-header">{srcLabel} → {dstLabel}</div>
                    <button
                      type="button"
                      className="node-context-item danger"
                      role="menuitem"
                      onClick={() => {
                        const current = projectRef.current;
                        applyProject(
                          {
                            ...current,
                            edges: current.edges.filter((e) => e.id !== targetEdge.id),
                          },
                          "Remove link",
                          "delete",
                        );
                        setEdgeContextMenu(null);
                        setStatus(`Removed link (${srcLabel} → ${dstLabel}).`);
                      }}
                    >
                      Remove link
                    </button>
                  </div>
                </div>
              );
            })()}
            {nodeContextMenu && (() => {
              const targetNode = nodes.find((n) => n.id === nodeContextMenu.nodeId);
              if (!targetNode) return null;
              const functionOptions: [string, string][] = [
                ["source", "Source stage"],
                ["blender", "Blender"],
                ["application", "Application"],
                ["transform", "Transform"],
                ["group", "Group"],
                ["assetOverride", "Asset Override"],
                ["materialx", "MaterialX"],
                ["material_bind", "Material Bind"],
                ["light", "Light"],
                ["hdri", "HDRI"],
                ["postfx", "PostFX"],
                ["hair", "Hair"],
                ["cache_edit", "Cache Sculptor"],
                ["character", "Character"],
                ["metahuman", "MetaHuman"],
                ["pose", "Pose"],
                ["camera", "Camera"],
                ["delete", "Delete prims"],
                ["scatter", "Scatter"],
                ["instancer", "Instancer"],
                ["output", "Output"],
              ];
              return (
                <div
                  className="node-context-backdrop"
                  onClick={() => setNodeContextMenu(null)}
                  onContextMenu={(e) => {
                    e.preventDefault();
                    setNodeContextMenu(null);
                  }}
                  onKeyDown={(e) => {
                    if (e.key === "Escape") setNodeContextMenu(null);
                  }}
                >
                  <div
                    className="node-context-menu"
                    role="menu"
                    aria-label={`Actions for ${targetNode.data.label}`}
                    style={{
                      left: Math.min(nodeContextMenu.x, window.innerWidth - 220),
                      top: Math.min(nodeContextMenu.y, window.innerHeight - 280),
                    }}
                    onClick={(e) => e.stopPropagation()}
                  >
                    <div className="node-context-header">{targetNode.data.label}</div>
                    <div className="node-context-submenu-wrap">
                      <button type="button" className="node-context-item" role="menuitem" aria-haspopup="menu">
                        <span>Change function</span>
                        <span className="node-context-arrow">▸</span>
                      </button>
                      <div className="node-context-submenu" role="menu" aria-label="Node function">
                        {functionOptions.map(([op, label]) => (
                          <button
                            key={op}
                            type="button"
                            role="menuitemradio"
                            aria-checked={targetNode.data.operation === op}
                            className={"node-context-item" + (targetNode.data.operation === op ? " active" : "")}
                            onClick={() => {
                              changeNodeFunction(targetNode.id, op);
                              setNodeContextMenu(null);
                            }}
                          >
                            <span>{label}</span>
                            {targetNode.data.operation === op && <span>✓</span>}
                          </button>
                        ))}
                      </div>
                    </div>
                    <button
                      type="button"
                      className="node-context-item"
                      role="menuitem"
                      onClick={() => {
                        duplicateNodeById(targetNode.id);
                        setNodeContextMenu(null);
                      }}
                    >
                      Duplicate node
                    </button>
                    <button
                      type="button"
                      className="node-context-item danger"
                      role="menuitem"
                      onClick={() => {
                        deleteNodeById(targetNode.id);
                        setNodeContextMenu(null);
                      }}
                    >
                      Delete node
                    </button>
                  </div>
                </div>
              );
            })()}
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
                {node.data.operation === "assetOverride" && <button onClick={() => setOverrideNodeId(node.id)}>Edit Asset Overrides… ({node.data.assetOverrides?.length || 0})</button>}
                {node.data.operation === "delete" && <label><span className="field-label">Prim paths<InspectorHint text="One absolute path per line. Deactivates these prims on this branch; original files are unchanged. Instance-proxy children cannot be deleted separately."/></span><textarea value={(node.data.deletePaths || []).join("\n")} onChange={e=>update({deletePaths:e.target.value.split("\n").map(v=>v.trim()).filter(Boolean)})}/></label>}
                {node.data.operation === "instancer" && <InstancerInspector
                  value={node.data.instancer || defaultInstancer}
                  selectedId={instancerSelection?.nodeId === node.id ? instancerSelection.id : null}
                  editing={instancerEditing === node.id}
                  onEditingChange={active => setInstancerEditing(active ? node.id : null)}
                  onSelect={id => setInstancerSelection({nodeId: node.id, id})}
                  onChange={instancer => update({instancer})}
                />}
                {node.data.operation === "scatter" && (
                  <ScatterInspector
                    project={projectModel}
                    nodeId={node.id}
                    value={node.data.scatter}
                    onChange={(scatter) => update({ scatter })}
                  />
                )}
                {node.data.operation === "transform" && (
                  <TransformInspector
                    value={node.data.transform}
                    targets={node.data.transformTargets}
                    selectedPrim={selectedPrim}
                    onTransformChange={(transform) => update({ transform })}
                    onTargetsChange={(transformTargets) => update({ transformTargets })}
                  />
                )}
                {node.data.operation === "group" && (
                  <GroupInspector
                    groupName={node.data.groupName}
                    transform={node.data.transform}
                    onGroupNameChange={(groupName) => update({ groupName })}
                    onTransformChange={(transform) => update({ transform })}
                  />
                )}
                {node.data.operation === "materialx" && <button onClick={()=>setMaterialXEditorId(node.id)}>Edit MaterialX shading rules...</button>}
                {node.data.operation === "output" && (
                  <p className="field-label">Combined output<InspectorHint text="Combines incoming stages in memory. Select this node to preview the result; it does not write a USD file."/></p>
                )}
                {["source", "application"].includes(node.data.operation) && (
                  <>
                    {node.data.operation==="source"&&<>
                      {(() => {
                        const pendingEntry = Object.entries(pendingFabDrops).find(([, v]) => v.nodeId === node.id);
                        if (!pendingEntry) return null;
                        const [dropId, info] = pendingEntry;
                        return (
                          <div className="fab-pending-banner">
                            <strong>Waiting for Fab browser download…</strong>
                            <p className="hint">
                              Click <b>Download</b> on <code>{info.title}</code> in your browser. Sublayer is watching your Downloads folder and will unpack it into your central Megascans/Fab library automatically.
                            </p>
                            <button
                              type="button"
                              onClick={() => {
                                void invoke("cancel_fab_drop_watch", { dropId });
                                setPendingFabDrops((prev) => {
                                  const next = { ...prev };
                                  delete next[dropId];
                                  return next;
                                });
                                setStatus(`Cancelled Fab download watch for "${info.title}".`);
                              }}
                            >
                              Cancel watch
                            </button>
                          </div>
                        );
                      })()}
                      <label>Source file (USD / FBX / GLB / ABC / OBJ / Megascans JSON / VDB / GoZ)<input placeholder="I:\\EpicGames\\Megascans\\...\\asset.json" value={node.data.path} onChange={e=>update({path:e.target.value})}/></label>
                      <div style={{ display: "flex", gap: "6px" }}>
                        <button style={{ flex: 1 }} disabled={busy || !isTauri()} onClick={()=>run(browse)}>Browse source...</button>
                        <button type="button" onClick={() => setMegascansOpen(true)}>Megascans / Fab...</button>
                      </div>
                      {/\.(abc|obj|vdb|goz|fbx|gltf|glb|json|sublayer|orchflow)$/i.test(node.data.path.trim()) && (
                        <button disabled={busy || !isTauri()} onClick={()=>run(convertSourceToUsd)}>Convert to USD...</button>
                      )}
                      <table className="source-options-table">
                        <tbody>
                          {/\.json$/i.test(node.data.path.trim()) && (
                            <>
                              <tr>
                                <td>Megascans LOD</td>
                                <td>
                                  <select
                                    aria-label="Megascans LOD"
                                    value={node.data.megascansLod ?? 0}
                                    onChange={(e) => update({ megascansLod: Number(e.target.value) })}
                                  >
                                    {[0, 1, 2, 3, 4, 5].map((lod) => (
                                      <option key={lod} value={lod}>
                                        {lod === 0 ? "LOD 0 (Highest)" : `LOD ${lod}`}
                                      </option>
                                    ))}
                                  </select>
                                </td>
                              </tr>
                              <tr>
                                <td>Texture resolution</td>
                                <td>
                                  <select
                                    aria-label="Texture resolution"
                                    value={node.data.megascansResolution || "auto"}
                                    onChange={(e) => update({ megascansResolution: e.target.value })}
                                  >
                                    <option value="auto">Auto (Highest)</option>
                                    <option value="8K">8K</option>
                                    <option value="4K">4K</option>
                                    <option value="2K">2K</option>
                                    <option value="1K">1K</option>
                                  </select>
                                </td>
                              </tr>
                              <tr>
                                <td>Normal convention</td>
                                <td>
                                  <select
                                    aria-label="Normal convention"
                                    value={node.data.megascansNormalConvention || "opengl"}
                                    onChange={(e) => update({ megascansNormalConvention: e.target.value })}
                                  >
                                    <option value="opengl">OpenGL (+Y)</option>
                                    <option value="directx">DirectX (-Y flip)</option>
                                  </select>
                                </td>
                              </tr>
                            </>
                          )}
                          <tr>
                            <td>
                              <span className="field-label">
                                Native instancing
                                <InspectorHint text="When enabled, OpenUSD treats this asset as a sealed prototype to save memory on heavy duplication, but child meshes become read-only proxies. Leave unchecked to allow component styling on sub-meshes (Dress, Kimono, etc.)."/>
                              </span>
                            </td>
                            <td>
                              <input
                                type="checkbox"
                                aria-label="Native instancing"
                                checked={Boolean(node.data.instanced)}
                                onChange={e=>update({instanced: e.target.checked})}
                              />
                            </td>
                          </tr>
                          <tr>
                            <td>Load USD payloads</td>
                            <td>
                              <input
                                type="checkbox"
                                aria-label="Load USD payloads"
                                checked={node.data.loadPayloads !== false}
                                onChange={(e) => update({ loadPayloads: e.target.checked })}
                              />
                            </td>
                          </tr>
                          <tr>
                            <td>
                              <span className="field-label">
                                Mount as payload
                                <InspectorHint text="Attaches this source file using a USD Payload arc instead of a Reference, allowing Load USD payloads to defer loading even if the file has no internal payloads."/>
                              </span>
                            </td>
                            <td>
                              <input
                                type="checkbox"
                                aria-label="Mount as payload"
                                checked={Boolean(node.data.useAsPayload)}
                                onChange={(e) => update({ useAsPayload: e.target.checked })}
                              />
                            </td>
                          </tr>
                          <tr>
                            <td>Up axis</td>
                            <td>
                              <select
                                aria-label="Up axis"
                                value={node.data.axis}
                                onChange={(e) => update({ axis: e.target.value })}
                              >
                                <option value="preserve">Preserve source</option>
                                <option value="Y">+Y</option>
                                <option value="-Y">-Y</option>
                                <option value="Z">+Z</option>
                                <option value="-Z">-Z</option>
                                <option value="X">+X</option>
                                <option value="-X">-X</option>
                              </select>
                            </td>
                          </tr>
                          <tr>
                            <td>Front axis</td>
                            <td>
                              <select
                                aria-label="Front axis"
                                value={node.data.frontAxis || "preserve"}
                                onChange={(e) => update({ frontAxis: e.target.value })}
                              >
                                <option value="preserve">Preserve source</option>
                                <option value="Z">+Z</option>
                                <option value="-Z">-Z</option>
                                <option value="Y">+Y</option>
                                <option value="-Y">-Y</option>
                                <option value="X">+X</option>
                                <option value="-X">-X</option>
                              </select>
                            </td>
                          </tr>
                          <tr>
                            <td>Scale</td>
                            <td>
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
                            </td>
                          </tr>
                        </tbody>
                      </table>
                    </>}
                    {node.data.operation === "application" && <NativeProjectPanel projectName={projectName} onConfigure={()=>{setSettingsTarget(v=>({id:node.data.applicationProfileId || "",visit:v.visit+1}));setActiveTab("applications");}} key={node.id} project={projectModel} nodeId={node.id} data={node.data}
                      onMdBridgePulled={()=>setApplicationPreviews(p=>({...p,[node.id]:"published"}))}
                      onChange={patch => applyProject(editNodeData(projectRef.current, node.id, patch),"Edit node",`field:${node.id}:${Object.keys(patch).join()}`)} onLive={(edited,result)=>{applyProject(recordLiveAdjustment(projectRef.current,node.id,edited,result));setApplicationPreviews(p=>({...p,[node.id]:"published"}));}} onPublished={(edited,result)=>{applyProject(recordPublication(projectRef.current,node.id,edited,result));setApplicationPreviews(p=>({...p,[node.id]:"published"}));}} onBaseline={result=>applyProject(recordManualBaseline(projectRef.current,node.id,result))} onQuickstartCreated={(original,prepared)=>commitQuickstart(node.id,original,prepared)} onNativeCreated={created=>{const next=recordNativeCreation(projectRef.current,node.id,created);applyProject(next);return next;}} onHandoff={created=>{const next=recordHandoff(projectRef.current,node.id,created);applyProject(next);setApplicationPreviews(p=>({...p,[node.id]:"handoff"}));return next;}} onStatus={reportStatus}/>}

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
                    globalScale={node.data.scale}
                    intensityScale={node.data.lightIntensityScale}
                    onScalesChange={(patch) => update(patch)}
                    getViewportCameraTransform={() => viewportCameraTransformRef.current}
                    onStatus={reportStatus}
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

                {node.data.operation === "postfx" && (
                  <PostFxInspector
                    value={node.data.postFx}
                    onChange={(postFx) => update({ postFx })}
                  />
                )}

                {node.data.operation === "camera" && (() => {
                  const defaultCam: ViewportCamera = {
                    yaw: 35,
                    pitch: 20,
                    zoom: 1,
                    panX: 0,
                    panY: 0,
                    projection: "perspective",
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
                              <th style={{ padding: "4px 6px", borderBottom: "1px solid rgba(255,255,255,0.12)" }}>Proj</th>
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
                                <td colSpan={12} style={{ padding: "8px 6px", opacity: 0.7 }}>
                                  No camera views yet. Click "+ New from current view" to add one.
                                </td>
                              </tr>
                            ) : (
                              views.map((row) => {
                                const cam = { ...defaultCam, ...row.camera };
                                const isOrthoCam = cam.projection === "orthographic";
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
                                      <select
                                        value={isOrthoCam ? "orthographic" : "perspective"}
                                        onChange={(e) =>
                                          patchRow(row.id, {
                                            projection: e.target.value === "orthographic" ? "orthographic" : "perspective",
                                          })
                                        }
                                        style={{ ...cellInputStyle, width: 64 }}
                                      >
                                        <option value="perspective">Persp</option>
                                        <option value="orthographic">Ortho</option>
                                      </select>
                                    </td>
                                    <td style={{ padding: "4px 6px", borderBottom: "1px solid rgba(255,255,255,0.06)", opacity: isOrthoCam ? 0.45 : 1 }}>
                                      {numCell(cam.fov ?? 45, 1, (fov) => patchRow(row.id, { fov }), 5, 170)}
                                    </td>
                                    <td style={{ padding: "4px 6px", borderBottom: "1px solid rgba(255,255,255,0.06)", textAlign: "center", opacity: isOrthoCam ? 0.45 : 1 }}>
                                      <input
                                        type="checkbox"
                                        disabled={isOrthoCam}
                                        checked={Boolean(cam.dofEnabled) && !isOrthoCam}
                                        onChange={(e) => patchRow(row.id, { dofEnabled: e.target.checked })}
                                      />
                                    </td>
                                    <td style={{ padding: "4px 6px", borderBottom: "1px solid rgba(255,255,255,0.06)", opacity: isOrthoCam ? 0.45 : 1 }}>
                                      {numCell(cam.focusDistance ?? 5, 0.1, (focusDistance) => patchRow(row.id, { focusDistance }), 0.01)}
                                    </td>
                                    <td style={{ padding: "4px 6px", borderBottom: "1px solid rgba(255,255,255,0.06)", opacity: isOrthoCam ? 0.45 : 1 }}>
                                      {numCell(cam.fStop ?? 5.6, 0.1, (fStop) => patchRow(row.id, { fStop }), 0.1)}
                                    </td>
                                    <td style={{ padding: "4px 6px", borderBottom: "1px solid rgba(255,255,255,0.06)" }}>
                                      {numCell(cam.yaw, 1, (yaw) => patchRow(row.id, { yaw }))}
                                    </td>
                                    <td style={{ padding: "4px 6px", borderBottom: "1px solid rgba(255,255,255,0.06)" }}>
                                      {numCell(cam.pitch, 1, (pitch) => patchRow(row.id, { pitch }), -90, 90)}
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
                  <Suspense fallback={<p className="hint">Loading hair inspector...</p>}>
                    <HairInspector
                      value={node.data.hair}
                      graph={edges.some((e) => e.target === node.id) ? graphPreview(projectModel, node.id) : undefined}
                      onChange={(hair) => update({ hair })}
                    />
                  </Suspense>
                )}

                {node.data.operation === "cache_edit" && (
                  <Suspense fallback={<p className="hint">Loading cache sculptor...</p>}>
                    <CacheEditInspector
                      value={node.data.cacheEdit}
                      project={projectModel}
                      nodeId={node.id}
                      currentFrame={viewportFrame}
                      onJumpToFrame={(f) => setSeekFrameRequest((prev) => ({ frame: f, seq: (prev?.seq ?? 0) + 1 }))}
                      onChange={(cacheEdit) => update({ cacheEdit })}
                    />
                  </Suspense>
                )}

                {node.data.operation === "pose" && (
                  <Suspense fallback={<p>Loading Pose...</p>}>
                    <PoseInspector key={node.id} value={node.data.pose} graph={graphPreview(projectModel,node.id)} dependencies={dependencies}
                      currentFrame={viewportFrame} editing={poseEditing?.nodeId===node.id?poseEditing:null}
                      onEdit={edit=>{setPoseEditing(edit?{...edit,nodeId:node.id}:null);if(edit)pin(node.id);}}
                      onStatus={reportStatus} onChange={(pose,nextDeps) => applyProject({...projectModel, dependencies:nextDeps || dependencies,
                        nodes:projectModel.nodes.map(n => n.id===node.id?{...n,data:{...n.data,pose}}:n)}, "Update Pose",document.activeElement?.tagName==="INPUT"?`pose-field:${node.id}`:null)} />
                  </Suspense>
                )}
                {node.data.operation === "character" && (
                  <Suspense fallback={<p className="hint">Loading character inspector...</p>}>
                    <CharacterInspector
                      value={node.data.character}
                      graph={edges.some((e) => e.target === node.id) ? graphPreview(projectModel, node.id) : null}
                      dependencies={dependencies}
                      onChange={(character, nextDeps) => {
                        if (nextDeps) {
                          applyProject(
                            {
                              ...projectModel,
                              dependencies: nextDeps,
                              nodes: projectModel.nodes.map((n) =>
                                n.id !== node.id ? n : { ...n, data: { ...n.data, character } },
                              ),
                            },
                            "Update Character",
                          );
                        } else {
                          update({ character });
                        }
                      }}
                      onStatus={reportStatus}
                      activeGroomGizmoId={
                        activeGroomGizmo?.nodeId === node.id ? activeGroomGizmo.groomId : null
                      }
                      onToggleGroomGizmo={(groomId) => {
                        setActiveGroomGizmo((cur) => {
                          const turningOn = !(cur?.nodeId === node.id && cur.groomId === groomId);
                          if (turningOn) setSceneMode(true);
                          return turningOn ? { nodeId: node.id, groomId } : null;
                        });
                      }}
                    />
                  </Suspense>
                )}

                {node.data.operation === "metahuman" && (
                  <Suspense fallback={<p>Loading MetaHuman inspector...</p>}>
                    <MetahumanInspector
                      key={node.id}
                      path={node.data.path}
                      bodyPath={node.data.metahumanBodyPath || ""}
                      lod={node.data.metahumanLod ?? 0}
                      materials={node.data.metahumanMaterials !== false}
                      onChange={update}
                      onStatus={reportStatus}
                    />
                  </Suspense>
                )}
                {node.data.operation === "blender" && (
                  <Suspense fallback={<p className="hint">Loading Blender inspector...</p>}>
                    <BlenderInspector
                      project={projectModel}
                      nodeId={node.id}
                      onProjectChange={(p) => applyProject(p, "Update Blender node")}
                      onStatus={reportStatus}
                    />
                  </Suspense>
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
